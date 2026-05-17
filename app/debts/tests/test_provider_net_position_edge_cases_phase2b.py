from __future__ import annotations

from decimal import Decimal

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from billing.models import Bill, Provider, ProviderReturn
from debts.models import (
    CreditorDebt,
    DebtCauseType,
    DebtDirection,
    DebtRecord,
    DebtStatus,
    DebtorDebt,
    OtherPartyType,
    PartyType,
)
from debts.provider_position import get_provider_net_position


class ProviderNetPositionEdgeCasesPhase2BTests(TestCase):
    """
    Phase 2B: executable edge-case contract for dedup/source-identity hardening.

    NOTE:
    - Tests here intentionally freeze behavior that is not yet implemented.
    - Failing tests are expected before Phase 2C hardening.
    """

    def setUp(self):
        self.provider = Provider.objects.create(name=f"Phase2B Provider {self._testMethodName}")
        self._serial_seq = 870000

    def _next_serial(self) -> int:
        self._serial_seq += 1
        return self._serial_seq

    def _project(self) -> dict:
        return get_provider_net_position(provider_id=self.provider.id)

    def _create_bill(self) -> Bill:
        return Bill.objects.create(serial=self._next_serial(), provider=self.provider)

    def _create_return(self) -> ProviderReturn:
        return ProviderReturn.objects.create(provider=self.provider)

    def _create_central(
        self,
        *,
        direction: str,
        cause_type: str,
        cause_id: str,
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
            actor_username="phase2b",
            total_syp=Decimal(remaining_syp),
            total_usd=Decimal(remaining_usd),
            remaining_syp=Decimal(remaining_syp),
            remaining_usd=Decimal(remaining_usd),
            status=status,
        )

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
    ) -> DebtorDebt:
        return DebtorDebt.objects.create(
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
    ) -> CreditorDebt:
        return CreditorDebt.objects.create(
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

    def _assert_diagnostics_shape(self, result: dict) -> dict:
        self.assertIn("diagnostics", result)
        diagnostics = result["diagnostics"]
        self.assertIsInstance(diagnostics, dict)
        self.assertIn("dedup_warnings", diagnostics)
        self.assertIn("unresolved_identities", diagnostics)
        self.assertIsInstance(diagnostics["dedup_warnings"], list)
        self.assertIsInstance(diagnostics["unresolved_identities"], list)
        return diagnostics

    def test_legacy_purchase_public_source_id_dedups_with_central(self):
        """
        Central purchase payable + legacy debtor for same bill where legacy source_id uses PB-* text.
        Expected: central wins, counted once.
        """
        bill = self._create_bill()
        self._create_central(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id=bill.public_id,
            remaining_syp="4000.00",
        )
        self._create_legacy_debtor(
            source_app="billing",
            source_model="Bill",
            source_id=bill.public_id,  # public-id text edge case
            currency_code="SYP",
            total="4000.00",
        )

        result = self._project()
        syp = result["currencies"]["SYP"]
        self.assertEqual(syp["payable"], Decimal("4000.00"))
        self.assertEqual(syp["open_payable_count"], 1)

    def test_legacy_provider_return_public_source_id_dedups_with_central(self):
        """
        Central provider-return receivable + legacy creditor for same return where source_id uses PR-* text.
        Expected: central wins, counted once.
        """
        pret = self._create_return()
        self._create_central(
            direction=DebtDirection.RECEIVABLE,
            cause_type=DebtCauseType.PROVIDER_RETURN,
            cause_id=pret.public_id,
            remaining_syp="1800.00",
        )
        self._create_legacy_creditor(
            source_app="billing",
            source_model="ProviderReturn",
            source_id=pret.public_id,  # public-id text edge case
            currency_code="SYP",
            total="1800.00",
        )

        result = self._project()
        syp = result["currencies"]["SYP"]
        self.assertEqual(syp["receivable"], Decimal("1800.00"))
        self.assertEqual(syp["open_receivable_count"], 1)

    def test_amount_mismatch_freezes_diagnostics_contract(self):
        """
        Same source in central+legacy with different amounts:
        - projection still uses central amount
        - diagnostics must expose dedup mismatch warning
        """
        bill = self._create_bill()
        self._create_central(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id=bill.public_id,
            remaining_syp="700.00",
        )
        self._create_legacy_debtor(
            source_app="billing",
            source_model="Bill",
            source_id=str(bill.id),
            currency_code="SYP",
            total="1000.00",
        )

        result = self._project()
        self.assertEqual(result["currencies"]["SYP"]["payable"], Decimal("700.00"))

        diagnostics = self._assert_diagnostics_shape(result)
        mismatch = [
            row
            for row in diagnostics["dedup_warnings"]
            if row.get("warning_type") == "central_legacy_amount_mismatch"
            and row.get("source_identity") == {
                "source_app": "billing",
                "source_model": "Bill",
                "source_id": str(bill.id),
            }
        ]
        self.assertTrue(mismatch, diagnostics["dedup_warnings"])
        warning = mismatch[0]
        self.assertEqual(
            warning.get("central_amount"),
            {"SYP": Decimal("700.00"), "USD": Decimal("0.00")},
        )
        self.assertEqual(
            warning.get("legacy_amount"),
            {"SYP": Decimal("1000.00"), "USD": Decimal("0.00")},
        )

    def test_malformed_central_cause_id_emits_unresolved_warning(self):
        """
        If central cause identity is malformed/unresolvable, test freezes diagnostics requirement
        rather than guessing dedup result.
        """
        bill = self._create_bill()
        self._create_central(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id="not-a-valid-bill-ref",
            remaining_syp="1000.00",
        )
        self._create_legacy_debtor(
            source_app="billing",
            source_model="Bill",
            source_id=str(bill.id),
            currency_code="SYP",
            total="1000.00",
        )

        result = self._project()
        diagnostics = self._assert_diagnostics_shape(result)
        unresolved = [
            row
            for row in diagnostics["unresolved_identities"]
            if row.get("warning_type") == "unresolved_central_identity"
            and row.get("cause_type") == DebtCauseType.PURCHASE_BILL
            and row.get("cause_id") == "not-a-valid-bill-ref"
        ]
        self.assertTrue(unresolved, diagnostics["unresolved_identities"])

    def test_missing_source_object_for_central_public_ref_emits_unresolved_warning(self):
        self._create_central(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id="PB-999999",
            remaining_syp="1000.00",
        )
        self._create_legacy_debtor(
            source_app="billing",
            source_model="Bill",
            source_id="999999",
            currency_code="SYP",
            total="1000.00",
        )

        result = self._project()
        diagnostics = self._assert_diagnostics_shape(result)
        unresolved = [
            row
            for row in diagnostics["unresolved_identities"]
            if row.get("warning_type") == "missing_source_object"
            and row.get("cause_type") == DebtCauseType.PURCHASE_BILL
            and row.get("cause_id") == "PB-999999"
        ]
        self.assertTrue(unresolved, diagnostics["unresolved_identities"])

    def test_manual_desync_emits_diagnostic_warning(self):
        """
        Central manual cause_id non-numeric + legacy manual row -> freeze warning requirement.
        """
        self._create_central(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.MANUAL,
            cause_id="manual:desync",
            remaining_syp="500.00",
        )
        self._create_legacy_debtor(
            source_app="debts",
            source_model="ManualDebt",
            source_id="manual:123",
            currency_code="SYP",
            total="500.00",
        )

        result = self._project()
        diagnostics = self._assert_diagnostics_shape(result)
        manual_warn = [
            row
            for row in diagnostics["dedup_warnings"]
            if row.get("warning_type") == "manual_identity_desync"
        ]
        self.assertTrue(manual_warn, diagnostics["dedup_warnings"])

    def test_single_provider_projection_query_budget_is_fixed(self):
        """
        Guard against row-level N+1 behavior for single-provider projection.
        """
        for idx in range(10):
            bill = self._create_bill()
            self._create_central(
                direction=DebtDirection.PAYABLE,
                cause_type=DebtCauseType.PURCHASE_BILL,
                cause_id=bill.public_id,
                remaining_syp="10.00",
            )
            self._create_legacy_debtor(
                source_app="billing",
                source_model="Bill",
                source_id=f"legacy-only-{idx}",
                currency_code="SYP",
                total="10.00",
            )

        with CaptureQueriesContext(connection) as ctx:
            result = self._project()

        self.assertEqual(result["currencies"]["SYP"]["payable"], Decimal("200.00"))
        # Fixed-size query envelope for current design; should not scale with row count.
        self.assertLessEqual(len(ctx.captured_queries), 8, [q.get("sql") for q in ctx.captured_queries])
