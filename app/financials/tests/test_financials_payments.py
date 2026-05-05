from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import AccountProfile
from billing.models import Provider
from debts import services as DebtSV
from debts.models import DebtorDebt, CreditorDebt, PartyType, DebtorPayment, CreditorReceipt
from financials import services as FinSV
from financials.models import Currency, MoneyContainer, MoneyContainerCurrency, Receipt, ReceiptKind, ReceiptStatus, Counterparty, CounterpartyType

D = Decimal


class DebtsFinancialsPaymentsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.actor = User.objects.create_user(username="mgr", password="123")

        prof, _ = AccountProfile.objects.get_or_create(user=cls.actor)
        prof.role = AccountProfile.Role.MANAGER
        prof.save(update_fields=["role"])

        cls.syp, _ = Currency.objects.get_or_create(
            code="SYP",
            defaults={"name": "Syrian Pound", "decimals": 0, "is_active": True},
        )
        cls.usd, _ = Currency.objects.get_or_create(
            code="USD",
            defaults={"name": "US Dollar", "decimals": 2, "is_active": True},
        )

        FinSV.set_current_fx(actor=cls.actor, rate_syp_per_usd=D("15000"))

        cls.container = MoneyContainer.objects.create(
            name="Debt Test Container",
            container_type=MoneyContainer.ContainerType.DRAWER,
            is_active=True,
            created_by=cls.actor,
        )
        cls.container.allowed_users.add(cls.actor)
        MoneyContainerCurrency.objects.update_or_create(
            container=cls.container, currency=cls.syp, defaults={"is_enabled": True}
        )
        MoneyContainerCurrency.objects.update_or_create(
            container=cls.container, currency=cls.usd, defaults={"is_enabled": True}
        )

        cls.provider = Provider.objects.create(name="Provider A")

    def setUp(self):
        ok = self.client.login(username="mgr", password="123")
        self.assertTrue(ok)

    def test_pay_debtor_creates_receipt_and_updates_balance(self):
        entry = DebtorDebt.objects.create(
            provider=self.provider,
            source_app="debts",
            source_model="ManualDebt",
            source_id="manual:1",
            total=D("100"),
            paid_amount=D("0"),
            status=DebtorDebt.Status.OPEN,
            party_type=PartyType.PROVIDER,
            party_name=self.provider.name,
            currency_code="SYP",
        )

        DebtSV.pay_debt(
            actor=self.actor,
            entry_id=entry.id,
            amount=D("50"),
            full=False,
            money_container_id=self.container.id,
            currency_code="SYP",
        )

        entry.refresh_from_db()
        self.assertEqual(entry.paid_amount, D("50"))

        pay = DebtorPayment.objects.get(entry=entry)
        self.assertIsNotNone(pay.receipt_id)
        self.assertEqual(pay.money_container_id, self.container.id)

        receipt = Receipt.objects.get(pk=pay.receipt_id)
        self.assertEqual(receipt.kind, ReceiptKind.COUNTERPARTY_SETTLE)
        self.assertEqual(receipt.status, ReceiptStatus.POSTED)

        self.container.refresh_from_db()
        self.assertEqual(self.container.balance_syp, D("-50"))

    def test_collect_creditor_creates_receipt_and_updates_balance(self):
        entry = CreditorDebt.objects.create(
            provider=self.provider,
            source_app="debts",
            source_model="ManualDebt",
            source_id="manual:2",
            total=D("80"),
            collected=D("0"),
            status=CreditorDebt.Status.OPEN,
            party_type=PartyType.PROVIDER,
            party_name=self.provider.name,
            currency_code="SYP",
        )

        DebtSV.collect_debt(
            actor=self.actor,
            entry_id=entry.id,
            amount=D("30"),
            full=False,
            money_container_id=self.container.id,
            currency_code="SYP",
        )

        entry.refresh_from_db()
        self.assertEqual(entry.collected, D("30"))

        receipt_row = CreditorReceipt.objects.get(entry=entry)
        self.assertIsNotNone(receipt_row.receipt_id)
        self.assertEqual(receipt_row.money_container_id, self.container.id)

        receipt = Receipt.objects.get(pk=receipt_row.receipt_id)
        self.assertEqual(receipt.kind, ReceiptKind.COUNTERPARTY_SETTLE)
        self.assertEqual(receipt.status, ReceiptStatus.POSTED)

        self.container.refresh_from_db()
        self.assertEqual(self.container.balance_syp, D("30"))

    def test_manual_debtor_creates_counterparty_adjust_only(self):
        entry = DebtSV.create_manual_debt(
            actor=self.actor,
            direction="debtor",
            party_type=PartyType.PROVIDER,
            provider_id=self.provider.id,
            party_name=self.provider.name,
            amount=D("70"),
            currency_code="SYP",
        )
        self.assertIsInstance(entry, DebtorDebt)

        cp = Counterparty.objects.get(type=CounterpartyType.PROVIDER, provider=self.provider)
        bal = FinSV.counterparty_balance(counterparty_id=cp.id)
        self.assertEqual(bal.get("SYP"), D("-70"))

        self.container.refresh_from_db()
        self.assertEqual(self.container.balance_syp, D("0"))

    def test_manual_creditor_creates_counterparty_adjust_only(self):
        entry = DebtSV.create_manual_debt(
            actor=self.actor,
            direction="creditor",
            party_type=PartyType.PROVIDER,
            provider_id=self.provider.id,
            party_name=self.provider.name,
            amount=D("55"),
            currency_code="USD",
        )
        self.assertIsInstance(entry, CreditorDebt)

        cp = Counterparty.objects.get(type=CounterpartyType.PROVIDER, provider=self.provider)
        bal = FinSV.counterparty_balance(counterparty_id=cp.id)
        self.assertEqual(bal.get("USD"), D("55"))

        self.container.refresh_from_db()
        self.assertEqual(self.container.balance_usd, D("0"))

    def test_usd_debt_payment_is_quantized_and_consistent(self):
        entry = DebtorDebt.objects.create(
            provider=self.provider,
            source_app="debts",
            source_model="ManualDebt",
            source_id="manual:usd-precision",
            total=D("2.50"),
            paid_amount=D("0"),
            status=DebtorDebt.Status.OPEN,
            party_type=PartyType.PROVIDER,
            party_name=self.provider.name,
            currency_code="USD",
        )

        with self.assertRaisesRegex(ValueError, "at most 2 decimal digits"):
            DebtSV.pay_debt(
                actor=self.actor,
                entry_id=entry.id,
                amount=D("1.005"),
                full=False,
                money_container_id=self.container.id,
                currency_code="USD",
            )

        DebtSV.pay_debt(
            actor=self.actor,
            entry_id=entry.id,
            amount=D("1.01"),
            full=False,
            money_container_id=self.container.id,
            currency_code="USD",
        )

        entry.refresh_from_db()
        self.assertEqual(entry.paid_amount, D("1.01"))
        self.assertEqual(entry.remaining, D("1.49"))

        payment = DebtorPayment.objects.get(entry=entry)
        self.assertEqual(payment.amount, D("1.01"))

        self.container.refresh_from_db()
        self.assertEqual(self.container.balance_usd, D("-1.01"))

    def test_reversal_symmetry_preserves_quantized_balances(self):
        cp, _ = Counterparty.objects.get_or_create(
            type=CounterpartyType.PROVIDER,
            provider=self.provider,
            defaults={"name": self.provider.name, "is_active": True},
        )

        FinSV.post_cash_add(
            actor=self.actor,
            container_id=self.container.id,
            currency_code="USD",
            amount=D("5"),
            source_app="tests",
            source_model="Seed",
            source_id="usd-seed",
        )

        before_container = FinSV.container_balance(container_id=self.container.id).get("USD", D("0"))
        before_cp = FinSV.counterparty_balance(counterparty_id=cp.id).get("USD", D("0"))

        with self.assertRaisesRegex(ValueError, "at most 2 decimal digits"):
            FinSV.post_settlement_with_fx(
                actor=self.actor,
                container_id=self.container.id,
                counterparty_id=cp.id,
                currency_code="USD",
                cash_amount_signed=D("-1.005"),
                fx_syp_per_usd=D("15000"),
                source_app="tests",
                source_model="Settlement",
                source_id="usd-precision-reject",
            )
        receipt = FinSV.post_settlement_with_fx(
            actor=self.actor,
            container_id=self.container.id,
            counterparty_id=cp.id,
            currency_code="USD",
            cash_amount_signed=D("-1.01"),
            fx_syp_per_usd=D("15000"),
            source_app="tests",
            source_model="Settlement",
            source_id="usd-precision",
        )

        mid_container = FinSV.container_balance(container_id=self.container.id).get("USD", D("0"))
        mid_cp = FinSV.counterparty_balance(counterparty_id=cp.id).get("USD", D("0"))
        self.assertEqual(mid_container, before_container - D("1.01"))
        self.assertEqual(mid_cp, before_cp + D("1.01"))

        FinSV.reverse_receipt(actor=self.actor, receipt_id=receipt.id, reason_note="precision reversal")

        after_container = FinSV.container_balance(container_id=self.container.id).get("USD", D("0"))
        after_cp = FinSV.counterparty_balance(counterparty_id=cp.id).get("USD", D("0"))
        self.assertEqual(after_container, before_container)
        self.assertEqual(after_cp, before_cp)
