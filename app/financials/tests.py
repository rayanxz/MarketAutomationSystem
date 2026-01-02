from __future__ import annotations

import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from financials.models import (
    Currency,
    MoneyContainer,
    Counterparty,
    CounterpartyType,
    ReceiptStatus,
    ReceiptKind,
    PostingLine,
    PostingTargetType,
)
from financials import services as FSV


D = Decimal


class FinancialsCoreTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.actor = User.objects.create_user(username="fefe", password="123")

        # Currencies
        cls.syp = Currency.objects.create(code="SYP", name="Syrian Pound", decimals=0, is_active=True)
        cls.usd = Currency.objects.create(code="USD", name="US Dollar", decimals=2, is_active=True)

        # Containers
        cls.c1 = MoneyContainer.objects.create(name="container#1", created_by=cls.actor)
        cls.c2 = MoneyContainer.objects.create(name="container#2", created_by=cls.actor)

        # Counterparties
        cls.provider_mark = Counterparty.objects.create(type=CounterpartyType.PROVIDER, name="mark")
        cls.customer_ali = Counterparty.objects.create(type=CounterpartyType.CUSTOMER, name="ali")

    def _bal_container(self, container_id: int) -> dict[str, Decimal]:
        return FSV.container_balance(container_id=container_id)

    def _bal_cp(self, cp_id: int) -> dict[str, Decimal]:
        return FSV.counterparty_balance(counterparty_id=cp_id)

    def test_00_empty_balances(self):
        self.assertEqual(self._bal_container(self.c1.id), {})
        self.assertEqual(self._bal_cp(self.provider_mark.id), {})

    def test_10_cash_add_and_withdraw(self):
        # Add 300,000 SYP to c1
        r_add = FSV.post_cash_add(
            actor=self.actor,
            container_id=self.c1.id,
            currency_code="SYP",
            amount=D("300000"),
            note="seed cash",
            source_app="test",
            source_model="seed",
            source_id="1",
        )
        self.assertEqual(r_add.status, ReceiptStatus.POSTED)
        self.assertEqual(r_add.kind, ReceiptKind.CASH_ADD)

        b = self._bal_container(self.c1.id)
        self.assertEqual(b.get("SYP"), D("300000"))

        # Withdraw 10,000 SYP from c1
        r_wd = FSV.post_cash_withdraw(
            actor=self.actor,
            container_id=self.c1.id,
            currency_code="SYP",
            amount=D("10000"),
            note="withdraw",
        )
        self.assertEqual(r_wd.status, ReceiptStatus.POSTED)
        self.assertEqual(r_wd.kind, ReceiptKind.CASH_WITHDRAW)

        b = self._bal_container(self.c1.id)
        self.assertEqual(b.get("SYP"), D("290000"))

        # Ensure we have container lines only and amounts are signed correctly
        lines = list(PostingLine.objects.filter(receipt=r_wd))
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].target_type, PostingTargetType.CONTAINER)
        self.assertEqual(lines[0].container_id, self.c1.id)
        self.assertEqual(lines[0].currency.code, "SYP")
        self.assertEqual(lines[0].amount, D("-10000"))

    def test_20_transfer_between_containers(self):
        # Seed c1: +100,000 SYP
        FSV.post_cash_add(actor=self.actor, container_id=self.c1.id, currency_code="SYP", amount=D("100000"))

        # Transfer 25,000 SYP c1 -> c2
        r = FSV.post_transfer(
            actor=self.actor,
            from_container_id=self.c1.id,
            to_container_id=self.c2.id,
            currency_code="SYP",
            amount=D("25000"),
            note="move money",
        )
        self.assertEqual(r.status, ReceiptStatus.POSTED)
        self.assertEqual(r.kind, ReceiptKind.CONTAINER_TRANSFER)

        b1 = self._bal_container(self.c1.id)
        b2 = self._bal_container(self.c2.id)
        self.assertEqual(b1.get("SYP"), D("75000"))
        self.assertEqual(b2.get("SYP"), D("25000"))

        lines = list(PostingLine.objects.filter(receipt=r).order_by("id"))
        self.assertEqual(len(lines), 2)

        # One negative for from, one positive for to
        self.assertEqual(lines[0].target_type, PostingTargetType.CONTAINER)
        self.assertEqual(lines[1].target_type, PostingTargetType.CONTAINER)
        self.assertTrue(any(l.container_id == self.c1.id and l.amount == D("-25000") for l in lines))
        self.assertTrue(any(l.container_id == self.c2.id and l.amount == D("25000") for l in lines))

        # meta_json should be valid JSON string (we store it as TextField on SQLite)
        for ln in lines:
            if ln.meta_json:
                meta = json.loads(ln.meta_json)
                self.assertIn("side", meta)

    def test_30_counterparty_adjust_unpaid_purchase(self):
        """
        Unpaid purchase means: store owes provider => counterparty balance negative.
        Convention:
          + => counterparty owes store
          - => store owes counterparty
        """
        r = FSV.post_counterparty_adjust(
            actor=self.actor,
            counterparty_id=self.provider_mark.id,
            currency_code="SYP",
            amount_signed=D("-10000"),
            note="unpaid purchase bill",
            source_app="billing",
            source_model="Bill",
            source_id="99",
        )
        self.assertEqual(r.status, ReceiptStatus.POSTED)
        self.assertEqual(r.kind, ReceiptKind.COUNTERPARTY_INC)

        # Containers untouched
        self.assertEqual(self._bal_container(self.c1.id), {})

        # Provider balance becomes -10,000 SYP
        bcp = self._bal_cp(self.provider_mark.id)
        self.assertEqual(bcp.get("SYP"), D("-10000"))

        # Line correctness
        ln = PostingLine.objects.get(receipt=r)
        self.assertEqual(ln.target_type, PostingTargetType.COUNTERPARTY)
        self.assertEqual(ln.counterparty_id, self.provider_mark.id)
        self.assertEqual(ln.container_id, None)
        self.assertEqual(ln.currency.code, "SYP")
        self.assertEqual(ln.amount, D("-10000"))

    def test_40_settlement_pay_provider(self):
        """
        Start: store owes provider 10,000 (cp=-10,000)
        Pay provider from container: container -10,000 ; counterparty +10,000 => cp moves to 0
        """
        # Seed container with cash
        FSV.post_cash_add(actor=self.actor, container_id=self.c1.id, currency_code="SYP", amount=D("50000"))

        # Create payable
        FSV.post_counterparty_adjust(
            actor=self.actor,
            counterparty_id=self.provider_mark.id,
            currency_code="SYP",
            amount_signed=D("-10000"),
            note="unpaid purchase",
        )

        # Pay provider (cash leaves)
        r = FSV.post_settlement(
            actor=self.actor,
            container_id=self.c1.id,
            counterparty_id=self.provider_mark.id,
            currency_code="SYP",
            cash_amount_signed=D("-10000"),
            note="pay provider",
        )
        self.assertEqual(r.status, ReceiptStatus.POSTED)
        self.assertEqual(r.kind, ReceiptKind.COUNTERPARTY_SETTLE)

        bc = self._bal_container(self.c1.id)
        bcp = self._bal_cp(self.provider_mark.id)

        self.assertEqual(bc.get("SYP"), D("40000"))
        self.assertEqual(bcp.get("SYP"), D("0"))

        lines = list(PostingLine.objects.filter(receipt=r))
        self.assertEqual(len(lines), 2)
        self.assertTrue(any(l.target_type == PostingTargetType.CONTAINER and l.amount == D("-10000") for l in lines))
        self.assertTrue(any(l.target_type == PostingTargetType.COUNTERPARTY and l.amount == D("10000") for l in lines))

    def test_50_settlement_collect_from_customer(self):
        """
        Customer owes store: cp +12,000 (receivable)
        Customer pays into container: container +12,000 ; counterparty -12,000 => cp to 0
        """
        # Create receivable
        FSV.post_counterparty_adjust(
            actor=self.actor,
            counterparty_id=self.customer_ali.id,
            currency_code="SYP",
            amount_signed=D("12000"),
            note="sale on credit",
        )
        self.assertEqual(self._bal_cp(self.customer_ali.id).get("SYP"), D("12000"))

        # Collect cash
        r = FSV.post_settlement(
            actor=self.actor,
            container_id=self.c2.id,
            counterparty_id=self.customer_ali.id,
            currency_code="SYP",
            cash_amount_signed=D("12000"),
            note="customer paid",
        )
        self.assertEqual(r.status, ReceiptStatus.POSTED)

        bc2 = self._bal_container(self.c2.id)
        bcp = self._bal_cp(self.customer_ali.id)
        self.assertEqual(bc2.get("SYP"), D("12000"))
        self.assertEqual(bcp.get("SYP"), D("0"))

    def test_60_negative_container_allowed(self):
        """
        Containers may go negative.
        Withdraw without seed => negative.
        """
        r = FSV.post_cash_withdraw(
            actor=self.actor,
            container_id=self.c1.id,
            currency_code="USD",
            amount=D("5"),
            note="allow negative",
        )
        self.assertEqual(r.status, ReceiptStatus.POSTED)
        b = self._bal_container(self.c1.id)
        # USD decimals=2, withdraw 5 => -5.00
        self.assertEqual(b.get("USD"), D("-5.00"))

    def test_70_quantization_by_currency(self):
        """
        SYP decimals=0 -> rounded
        USD decimals=2 -> rounded
        """
        # SYP: 10.6 -> 11 (ROUND_HALF_UP)
        FSV.post_cash_add(actor=self.actor, container_id=self.c1.id, currency_code="SYP", amount=D("10.6"))
        self.assertEqual(self._bal_container(self.c1.id).get("SYP"), D("11"))

        # USD: 1.235 -> 1.24
        FSV.post_cash_add(actor=self.actor, container_id=self.c1.id, currency_code="USD", amount=D("1.235"))
        self.assertEqual(self._bal_container(self.c1.id).get("USD"), D("1.24"))

    def test_80_reversal_creates_negating_lines_and_marks_original(self):
        # Seed and do a transfer
        FSV.post_cash_add(actor=self.actor, container_id=self.c1.id, currency_code="SYP", amount=D("1000"))
        r = FSV.post_transfer(actor=self.actor, from_container_id=self.c1.id, to_container_id=self.c2.id, currency_code="SYP", amount=D("200"))

        b1 = self._bal_container(self.c1.id)["SYP"]
        b2 = self._bal_container(self.c2.id)["SYP"]
        self.assertEqual(b1, D("800"))
        self.assertEqual(b2, D("200"))

        # Reverse transfer
        rev = FSV.reverse_receipt(actor=self.actor, receipt_id=r.id, reason_note="mistake")
        self.assertEqual(rev.kind, ReceiptKind.REVERSAL)
        self.assertEqual(rev.status, ReceiptStatus.POSTED)

        # Original should be marked reversed
        r.refresh_from_db()
        self.assertEqual(r.status, ReceiptStatus.REVERSED)

        # Balances should return to before transfer
        b1 = self._bal_container(self.c1.id)["SYP"]
        b2 = self._bal_container(self.c2.id)["SYP"]
        self.assertEqual(b1, D("1000"))
        self.assertEqual(b2, D("0"))

        # Reversal lines should negate original lines
        orig_lines = list(PostingLine.objects.filter(receipt=r).order_by("id"))
        rev_lines = list(PostingLine.objects.filter(receipt=rev).order_by("id"))
        self.assertEqual(len(orig_lines), len(rev_lines))
        for o, x in zip(orig_lines, rev_lines):
            self.assertEqual(o.target_type, x.target_type)
            self.assertEqual(o.currency_id, x.currency_id)
            self.assertEqual(o.container_id, x.container_id)
            self.assertEqual(o.counterparty_id, x.counterparty_id)
            self.assertEqual(o.amount, -x.amount)
        
        # Double reversal should fail
        with self.assertRaises(ValueError):
            FSV.reverse_receipt(actor=self.actor, receipt_id=r.id, reason_note="again")

    def test_90_invalid_inputs(self):
        # cannot add 0
        with self.assertRaises(ValueError):
            FSV.post_cash_add(actor=self.actor, container_id=self.c1.id, currency_code="SYP", amount=D("0"))

        # cannot transfer to same container
        with self.assertRaises(ValueError):
            FSV.post_transfer(actor=self.actor, from_container_id=self.c1.id, to_container_id=self.c1.id, currency_code="SYP", amount=D("1"))

        # settlement cannot be 0
        with self.assertRaises(ValueError):
            FSV.post_settlement(actor=self.actor, container_id=self.c1.id, counterparty_id=self.provider_mark.id, currency_code="SYP", cash_amount_signed=D("0"))

        # counterparty adjust cannot be 0
        with self.assertRaises(ValueError):
            FSV.post_counterparty_adjust(actor=self.actor, counterparty_id=self.provider_mark.id, currency_code="SYP", amount_signed=D("0"))
