from __future__ import annotations

from decimal import Decimal

from django.test import TestCase

from billing.models import Bill, Provider
from debts.models import (
    CreditorDebt,
    CreditorReceipt,
    DebtCauseType,
    DebtDirection,
    DebtRecord,
    DebtSettlement,
    DebtStatus,
    DebtorDebt,
    DebtorPayment,
    OtherPartyType,
    PartyType,
)
from financials.models import Receipt


class ProviderNetBalanceContractPhase1BTests(TestCase):
    """
    Phase 1B contract tests for a future read-only provider position projection.

    Expected future implementation entrypoint:
        debts.provider_position.get_provider_net_position(provider_id: int) -> dict

    Expected return shape:
        {
            "provider_id": int,
            "currencies": {
                "SYP": {
                    "receivable": Decimal,
                    "payable": Decimal,
                    "net": Decimal,
                    "open_receivable_count": int,
                    "open_payable_count": int,
                },
                "USD": { ... same keys ... },
            },
        }
    """

    def setUp(self):
        self.provider = Provider.objects.create(name=f"Provider Net Contract {self._testMethodName}")
        self._cause_seq = 0
        self.actor_username = "phase1b_contract"

    def _next_cause_id(self, prefix: str) -> str:
        self._cause_seq += 1
        return f"{prefix}-{self._cause_seq}"

    def _project(self, provider_id: int) -> dict:
        try:
            from debts.provider_position import get_provider_net_position
        except Exception as exc:
            self.fail(
                "Phase 1B contract target missing: expected "
                "`debts.provider_position.get_provider_net_position(provider_id)` "
                f"to exist in Phase 2. Import error: {exc!r}"
            )
        return get_provider_net_position(provider_id=provider_id)

    def _create_central_debt(
        self,
        *,
        direction: str,
        cause_type: str,
        cause_id: str,
        total_syp: str = "0.00",
        total_usd: str = "0.00",
        remaining_syp: str = "0.00",
        remaining_usd: str = "0.00",
        status: str = DebtStatus.OPEN,
    ) -> DebtRecord:
        return DebtRecord.objects.create(
            direction=direction,
            cause_type=cause_type,
            cause_id=cause_id,
            source_app="billing",
            other_party_type=OtherPartyType.PROVIDER,
            other_party_id=str(self.provider.id),
            provider=self.provider,
            actor_username=self.actor_username,
            total_syp=Decimal(total_syp),
            total_usd=Decimal(total_usd),
            remaining_syp=Decimal(remaining_syp),
            remaining_usd=Decimal(remaining_usd),
            status=status,
        )

    def _create_legacy_debtor(
        self,
        *,
        source_model: str,
        source_id: str,
        currency_code: str,
        total: str,
        paid_amount: str,
        status: str = DebtorDebt.Status.OPEN,
    ) -> DebtorDebt:
        return DebtorDebt.objects.create(
            provider=self.provider,
            source_app="billing",
            source_model=source_model,
            source_id=source_id,
            total=Decimal(total),
            paid_amount=Decimal(paid_amount),
            status=status,
            party_type=PartyType.PROVIDER,
            party_name=self.provider.name,
            currency_code=currency_code,
        )

    def _create_legacy_creditor(
        self,
        *,
        source_model: str,
        source_id: str,
        currency_code: str,
        total: str,
        collected: str,
        status: str = CreditorDebt.Status.OPEN,
    ) -> CreditorDebt:
        return CreditorDebt.objects.create(
            provider=self.provider,
            source_app="billing",
            source_model=source_model,
            source_id=source_id,
            total=Decimal(total),
            collected=Decimal(collected),
            status=status,
            party_type=PartyType.PROVIDER,
            party_name=self.provider.name,
            currency_code=currency_code,
        )

    def _assert_contract_shape(self, payload: dict) -> None:
        self.assertIsInstance(payload, dict)
        self.assertEqual(payload.get("provider_id"), self.provider.id)
        self.assertIn("currencies", payload)
        self.assertIsInstance(payload["currencies"], dict)
        for code in ("SYP", "USD"):
            self.assertIn(code, payload["currencies"])
            bucket = payload["currencies"][code]
            self.assertIsInstance(bucket, dict)
            for key in (
                "receivable",
                "payable",
                "net",
                "open_receivable_count",
                "open_payable_count",
            ):
                self.assertIn(key, bucket)
            self.assertIsInstance(bucket["receivable"], Decimal)
            self.assertIsInstance(bucket["payable"], Decimal)
            self.assertIsInstance(bucket["net"], Decimal)
            self.assertIsInstance(bucket["open_receivable_count"], int)
            self.assertIsInstance(bucket["open_payable_count"], int)

    def _assert_bucket(
        self,
        payload: dict,
        *,
        currency: str,
        receivable: str,
        payable: str,
        net: str,
        open_receivable_count: int,
        open_payable_count: int,
    ) -> None:
        self._assert_contract_shape(payload)
        bucket = payload["currencies"][currency]
        self.assertEqual(bucket["receivable"], Decimal(receivable))
        self.assertEqual(bucket["payable"], Decimal(payable))
        self.assertEqual(bucket["net"], Decimal(net))
        self.assertEqual(bucket["open_receivable_count"], open_receivable_count)
        self.assertEqual(bucket["open_payable_count"], open_payable_count)

    def test_case_01_pure_payable_net_is_negative(self):
        self._create_central_debt(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id=self._next_cause_id("pb"),
            total_syp="20000.00",
            remaining_syp="20000.00",
        )
        payload = self._project(provider_id=self.provider.id)
        self._assert_bucket(
            payload,
            currency="SYP",
            receivable="0.00",
            payable="20000.00",
            net="-20000.00",
            open_receivable_count=0,
            open_payable_count=1,
        )

    def test_case_02_pure_receivable_net_is_positive(self):
        self._create_central_debt(
            direction=DebtDirection.RECEIVABLE,
            cause_type=DebtCauseType.MANUAL,
            cause_id=self._next_cause_id("manual"),
            total_syp="15000.00",
            remaining_syp="15000.00",
        )
        payload = self._project(provider_id=self.provider.id)
        self._assert_bucket(
            payload,
            currency="SYP",
            receivable="15000.00",
            payable="0.00",
            net="15000.00",
            open_receivable_count=1,
            open_payable_count=0,
        )

    def test_case_03_mixed_directions_net_is_receivable_minus_payable(self):
        self._create_central_debt(
            direction=DebtDirection.RECEIVABLE,
            cause_type=DebtCauseType.MANUAL,
            cause_id=self._next_cause_id("manual"),
            total_syp="10000.00",
            remaining_syp="10000.00",
        )
        self._create_central_debt(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id=self._next_cause_id("pb"),
            total_syp="20000.00",
            remaining_syp="20000.00",
        )
        payload = self._project(provider_id=self.provider.id)
        self._assert_bucket(
            payload,
            currency="SYP",
            receivable="10000.00",
            payable="20000.00",
            net="-10000.00",
            open_receivable_count=1,
            open_payable_count=1,
        )

    def test_case_04_zero_net_allowed_while_open_debts_exist(self):
        self._create_central_debt(
            direction=DebtDirection.RECEIVABLE,
            cause_type=DebtCauseType.MANUAL,
            cause_id=self._next_cause_id("manual"),
            total_syp="10000.00",
            remaining_syp="10000.00",
        )
        self._create_central_debt(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id=self._next_cause_id("pb"),
            total_syp="10000.00",
            remaining_syp="10000.00",
        )
        payload = self._project(provider_id=self.provider.id)
        self._assert_bucket(
            payload,
            currency="SYP",
            receivable="10000.00",
            payable="10000.00",
            net="0.00",
            open_receivable_count=1,
            open_payable_count=1,
        )

    def test_case_05_partial_settlement_contributes_remaining_only(self):
        self._create_central_debt(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id=self._next_cause_id("pb"),
            total_syp="20000.00",
            remaining_syp="15000.00",
            status=DebtStatus.OPEN,
        )
        payload = self._project(provider_id=self.provider.id)
        self._assert_bucket(
            payload,
            currency="SYP",
            receivable="0.00",
            payable="15000.00",
            net="-15000.00",
            open_receivable_count=0,
            open_payable_count=1,
        )

    def test_case_06_closed_debts_are_excluded(self):
        self._create_central_debt(
            direction=DebtDirection.RECEIVABLE,
            cause_type=DebtCauseType.MANUAL,
            cause_id=self._next_cause_id("manual"),
            total_syp="9000.00",
            remaining_syp="0.00",
            status=DebtStatus.CLOSED,
        )
        self._create_central_debt(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id=self._next_cause_id("pb"),
            total_syp="1000.00",
            remaining_syp="1000.00",
            status=DebtStatus.OPEN,
        )
        payload = self._project(provider_id=self.provider.id)
        self._assert_bucket(
            payload,
            currency="SYP",
            receivable="0.00",
            payable="1000.00",
            net="-1000.00",
            open_receivable_count=0,
            open_payable_count=1,
        )

    def test_case_07_central_preferred_over_legacy_duplicate(self):
        bill = Bill.objects.create(serial=91001, provider=self.provider)
        self._create_central_debt(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id=(bill.public_id or str(bill.id)),
            total_syp="4000.00",
            remaining_syp="4000.00",
            status=DebtStatus.OPEN,
        )
        self._create_legacy_debtor(
            source_model="Bill",
            source_id=str(bill.id),
            currency_code="SYP",
            total="4000.00",
            paid_amount="0.00",
            status=DebtorDebt.Status.OPEN,
        )
        payload = self._project(provider_id=self.provider.id)
        self._assert_bucket(
            payload,
            currency="SYP",
            receivable="0.00",
            payable="4000.00",
            net="-4000.00",
            open_receivable_count=0,
            open_payable_count=1,
        )

    def test_case_08_legacy_only_obligation_is_included(self):
        self._create_legacy_creditor(
            source_model="ProviderReturn",
            source_id="legacy-ret-801",
            currency_code="SYP",
            total="3000.00",
            collected="1200.00",
            status=CreditorDebt.Status.OPEN,
        )
        payload = self._project(provider_id=self.provider.id)
        self._assert_bucket(
            payload,
            currency="SYP",
            receivable="1800.00",
            payable="0.00",
            net="1800.00",
            open_receivable_count=1,
            open_payable_count=0,
        )

    def test_case_09_currency_isolation_no_forced_conversion(self):
        self._create_central_debt(
            direction=DebtDirection.RECEIVABLE,
            cause_type=DebtCauseType.MANUAL,
            cause_id=self._next_cause_id("manual"),
            total_usd="100.00",
            remaining_usd="100.00",
        )
        self._create_central_debt(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id=self._next_cause_id("pb"),
            total_syp="200000.00",
            remaining_syp="200000.00",
        )
        payload = self._project(provider_id=self.provider.id)
        self._assert_bucket(
            payload,
            currency="USD",
            receivable="100.00",
            payable="0.00",
            net="100.00",
            open_receivable_count=1,
            open_payable_count=0,
        )
        self._assert_bucket(
            payload,
            currency="SYP",
            receivable="0.00",
            payable="200000.00",
            net="-200000.00",
            open_receivable_count=0,
            open_payable_count=1,
        )

    def test_case_10_projection_read_has_no_auto_cancel_or_mutation_side_effects(self):
        central_payable = self._create_central_debt(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id=self._next_cause_id("pb"),
            total_syp="9000.00",
            remaining_syp="9000.00",
        )
        central_receivable = self._create_central_debt(
            direction=DebtDirection.RECEIVABLE,
            cause_type=DebtCauseType.MANUAL,
            cause_id=self._next_cause_id("manual"),
            total_syp="3000.00",
            remaining_syp="3000.00",
        )
        legacy_debtor = self._create_legacy_debtor(
            source_model="Bill",
            source_id="mut-legacy-1",
            currency_code="SYP",
            total="2000.00",
            paid_amount="500.00",
            status=DebtorDebt.Status.OPEN,
        )
        legacy_creditor = self._create_legacy_creditor(
            source_model="ProviderReturn",
            source_id="mut-legacy-2",
            currency_code="SYP",
            total="1000.00",
            collected="100.00",
            status=CreditorDebt.Status.OPEN,
        )

        before_central = list(
            DebtRecord.objects.filter(provider=self.provider)
            .order_by("id")
            .values("id", "status", "remaining_syp", "remaining_usd")
        )
        before_legacy_debtor = list(
            DebtorDebt.objects.filter(provider=self.provider)
            .order_by("id")
            .values("id", "status", "paid_amount")
        )
        before_legacy_creditor = list(
            CreditorDebt.objects.filter(provider=self.provider)
            .order_by("id")
            .values("id", "status", "collected")
        )
        before_settlement_count = DebtSettlement.objects.count()
        before_receipt_count = Receipt.objects.count()
        before_debtor_payment_count = DebtorPayment.objects.count()
        before_creditor_receipt_count = CreditorReceipt.objects.count()

        payload = self._project(provider_id=self.provider.id)
        self._assert_contract_shape(payload)

        central_payable.refresh_from_db()
        central_receivable.refresh_from_db()
        legacy_debtor.refresh_from_db()
        legacy_creditor.refresh_from_db()

        after_central = list(
            DebtRecord.objects.filter(provider=self.provider)
            .order_by("id")
            .values("id", "status", "remaining_syp", "remaining_usd")
        )
        after_legacy_debtor = list(
            DebtorDebt.objects.filter(provider=self.provider)
            .order_by("id")
            .values("id", "status", "paid_amount")
        )
        after_legacy_creditor = list(
            CreditorDebt.objects.filter(provider=self.provider)
            .order_by("id")
            .values("id", "status", "collected")
        )
        after_settlement_count = DebtSettlement.objects.count()
        after_receipt_count = Receipt.objects.count()
        after_debtor_payment_count = DebtorPayment.objects.count()
        after_creditor_receipt_count = CreditorReceipt.objects.count()

        self.assertEqual(before_central, after_central)
        self.assertEqual(before_legacy_debtor, after_legacy_debtor)
        self.assertEqual(before_legacy_creditor, after_legacy_creditor)
        self.assertEqual(before_settlement_count, after_settlement_count)
        self.assertEqual(before_receipt_count, after_receipt_count)
        self.assertEqual(before_debtor_payment_count, after_debtor_payment_count)
        self.assertEqual(before_creditor_receipt_count, after_creditor_receipt_count)
