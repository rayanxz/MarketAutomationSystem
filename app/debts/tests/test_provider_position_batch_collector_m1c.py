from __future__ import annotations

from decimal import Decimal

from django.test import TestCase

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
from debts.provider_position import (
    collect_provider_open_obligations,
    collect_provider_open_obligations_batch,
)


class ProviderPositionBatchCollectorM1CTests(TestCase):
    def _normalize_snapshot(self, payload: dict) -> dict:
        obligations = []
        for row in list(payload.get("obligations") or []):
            obligations.append(
                {
                    "debt_source": str(row.get("debt_source") or ""),
                    "debt_id": int(row.get("debt_id") or 0),
                    "public_id": row.get("public_id"),
                    "direction": str(row.get("direction") or ""),
                    "cause_type": str(row.get("cause_type") or ""),
                    "cause_id": str(row.get("cause_id") or ""),
                    "remaining_syp": Decimal(row.get("remaining_syp") or Decimal("0.00")),
                    "remaining_usd": Decimal(row.get("remaining_usd") or Decimal("0.00")),
                }
            )
        obligations.sort(key=lambda item: (item["debt_source"], item["debt_id"]))

        def _norm(v):
            if isinstance(v, Decimal):
                return str(v)
            if isinstance(v, list):
                return [_norm(x) for x in v]
            if isinstance(v, dict):
                return {k: _norm(v[k]) for k in sorted(v.keys())}
            return v

        dedup_warnings = [_norm(row) for row in list(payload.get("diagnostics", {}).get("dedup_warnings") or [])]
        unresolved = [_norm(row) for row in list(payload.get("diagnostics", {}).get("unresolved_identities") or [])]
        dedup_warnings.sort(key=repr)
        unresolved.sort(key=repr)

        return {
            "provider_id": int(payload.get("provider_id") or 0),
            "obligations": obligations,
            "diagnostics": {
                "dedup_warnings": dedup_warnings,
                "unresolved_identities": unresolved,
            },
        }

    def _assert_batch_matches_single(self, provider: Provider) -> None:
        single = collect_provider_open_obligations(provider_id=provider.id)
        batch = collect_provider_open_obligations_batch(provider_ids=[provider.id]).get(provider.id)
        self.assertIsNotNone(batch)
        self.assertEqual(self._normalize_snapshot(single), self._normalize_snapshot(batch))

    def _create_central(
        self,
        *,
        provider: Provider,
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
            other_party_id=str(provider.id),
            provider=provider,
            total_syp=Decimal(remaining_syp),
            total_usd=Decimal(remaining_usd),
            remaining_syp=Decimal(remaining_syp),
            remaining_usd=Decimal(remaining_usd),
            status=status,
        )

    def test_batch_collector_parity_central_only(self):
        provider = Provider.objects.create(name="M1C Batch Central Only")
        self._create_central(
            provider=provider,
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.MANUAL,
            cause_id="m1c-central-pay-syp",
            remaining_syp="2100.00",
        )
        self._create_central(
            provider=provider,
            direction=DebtDirection.RECEIVABLE,
            cause_type=DebtCauseType.MANUAL,
            cause_id="m1c-central-rec-usd",
            remaining_usd="5.00",
        )
        self._assert_batch_matches_single(provider)

    def test_batch_collector_parity_legacy_only(self):
        provider = Provider.objects.create(name="M1C Batch Legacy Only")
        DebtorDebt.objects.create(
            provider=provider,
            source_app="billing",
            source_model="Bill",
            source_id="legacy-bill-1",
            total=Decimal("1200.00"),
            paid_amount=Decimal("200.00"),
            status=DebtorDebt.Status.OPEN,
            party_type=PartyType.PROVIDER,
            party_name=provider.name,
            currency_code="SYP",
        )
        CreditorDebt.objects.create(
            provider=provider,
            source_app="billing",
            source_model="ProviderReturn",
            source_id="legacy-return-1",
            total=Decimal("100.00"),
            collected=Decimal("20.00"),
            status=CreditorDebt.Status.OPEN,
            party_type=PartyType.PROVIDER,
            party_name=provider.name,
            currency_code="USD",
        )
        self._assert_batch_matches_single(provider)

    def test_batch_collector_parity_mixed_duplicates_and_public_ids(self):
        provider = Provider.objects.create(name="M1C Batch Mixed")
        bill = Bill.objects.create(serial=981001, provider=provider)
        pret = ProviderReturn.objects.create(provider=provider)

        self._create_central(
            provider=provider,
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id=bill.public_id,
            remaining_syp="400.00",
        )
        self._create_central(
            provider=provider,
            direction=DebtDirection.RECEIVABLE,
            cause_type=DebtCauseType.PROVIDER_RETURN,
            cause_id=pret.public_id,
            remaining_syp="200.00",
        )

        DebtorDebt.objects.create(
            provider=provider,
            source_app="billing",
            source_model="Bill",
            source_id=bill.public_id,  # public-id dedup variant
            total=Decimal("400.00"),
            paid_amount=Decimal("0.00"),
            status=DebtorDebt.Status.OPEN,
            party_type=PartyType.PROVIDER,
            party_name=provider.name,
            currency_code="SYP",
        )
        CreditorDebt.objects.create(
            provider=provider,
            source_app="billing",
            source_model="ProviderReturn",
            source_id=str(pret.id),  # numeric-id dedup variant
            total=Decimal("200.00"),
            collected=Decimal("0.00"),
            status=CreditorDebt.Status.OPEN,
            party_type=PartyType.PROVIDER,
            party_name=provider.name,
            currency_code="SYP",
        )
        DebtorDebt.objects.create(
            provider=provider,
            source_app="billing",
            source_model="Bill",
            source_id="legacy-only-payable",
            total=Decimal("99.00"),
            paid_amount=Decimal("0.00"),
            status=DebtorDebt.Status.OPEN,
            party_type=PartyType.PROVIDER,
            party_name=provider.name,
            currency_code="USD",
        )

        self._assert_batch_matches_single(provider)

    def test_batch_collector_parity_preserves_mismatch_diagnostics(self):
        provider = Provider.objects.create(name="M1C Batch Mismatch Diagnostics")
        bill = Bill.objects.create(serial=981002, provider=provider)
        self._create_central(
            provider=provider,
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id=bill.public_id,
            remaining_syp="300.00",
        )
        DebtorDebt.objects.create(
            provider=provider,
            source_app="billing",
            source_model="Bill",
            source_id=str(bill.id),
            total=Decimal("500.00"),
            paid_amount=Decimal("0.00"),
            status=DebtorDebt.Status.OPEN,
            party_type=PartyType.PROVIDER,
            party_name=provider.name,
            currency_code="SYP",
        )
        self._assert_batch_matches_single(provider)

