from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

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
from debts.provider_account_allocator import simulate_provider_account_allocation
from financials.models import Receipt


class ProviderAccountAllocatorPhase4ATests(TestCase):
    def setUp(self):
        self.provider = Provider.objects.create(name=f"Phase4A Provider {self._testMethodName}")
        self._serial_seq = 980000
        self._now = timezone.now()

    def _next_serial(self) -> int:
        self._serial_seq += 1
        return self._serial_seq

    def _create_bill(self) -> Bill:
        return Bill.objects.create(serial=self._next_serial(), provider=self.provider)

    def _create_central(
        self,
        *,
        direction: str,
        cause_type: str,
        cause_id: str,
        remaining_syp: str = "0.00",
        remaining_usd: str = "0.00",
        created_offset_days: int = 0,
    ) -> DebtRecord:
        row = DebtRecord.objects.create(
            direction=direction,
            cause_type=cause_type,
            cause_id=cause_id,
            source_app="billing",
            other_party_type=OtherPartyType.PROVIDER,
            other_party_id=str(self.provider.id),
            provider=self.provider,
            actor_username="phase4a",
            total_syp=Decimal(remaining_syp),
            total_usd=Decimal(remaining_usd),
            remaining_syp=Decimal(remaining_syp),
            remaining_usd=Decimal(remaining_usd),
            status=DebtStatus.OPEN,
        )
        DebtRecord.objects.filter(id=row.id).update(
            created_at=self._now + timedelta(days=created_offset_days)
        )
        row.refresh_from_db()
        return row

    def _create_legacy_debtor(
        self,
        *,
        source_app: str,
        source_model: str,
        source_id: str,
        currency_code: str,
        total: str,
        paid_amount: str = "0.00",
        legacy_source_id: str = "",
        created_offset_days: int = 0,
    ) -> DebtorDebt:
        row = DebtorDebt.objects.create(
            provider=self.provider,
            source_app=source_app,
            source_model=source_model,
            source_id=source_id,
            legacy_source_id=legacy_source_id,
            total=Decimal(total),
            paid_amount=Decimal(paid_amount),
            status=DebtorDebt.Status.OPEN,
            party_type=PartyType.PROVIDER,
            party_name=self.provider.name,
            currency_code=currency_code,
        )
        DebtorDebt.objects.filter(id=row.id).update(
            created_at=self._now + timedelta(days=created_offset_days)
        )
        row.refresh_from_db()
        return row

    def _create_legacy_creditor(
        self,
        *,
        source_app: str,
        source_model: str,
        source_id: str,
        currency_code: str,
        total: str,
        collected: str = "0.00",
        legacy_source_id: str = "",
        created_offset_days: int = 0,
    ) -> CreditorDebt:
        row = CreditorDebt.objects.create(
            provider=self.provider,
            source_app=source_app,
            source_model=source_model,
            source_id=source_id,
            legacy_source_id=legacy_source_id,
            total=Decimal(total),
            collected=Decimal(collected),
            status=CreditorDebt.Status.OPEN,
            party_type=PartyType.PROVIDER,
            party_name=self.provider.name,
            currency_code=currency_code,
        )
        CreditorDebt.objects.filter(id=row.id).update(
            created_at=self._now + timedelta(days=created_offset_days)
        )
        row.refresh_from_db()
        return row

    def _simulate(self, *, action: str, currency: str, amount) -> dict:
        return simulate_provider_account_allocation(
            provider_id=self.provider.id,
            action=action,
            currency=currency,
            amount=amount,
        )

    def test_example_allocation_oldest_first_full_then_partial(self):
        d1 = self._create_central(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id="ex1",
            remaining_syp="2000.00",
            created_offset_days=-4,
        )
        d2 = self._create_central(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id="ex2",
            remaining_syp="3000.00",
            created_offset_days=-3,
        )
        d3 = self._create_central(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id="ex3",
            remaining_syp="10000.00",
            created_offset_days=-2,
        )
        self._create_central(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id="ex4",
            remaining_syp="5000.00",
            created_offset_days=-1,
        )

        result = self._simulate(action="pay_provider", currency="SYP", amount="10000.00")

        self.assertEqual(result["requested_amount"], Decimal("10000.00"))
        self.assertEqual(result["total_applied"], Decimal("10000.00"))
        self.assertEqual(result["unallocated_amount"], Decimal("0.00"))
        self.assertEqual(len(result["allocations"]), 3)

        a1, a2, a3 = result["allocations"]
        self.assertEqual(a1["debt_id"], d1.id)
        self.assertEqual(a1["applied"], Decimal("2000.00"))
        self.assertEqual(a1["would_close"], True)

        self.assertEqual(a2["debt_id"], d2.id)
        self.assertEqual(a2["applied"], Decimal("3000.00"))
        self.assertEqual(a2["would_close"], True)

        self.assertEqual(a3["debt_id"], d3.id)
        self.assertEqual(a3["applied"], Decimal("5000.00"))
        self.assertEqual(a3["after_remaining"], Decimal("5000.00"))
        self.assertEqual(a3["would_close"], False)

    def test_full_coverage_amount_exactly_matches_total(self):
        self._create_central(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id="full-1",
            remaining_syp="2000.00",
            created_offset_days=-2,
        )
        self._create_central(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id="full-2",
            remaining_syp="3000.00",
            created_offset_days=-1,
        )
        result = self._simulate(action="pay_provider", currency="SYP", amount="5000.00")
        self.assertEqual(result["total_applied"], Decimal("5000.00"))
        self.assertEqual(result["unallocated_amount"], Decimal("0.00"))
        self.assertTrue(all(row["would_close"] for row in result["allocations"]))

    def test_partial_first_debt(self):
        d1 = self._create_central(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id="partial-1",
            remaining_syp="2000.00",
            created_offset_days=-2,
        )
        self._create_central(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id="partial-2",
            remaining_syp="3000.00",
            created_offset_days=-1,
        )
        result = self._simulate(action="pay_provider", currency="SYP", amount="500.00")
        self.assertEqual(len(result["allocations"]), 1)
        row = result["allocations"][0]
        self.assertEqual(row["debt_id"], d1.id)
        self.assertEqual(row["before_remaining"], Decimal("2000.00"))
        self.assertEqual(row["applied"], Decimal("500.00"))
        self.assertEqual(row["after_remaining"], Decimal("1500.00"))
        self.assertFalse(row["would_close"])

    def test_over_amount_applies_all_and_returns_unallocated(self):
        self._create_central(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id="over-1",
            remaining_syp="2000.00",
            created_offset_days=-2,
        )
        self._create_central(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id="over-2",
            remaining_syp="3000.00",
            created_offset_days=-1,
        )
        result = self._simulate(action="pay_provider", currency="SYP", amount="9000.00")
        self.assertEqual(result["total_applied"], Decimal("5000.00"))
        self.assertEqual(result["unallocated_amount"], Decimal("4000.00"))
        self.assertEqual(result["allocatable_amount"], Decimal("5000.00"))

    def test_receive_from_provider_allocates_receivables_only(self):
        recv = self._create_central(
            direction=DebtDirection.RECEIVABLE,
            cause_type=DebtCauseType.MANUAL,
            cause_id="recv-1",
            remaining_syp="3000.00",
            created_offset_days=-2,
        )
        self._create_central(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id="pay-1",
            remaining_syp="9000.00",
            created_offset_days=-3,
        )
        result = self._simulate(action="receive_from_provider", currency="SYP", amount="1000.00")
        self.assertEqual(len(result["allocations"]), 1)
        self.assertEqual(result["allocations"][0]["debt_id"], recv.id)
        self.assertEqual(result["allocations"][0]["direction"], DebtDirection.RECEIVABLE)
        self.assertEqual(result["total_applied"], Decimal("1000.00"))

    def test_currency_isolation(self):
        self._create_central(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id="iso-syp",
            remaining_syp="2500.00",
            created_offset_days=-2,
        )
        self._create_central(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id="iso-usd",
            remaining_usd="200.00",
            created_offset_days=-1,
        )
        result_syp = self._simulate(action="pay_provider", currency="SYP", amount="2000.00")
        self.assertEqual(result_syp["total_applied"], Decimal("2000.00"))
        self.assertEqual(len(result_syp["allocations"]), 1)
        result_usd = self._simulate(action="pay_provider", currency="USD", amount="50.00")
        self.assertEqual(result_usd["total_applied"], Decimal("50.00"))
        self.assertEqual(len(result_usd["allocations"]), 1)

    def test_no_auto_cancel_opposite_direction_ignored(self):
        self._create_central(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id="no-cancel-pay",
            remaining_syp="1000.00",
            created_offset_days=-2,
        )
        self._create_central(
            direction=DebtDirection.RECEIVABLE,
            cause_type=DebtCauseType.MANUAL,
            cause_id="no-cancel-recv",
            remaining_syp="1000.00",
            created_offset_days=-3,
        )
        result = self._simulate(action="pay_provider", currency="SYP", amount="1000.00")
        self.assertEqual(result["total_applied"], Decimal("1000.00"))
        self.assertTrue(all(r["direction"] == DebtDirection.PAYABLE for r in result["allocations"]))

    def test_dedup_central_preferred_legacy_duplicate_counted_once(self):
        bill = self._create_bill()
        central = self._create_central(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id=bill.public_id,
            remaining_syp="4000.00",
            created_offset_days=-2,
        )
        self._create_legacy_debtor(
            source_app="billing",
            source_model="Bill",
            source_id=str(bill.id),
            currency_code="SYP",
            total="4000.00",
            created_offset_days=-2,
        )
        legacy_only = self._create_legacy_debtor(
            source_app="billing",
            source_model="Bill",
            source_id="legacy-only-771",
            currency_code="SYP",
            total="1000.00",
            created_offset_days=-1,
        )

        result = self._simulate(action="pay_provider", currency="SYP", amount="10000.00")

        self.assertEqual(result["total_applied"], Decimal("5000.00"))
        ids = {(row["debt_source"], row["debt_id"]) for row in result["allocations"]}
        self.assertIn(("central", central.id), ids)
        self.assertIn(("legacy", legacy_only.id), ids)
        self.assertEqual(len(result["allocations"]), 2)

    def test_read_only_no_mutation_side_effects(self):
        central_payable = self._create_central(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id="ro-pay-1",
            remaining_syp="9000.00",
            created_offset_days=-2,
        )
        central_receivable = self._create_central(
            direction=DebtDirection.RECEIVABLE,
            cause_type=DebtCauseType.MANUAL,
            cause_id="ro-recv-1",
            remaining_syp="3000.00",
            created_offset_days=-1,
        )
        legacy_debtor = self._create_legacy_debtor(
            source_app="billing",
            source_model="Bill",
            source_id="legacy-ro-1",
            currency_code="SYP",
            total="2000.00",
            paid_amount="500.00",
        )
        legacy_creditor = self._create_legacy_creditor(
            source_app="billing",
            source_model="ProviderReturn",
            source_id="legacy-ro-2",
            currency_code="SYP",
            total="1000.00",
            collected="100.00",
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

        result = self._simulate(action="pay_provider", currency="SYP", amount="1000.00")
        self.assertEqual(result["total_applied"], Decimal("1000.00"))

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

    def test_deterministic_ordering_uses_created_at_then_id(self):
        d1 = self._create_central(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id="tie-1",
            remaining_syp="100.00",
            created_offset_days=-1,
        )
        d2 = self._create_central(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id="tie-2",
            remaining_syp="100.00",
            created_offset_days=-1,
        )
        # Freeze same created_at then assert id tie-breaker.
        tied_ts = self._now - timedelta(days=1)
        DebtRecord.objects.filter(id__in=[d1.id, d2.id]).update(created_at=tied_ts)
        d1.refresh_from_db()
        d2.refresh_from_db()

        result = self._simulate(action="pay_provider", currency="SYP", amount="150.00")
        self.assertEqual(len(result["allocations"]), 2)
        first, second = result["allocations"]
        self.assertEqual(first["debt_id"], min(d1.id, d2.id))
        self.assertEqual(first["applied"], Decimal("100.00"))
        self.assertEqual(second["debt_id"], max(d1.id, d2.id))
        self.assertEqual(second["applied"], Decimal("50.00"))

    def test_validation_invalid_provider(self):
        with self.assertRaisesMessage(ValueError, "provider not found"):
            simulate_provider_account_allocation(
                provider_id=self.provider.id + 999999,
                action="pay_provider",
                currency="SYP",
                amount="10.00",
            )

    def test_validation_invalid_action(self):
        with self.assertRaisesMessage(ValueError, "invalid action"):
            self._simulate(action="bad-action", currency="SYP", amount="10.00")

    def test_validation_invalid_currency(self):
        with self.assertRaisesMessage(ValueError, "invalid currency"):
            self._simulate(action="pay_provider", currency="EUR", amount="10.00")

    def test_validation_amount_must_be_positive(self):
        with self.assertRaisesMessage(ValueError, "amount must be positive"):
            self._simulate(action="pay_provider", currency="SYP", amount="0")

    def test_validation_amount_precision(self):
        with self.assertRaisesMessage(ValueError, "amount supports at most 2 decimal digits"):
            self._simulate(action="pay_provider", currency="SYP", amount="10.001")

    def test_validation_no_eligible_debts(self):
        with self.assertRaisesMessage(ValueError, "no eligible debts"):
            self._simulate(action="pay_provider", currency="SYP", amount="10.00")

    def test_validation_zero_remaining_for_action_currency(self):
        self._create_central(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id="usd-only",
            remaining_usd="5.00",
            created_offset_days=-1,
        )
        with self.assertRaisesMessage(ValueError, "action/currency has zero remaining balance"):
            self._simulate(action="pay_provider", currency="SYP", amount="1.00")
