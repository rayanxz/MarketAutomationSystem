from __future__ import annotations

import json
from decimal import Decimal
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase

from financials.models import (
    Currency,
    MoneyContainer,
    MoneyContainerCurrency,
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

        cls.syp, _ = Currency.objects.get_or_create(
            code="SYP",
            defaults={"name": "Syrian Pound", "decimals": 0, "is_active": True},
        )
        cls.usd, _ = Currency.objects.get_or_create(
            code="USD",
            defaults={"name": "US Dollar", "decimals": 2, "is_active": True},
        )

        FSV.set_current_fx(actor=cls.actor, rate_syp_per_usd=Decimal("15000"))

        cls.provider_mark = Counterparty.objects.create(
            type=CounterpartyType.PROVIDER, name="mark"
        )
        cls.customer_ali = Counterparty.objects.create(
            type=CounterpartyType.CUSTOMER, name="ali"
        )

    def setUp(self):
        # Create fresh containers per test to avoid any weird state / name collisions
        self.c1 = MoneyContainer.objects.create(
            name=f"T_core_c1_{uuid4().hex[:8]}",
            created_by=self.actor,
        )
        self.c2 = MoneyContainer.objects.create(
            name=f"T_core_c2_{uuid4().hex[:8]}",
            created_by=self.actor,
        )

        # Ensure currency-state rows exist
        FSV.ensure_currency_states(container=self.c1)
        FSV.ensure_currency_states(container=self.c2)

        # Enable SYP + USD for both
        MoneyContainerCurrency.objects.filter(
            container=self.c1, currency__code__in=["SYP", "USD"]
        ).update(is_enabled=True)
        MoneyContainerCurrency.objects.filter(
            container=self.c2, currency__code__in=["SYP", "USD"]
        ).update(is_enabled=True)

    def _bal_container(self, container_id: int) -> dict[str, Decimal]:
        return FSV.container_balance(container_id=container_id)

    def _bal_cp(self, cp_id: int) -> dict[str, Decimal]:
        return FSV.counterparty_balance(counterparty_id=cp_id)

    def test_00_empty_balances(self):
        self.assertEqual(self._bal_container(self.c1.id), {})
        self.assertEqual(self._bal_cp(self.provider_mark.id), {})

    def test_10_cash_add_and_withdraw(self):
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

        lines = list(PostingLine.objects.filter(receipt=r_wd))
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].target_type, PostingTargetType.CONTAINER)
        self.assertEqual(lines[0].container_id, self.c1.id)
        self.assertEqual(lines[0].currency.code, "SYP")
        self.assertEqual(lines[0].amount, D("-10000"))

    def test_20_transfer_between_containers(self):
        FSV.post_cash_add(
            actor=self.actor,
            container_id=self.c1.id,
            currency_code="SYP",
            amount=D("100000"),
        )

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

        self.assertTrue(any(l.container_id == self.c1.id and l.amount == D("-25000") for l in lines))
        self.assertTrue(any(l.container_id == self.c2.id and l.amount == D("25000") for l in lines))

        for ln in lines:
            if ln.meta_json:
                meta = json.loads(ln.meta_json)
                self.assertIn("side", meta)

    def test_30_counterparty_adjust_unpaid_purchase(self):
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

        self.assertEqual(self._bal_container(self.c1.id), {})

        bcp = self._bal_cp(self.provider_mark.id)
        self.assertEqual(bcp.get("SYP"), D("-10000"))

        ln = PostingLine.objects.get(receipt=r)
        self.assertEqual(ln.target_type, PostingTargetType.COUNTERPARTY)
        self.assertEqual(ln.counterparty_id, self.provider_mark.id)
        self.assertEqual(ln.container_id, None)
        self.assertEqual(ln.currency.code, "SYP")
        self.assertEqual(ln.amount, D("-10000"))

    def test_40_settlement_pay_provider(self):
        FSV.post_cash_add(
            actor=self.actor,
            container_id=self.c1.id,
            currency_code="SYP",
            amount=D("50000"),
        )

        FSV.post_counterparty_adjust(
            actor=self.actor,
            counterparty_id=self.provider_mark.id,
            currency_code="SYP",
            amount_signed=D("-10000"),
            note="unpaid purchase",
        )

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
        FSV.post_counterparty_adjust(
            actor=self.actor,
            counterparty_id=self.customer_ali.id,
            currency_code="SYP",
            amount_signed=D("12000"),
            note="sale on credit",
        )
        self.assertEqual(self._bal_cp(self.customer_ali.id).get("SYP"), D("12000"))

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
        r = FSV.post_cash_withdraw(
            actor=self.actor,
            container_id=self.c1.id,
            currency_code="USD",
            amount=D("5"),
            note="allow negative",
        )
        self.assertEqual(r.status, ReceiptStatus.POSTED)
        b = self._bal_container(self.c1.id)
        self.assertEqual(b.get("USD"), D("-5.00"))

    def test_70_quantization_by_currency(self):
        FSV.post_cash_add(
            actor=self.actor,
            container_id=self.c1.id,
            currency_code="SYP",
            amount=D("10.6"),
        )
        self.assertEqual(self._bal_container(self.c1.id).get("SYP"), D("11"))

        FSV.post_cash_add(
            actor=self.actor,
            container_id=self.c1.id,
            currency_code="USD",
            amount=D("1.235"),
        )
        self.assertEqual(self._bal_container(self.c1.id).get("USD"), D("1.24"))

    def test_80_reversal_creates_negating_lines_and_marks_original(self):
        # Start baseline
        FSV.post_cash_add(
            actor=self.actor,
            container_id=self.c1.id,
            currency_code="SYP",
            amount=D("1000"),
        )
        start_b1 = self._bal_container(self.c1.id).get("SYP", D("0"))
        start_b2 = self._bal_container(self.c2.id).get("SYP", D("0"))
        self.assertEqual(start_b1, D("1000"))
        self.assertEqual(start_b2, D("0"))

        # Transfer 200
        r = FSV.post_transfer(
            actor=self.actor,
            from_container_id=self.c1.id,
            to_container_id=self.c2.id,
            currency_code="SYP",
            amount=D("200"),
        )
        mid_b1 = self._bal_container(self.c1.id).get("SYP", D("0"))
        mid_b2 = self._bal_container(self.c2.id).get("SYP", D("0"))
        self.assertEqual(mid_b1, D("800"))
        self.assertEqual(mid_b2, D("200"))

        # Reverse
        rev = FSV.reverse_receipt(actor=self.actor, receipt_id=r.id, reason_note="mistake")
        self.assertEqual(rev.kind, ReceiptKind.REVERSAL)
        self.assertEqual(rev.status, ReceiptStatus.POSTED)

        r.refresh_from_db()
        self.assertEqual(r.status, ReceiptStatus.REVERSED)

        end_b1 = self._bal_container(self.c1.id).get("SYP", D("0"))
        end_b2 = self._bal_container(self.c2.id).get("SYP", D("0"))
        self.assertEqual(end_b1, start_b1)
        self.assertEqual(end_b2, start_b2)

        # Reversal lines negate original lines (match by identity tuple, not order)
        orig_lines = list(PostingLine.objects.filter(receipt=r))
        rev_lines = list(PostingLine.objects.filter(receipt=rev))

        def k(ln: PostingLine):
            return (ln.target_type, ln.container_id, ln.counterparty_id, ln.currency_id)

        orig_map = {k(ln): ln.amount for ln in orig_lines}
        rev_map = {k(ln): ln.amount for ln in rev_lines}

        self.assertEqual(set(orig_map.keys()), set(rev_map.keys()))
        for key in orig_map.keys():
            self.assertEqual(orig_map[key], -rev_map[key])

        with self.assertRaises(ValueError):
            FSV.reverse_receipt(actor=self.actor, receipt_id=r.id, reason_note="again")

    def test_90_invalid_inputs(self):
        with self.assertRaises(ValueError):
            FSV.post_cash_add(
                actor=self.actor,
                container_id=self.c1.id,
                currency_code="SYP",
                amount=D("0"),
            )

        with self.assertRaises(ValueError):
            FSV.post_transfer(
                actor=self.actor,
                from_container_id=self.c1.id,
                to_container_id=self.c1.id,
                currency_code="SYP",
                amount=D("1"),
            )

        with self.assertRaises(ValueError):
            FSV.post_settlement(
                actor=self.actor,
                container_id=self.c1.id,
                counterparty_id=self.provider_mark.id,
                currency_code="SYP",
                cash_amount_signed=D("0"),
            )

        with self.assertRaises(ValueError):
            FSV.post_counterparty_adjust(
                actor=self.actor,
                counterparty_id=self.provider_mark.id,
                currency_code="SYP",
                amount_signed=D("0"),
            )
