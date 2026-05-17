from __future__ import annotations

from decimal import Decimal

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from billing import selectors as S
from billing.models import Bill, Provider
from debts.models import (
    DebtCauseType,
    DebtDirection,
    DebtRecord,
    DebtStatus,
    DebtorDebt,
    OtherPartyType,
    PartyType,
)
from debts.provider_position import get_provider_net_position


class ProviderTotalsBatchingM1CTests(TestCase):
    def _create_central_payable(self, *, provider: Provider, amount: str, cause_id: str) -> DebtRecord:
        return DebtRecord.objects.create(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.MANUAL,
            cause_id=cause_id,
            source_app="debts",
            other_party_type=OtherPartyType.PROVIDER,
            other_party_id=str(provider.id),
            provider=provider,
            total_syp=Decimal(amount),
            remaining_syp=Decimal(amount),
            total_usd=Decimal("0.00"),
            remaining_usd=Decimal("0.00"),
            status=DebtStatus.OPEN,
        )

    def test_providers_with_stats_query_budget_does_not_grow_linearly_for_include_all(self):
        p1 = Provider.objects.create(name="M1C Budget P1")
        self._create_central_payable(provider=p1, amount="10.00", cause_id="m1c-budget-1")

        with CaptureQueriesContext(connection) as one_ctx:
            rows_1 = S.providers_with_stats("", True, None, 1)
        self.assertEqual(len(rows_1), 1)

        for idx in range(2, 31):
            provider = Provider.objects.create(name=f"M1C Budget P{idx}")
            self._create_central_payable(provider=provider, amount="10.00", cause_id=f"m1c-budget-{idx}")

        with CaptureQueriesContext(connection) as thirty_ctx:
            rows_30 = S.providers_with_stats("", True, None, 30)

        self.assertEqual(len(rows_30), 30)
        self.assertLessEqual(
            len(thirty_ctx.captured_queries),
            len(one_ctx.captured_queries) + 6,
            f"1-provider queries={len(one_ctx.captured_queries)}, 30-provider queries={len(thirty_ctx.captured_queries)}",
        )

    def test_providers_with_stats_sparse_include_all_false_query_budget(self):
        tracked_ids: list[int] = []
        for idx in range(1, 4):
            provider = Provider.objects.create(name=f"M1C Sparse Eligible {idx}")
            tracked_ids.append(provider.id)
            self._create_central_payable(provider=provider, amount="10.00", cause_id=f"m1c-sparse-{idx}")

        for idx in range(1, 121):
            Provider.objects.create(name=f"M1C Sparse Empty {idx:03d}")

        with CaptureQueriesContext(connection) as ctx:
            rows = S.providers_with_stats("", False, None, 30)

        returned_ids = {int(p.id) for p in rows}
        self.assertTrue(set(tracked_ids).issubset(returned_ids))
        self.assertLessEqual(
            len(ctx.captured_queries),
            30,
            f"sparse include_all=false query budget unexpectedly high: {len(ctx.captured_queries)}",
        )

    def test_providers_with_stats_mixed_coexistence_query_budget_and_projection_parity(self):
        providers: list[Provider] = []
        for idx in range(1, 11):
            provider = Provider.objects.create(name=f"M1C Mixed Provider {idx}")
            providers.append(provider)
            bill = Bill.objects.create(serial=990000 + idx, provider=provider)
            DebtRecord.objects.create(
                direction=DebtDirection.PAYABLE,
                cause_type=DebtCauseType.PURCHASE_BILL,
                cause_id=bill.public_id,
                source_app="billing",
                other_party_type=OtherPartyType.PROVIDER,
                other_party_id=str(provider.id),
                provider=provider,
                total_syp=Decimal("400.00"),
                remaining_syp=Decimal("400.00"),
                total_usd=Decimal("0.00"),
                remaining_usd=Decimal("0.00"),
                status=DebtStatus.OPEN,
            )
            DebtorDebt.objects.create(
                provider=provider,
                source_app="billing",
                source_model="Bill",
                source_id=bill.public_id,  # duplicate mirror, should be deduped
                total=Decimal("400.00"),
                paid_amount=Decimal("0.00"),
                status=DebtorDebt.Status.OPEN,
                party_type=PartyType.PROVIDER,
                party_name=provider.name,
                currency_code="SYP",
            )
            DebtorDebt.objects.create(
                provider=provider,
                source_app="billing",
                source_model="Bill",
                source_id=f"legacy-only-{idx}",
                total=Decimal("100.00"),
                paid_amount=Decimal("0.00"),
                status=DebtorDebt.Status.OPEN,
                party_type=PartyType.PROVIDER,
                party_name=provider.name,
                currency_code="USD",
            )

        with CaptureQueriesContext(connection) as ctx:
            rows = S.providers_with_stats("", True, None, 30)

        self.assertGreaterEqual(len(rows), 10)
        self.assertLessEqual(len(ctx.captured_queries), 20, len(ctx.captured_queries))

        rows_by_id = {int(p.id): p for p in rows}
        for provider in providers:
            self.assertIn(provider.id, rows_by_id)
            projection = get_provider_net_position(provider_id=provider.id)
            row = rows_by_id[provider.id]
            self.assertEqual(Decimal(getattr(row, "total_debt_syp", Decimal("0.00"))), projection["currencies"]["SYP"]["payable"])
            self.assertEqual(Decimal(getattr(row, "total_debt_usd", Decimal("0.00"))), projection["currencies"]["USD"]["payable"])

