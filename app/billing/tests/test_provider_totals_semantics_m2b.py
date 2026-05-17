from __future__ import annotations

from decimal import Decimal

from django.test import TestCase

from billing import selectors as S
from billing.models import Bill, Provider
from billing.serializers import provider_row
from debts.models import (
    DebtCauseType,
    DebtDirection,
    DebtRecord,
    DebtStatus,
    DebtorDebt,
    OtherPartyType,
    PartyType,
)
from debts.provider_position import collect_provider_open_obligations, get_provider_net_position


class ProviderTotalsSemanticsM2BTests(TestCase):
    def setUp(self):
        self._serial = 985000

    def _next_serial(self) -> int:
        self._serial += 1
        return self._serial

    def _create_central_payable(
        self,
        *,
        provider: Provider,
        cause_type: str = DebtCauseType.MANUAL,
        cause_id: str = "",
        remaining_syp: str = "0.00",
        remaining_usd: str = "0.00",
    ) -> DebtRecord:
        return DebtRecord.objects.create(
            direction=DebtDirection.PAYABLE,
            cause_type=cause_type,
            cause_id=cause_id or f"m2b-cause-{provider.id}-{self._serial}",
            source_app="billing",
            other_party_type=OtherPartyType.PROVIDER,
            other_party_id=str(provider.id),
            provider=provider,
            total_syp=Decimal(remaining_syp),
            remaining_syp=Decimal(remaining_syp),
            total_usd=Decimal(remaining_usd),
            remaining_usd=Decimal(remaining_usd),
            status=DebtStatus.OPEN,
        )

    def _create_central_receivable(
        self,
        *,
        provider: Provider,
        cause_id: str,
        remaining_syp: str = "0.00",
        remaining_usd: str = "0.00",
    ) -> DebtRecord:
        return DebtRecord.objects.create(
            direction=DebtDirection.RECEIVABLE,
            cause_type=DebtCauseType.MANUAL,
            cause_id=cause_id,
            source_app="billing",
            other_party_type=OtherPartyType.PROVIDER,
            other_party_id=str(provider.id),
            provider=provider,
            total_syp=Decimal(remaining_syp),
            remaining_syp=Decimal(remaining_syp),
            total_usd=Decimal(remaining_usd),
            remaining_usd=Decimal(remaining_usd),
            status=DebtStatus.OPEN,
        )

    def _create_legacy_payable(
        self,
        *,
        provider: Provider,
        source_id: str,
        total: str,
        paid_amount: str = "0.00",
        currency_code: str = "SYP",
    ) -> DebtorDebt:
        return DebtorDebt.objects.create(
            provider=provider,
            source_app="billing",
            source_model="Bill",
            source_id=source_id,
            total=Decimal(total),
            paid_amount=Decimal(paid_amount),
            status=DebtorDebt.Status.OPEN,
            party_type=PartyType.PROVIDER,
            party_name=provider.name,
            currency_code=currency_code,
        )

    def _serialized_provider_row(self, *, provider_id: int, include_all: bool = True) -> dict:
        items = list(S.providers_with_stats("", include_all, None, 200))
        provider_obj = next(p for p in items if int(p.id) == int(provider_id))
        return provider_row(provider_obj)

    def _collector_open_payable_count(self, *, provider_id: int) -> int:
        obligations = list(collect_provider_open_obligations(provider_id=provider_id).get("obligations") or [])
        out = 0
        for row in obligations:
            if str(row.get("direction") or "") != DebtDirection.PAYABLE:
                continue
            rem_syp = Decimal(row.get("remaining_syp") or Decimal("0.00"))
            rem_usd = Decimal(row.get("remaining_usd") or Decimal("0.00"))
            if rem_syp <= 0 and rem_usd <= 0:
                continue
            out += 1
        return out

    def _assert_row_matches_payable_projection_semantics(self, *, provider_id: int, row: dict) -> None:
        projection = get_provider_net_position(provider_id=provider_id)
        self.assertEqual(
            Decimal(row["debt_totals"]["SYP"]),
            projection["currencies"]["SYP"]["payable"],
        )
        self.assertEqual(
            Decimal(row["debt_totals"]["USD"]),
            projection["currencies"]["USD"]["payable"],
        )
        self.assertEqual(
            int(row["unpaid_bills_count"]),
            self._collector_open_payable_count(provider_id=provider_id),
        )

    def test_provider_row_total_debt_is_compatibility_syp_only(self):
        """
        Contract freeze: `total_debt` is compatibility-only and mirrors SYP payable total,
        not full provider account net balance.
        """
        provider = Provider.objects.create(name="M2B Compatibility Field Provider")
        self._create_legacy_payable(
            provider=provider,
            source_id="m2b-compat-syp",
            total="1200.00",
            paid_amount="200.00",
            currency_code="SYP",
        )
        self._create_legacy_payable(
            provider=provider,
            source_id="m2b-compat-usd",
            total="10.00",
            paid_amount="2.00",
            currency_code="USD",
        )

        row = self._serialized_provider_row(provider_id=provider.id, include_all=True)
        self.assertEqual(row["total_debt"], row["debt_totals"]["SYP"])
        self.assertEqual(Decimal(row["total_debt"]), Decimal("1000.00"))
        self.assertEqual(Decimal(row["debt_totals"]["USD"]), Decimal("8.00"))

    def test_selector_semantics_central_only_payable_matches_projection(self):
        provider = Provider.objects.create(name="M2B Selector Central Only")
        self._create_central_payable(
            provider=provider,
            cause_id="m2b-central-pay",
            remaining_syp="2000.00",
            remaining_usd="3.00",
        )

        row = self._serialized_provider_row(provider_id=provider.id, include_all=True)
        self._assert_row_matches_payable_projection_semantics(provider_id=provider.id, row=row)

    def test_selector_semantics_legacy_only_payable_matches_projection(self):
        provider = Provider.objects.create(name="M2B Selector Legacy Only")
        self._create_legacy_payable(
            provider=provider,
            source_id="m2b-legacy-pay-syp",
            total="500.00",
            paid_amount="100.00",
            currency_code="SYP",
        )
        self._create_legacy_payable(
            provider=provider,
            source_id="m2b-legacy-pay-usd",
            total="9.00",
            paid_amount="1.00",
            currency_code="USD",
        )

        row = self._serialized_provider_row(provider_id=provider.id, include_all=True)
        self._assert_row_matches_payable_projection_semantics(provider_id=provider.id, row=row)

    def test_selector_semantics_central_legacy_duplicate_counts_once(self):
        provider = Provider.objects.create(name="M2B Selector Duplicate")
        bill = Bill.objects.create(serial=self._next_serial(), provider=provider)
        self._create_central_payable(
            provider=provider,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id=bill.public_id,
            remaining_syp="400.00",
        )
        self._create_legacy_payable(
            provider=provider,
            source_id=str(bill.id),  # duplicate mirror
            total="500.00",
            paid_amount="100.00",
            currency_code="SYP",
        )
        self._create_legacy_payable(
            provider=provider,
            source_id="m2b-legacy-only",
            total="50.00",
            paid_amount="0.00",
            currency_code="USD",
        )

        row = self._serialized_provider_row(provider_id=provider.id, include_all=True)
        self._assert_row_matches_payable_projection_semantics(provider_id=provider.id, row=row)
        self.assertEqual(Decimal(row["debt_totals"]["SYP"]), Decimal("400.00"))
        self.assertEqual(Decimal(row["debt_totals"]["USD"]), Decimal("50.00"))

    def test_receivable_only_provider_excluded_when_include_all_false(self):
        provider = Provider.objects.create(name="M2B Selector Receivable Only")
        self._create_central_receivable(
            provider=provider,
            cause_id="m2b-rec-only",
            remaining_syp="700.00",
        )

        rows = list(S.providers_with_stats("", False, None, 200))
        ids = {int(p.id) for p in rows}
        self.assertNotIn(provider.id, ids)

