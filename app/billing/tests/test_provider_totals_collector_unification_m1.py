from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import AccountProfile
from billing.models import Bill, Provider
from debts.models import (
    DebtorDebt,
    DebtCauseType,
    DebtDirection,
    DebtRecord,
    DebtStatus,
    OtherPartyType,
    PartyType,
)
from debts.provider_position import collect_provider_open_obligations, get_provider_net_position


class ProviderTotalsCollectorUnificationM1Tests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.manager = user_model.objects.create_user(
            username="m1_provider_totals_mgr",
            password="pw12345",
        )
        AccountProfile.objects.create(user=cls.manager, role=AccountProfile.Role.MANAGER)

    def setUp(self):
        ok = self.client.login(username="m1_provider_totals_mgr", password="pw12345")
        self.assertTrue(ok)

    def _providers_list_items(self, **params):
        base = {
            "include_all": "1",
            "page_size": "100",
        }
        base.update(params)
        resp = self.client.get(reverse("billing_api_providers_list"), base)
        self.assertEqual(resp.status_code, 200, resp.content.decode("utf-8"))
        body = resp.json()
        self.assertTrue(body.get("ok"), body)
        return body["items"]

    def _provider_row(self, provider_id: int, *, items: list[dict]) -> dict:
        return next(row for row in items if int(row["id"]) == int(provider_id))

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

    def _assert_row_matches_projection(self, *, provider_id: int, row: dict):
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
            Decimal(row["total_debt"]),
            projection["currencies"]["SYP"]["payable"],
        )
        self.assertEqual(
            int(row["unpaid_bills_count"]),
            self._collector_open_payable_count(provider_id=provider_id),
        )

    def test_central_only_provider_totals_use_collector_semantics(self):
        provider = Provider.objects.create(name="M1 Central Only")

        DebtRecord.objects.create(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.MANUAL,
            cause_id="m1-central-pay-syp",
            source_app="debts",
            other_party_type=OtherPartyType.PROVIDER,
            other_party_id=str(provider.id),
            provider=provider,
            total_syp=Decimal("2000.00"),
            remaining_syp=Decimal("2000.00"),
            total_usd=Decimal("0.00"),
            remaining_usd=Decimal("0.00"),
            status=DebtStatus.OPEN,
        )
        DebtRecord.objects.create(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.MANUAL,
            cause_id="m1-central-pay-usd",
            source_app="debts",
            other_party_type=OtherPartyType.PROVIDER,
            other_party_id=str(provider.id),
            provider=provider,
            total_syp=Decimal("0.00"),
            remaining_syp=Decimal("0.00"),
            total_usd=Decimal("5.50"),
            remaining_usd=Decimal("5.50"),
            status=DebtStatus.OPEN,
        )
        # Receivable exists but must not inflate payable totals shown on provider list.
        DebtRecord.objects.create(
            direction=DebtDirection.RECEIVABLE,
            cause_type=DebtCauseType.MANUAL,
            cause_id="m1-central-rec-only",
            source_app="debts",
            other_party_type=OtherPartyType.PROVIDER,
            other_party_id=str(provider.id),
            provider=provider,
            total_syp=Decimal("999.00"),
            remaining_syp=Decimal("999.00"),
            total_usd=Decimal("0.00"),
            remaining_usd=Decimal("0.00"),
            status=DebtStatus.OPEN,
        )

        items = self._providers_list_items()
        row = self._provider_row(provider.id, items=items)

        self.assertEqual(Decimal(row["debt_totals"]["SYP"]), Decimal("2000.00"))
        self.assertEqual(Decimal(row["debt_totals"]["USD"]), Decimal("5.50"))
        self.assertEqual(int(row["unpaid_bills_count"]), 2)
        self._assert_row_matches_projection(provider_id=provider.id, row=row)

    def test_legacy_only_provider_totals_use_collector_semantics(self):
        provider = Provider.objects.create(name="M1 Legacy Only")

        DebtorDebt.objects.create(
            provider=provider,
            source_app="billing",
            source_model="Bill",
            source_id="m1-legacy-bill-syp",
            total=Decimal("1200.00"),
            paid_amount=Decimal("200.00"),
            status=DebtorDebt.Status.OPEN,
            party_type=PartyType.PROVIDER,
            party_name=provider.name,
            currency_code="SYP",
        )
        DebtorDebt.objects.create(
            provider=provider,
            source_app="billing",
            source_model="Bill",
            source_id="m1-legacy-bill-usd",
            total=Decimal("10.00"),
            paid_amount=Decimal("1.25"),
            status=DebtorDebt.Status.OPEN,
            party_type=PartyType.PROVIDER,
            party_name=provider.name,
            currency_code="USD",
        )

        items = self._providers_list_items()
        row = self._provider_row(provider.id, items=items)

        self.assertEqual(Decimal(row["debt_totals"]["SYP"]), Decimal("1000.00"))
        self.assertEqual(Decimal(row["debt_totals"]["USD"]), Decimal("8.75"))
        self.assertEqual(int(row["unpaid_bills_count"]), 2)
        self._assert_row_matches_projection(provider_id=provider.id, row=row)

    def test_mixed_coexistence_duplicate_obligation_is_counted_once(self):
        provider = Provider.objects.create(name="M1 Mixed Coexistence")
        bill = Bill.objects.create(serial=900001, provider=provider)

        # Central canonical payable for the purchase bill.
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
        # Legacy duplicate of the same bill obligation: should be deduped/skipped.
        DebtorDebt.objects.create(
            provider=provider,
            source_app="billing",
            source_model="Bill",
            source_id=str(bill.id),
            total=Decimal("500.00"),
            paid_amount=Decimal("100.00"),
            status=DebtorDebt.Status.OPEN,
            party_type=PartyType.PROVIDER,
            party_name=provider.name,
            currency_code="SYP",
        )
        # Legacy-only payable should still be included.
        DebtorDebt.objects.create(
            provider=provider,
            source_app="billing",
            source_model="Bill",
            source_id="m1-legacy-only-bill",
            total=Decimal("300.00"),
            paid_amount=Decimal("0.00"),
            status=DebtorDebt.Status.OPEN,
            party_type=PartyType.PROVIDER,
            party_name=provider.name,
            currency_code="SYP",
        )
        # Extra central payable in USD lane.
        DebtRecord.objects.create(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.MANUAL,
            cause_id="m1-extra-usd",
            source_app="debts",
            other_party_type=OtherPartyType.PROVIDER,
            other_party_id=str(provider.id),
            provider=provider,
            total_syp=Decimal("0.00"),
            remaining_syp=Decimal("0.00"),
            total_usd=Decimal("2.00"),
            remaining_usd=Decimal("2.00"),
            status=DebtStatus.OPEN,
        )

        items = self._providers_list_items()
        row = self._provider_row(provider.id, items=items)

        # If duplicate is incorrectly summed, SYP would become 1100.00.
        self.assertEqual(Decimal(row["debt_totals"]["SYP"]), Decimal("700.00"))
        self.assertEqual(Decimal(row["debt_totals"]["USD"]), Decimal("2.00"))
        self.assertEqual(int(row["unpaid_bills_count"]), 3)
        self._assert_row_matches_projection(provider_id=provider.id, row=row)

    def test_provider_rows_match_projection_payable_totals_for_multiple_providers(self):
        p1 = Provider.objects.create(name="M1 Parity P1")
        p2 = Provider.objects.create(name="M1 Parity P2")
        p3 = Provider.objects.create(name="M1 Parity P3 Receivable Only")

        DebtRecord.objects.create(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.MANUAL,
            cause_id="m1-parity-p1-pay",
            source_app="debts",
            other_party_type=OtherPartyType.PROVIDER,
            other_party_id=str(p1.id),
            provider=p1,
            total_syp=Decimal("900.00"),
            remaining_syp=Decimal("900.00"),
            total_usd=Decimal("0.00"),
            remaining_usd=Decimal("0.00"),
            status=DebtStatus.OPEN,
        )
        DebtRecord.objects.create(
            direction=DebtDirection.RECEIVABLE,
            cause_type=DebtCauseType.MANUAL,
            cause_id="m1-parity-p1-rec",
            source_app="debts",
            other_party_type=OtherPartyType.PROVIDER,
            other_party_id=str(p1.id),
            provider=p1,
            total_syp=Decimal("500.00"),
            remaining_syp=Decimal("500.00"),
            total_usd=Decimal("0.00"),
            remaining_usd=Decimal("0.00"),
            status=DebtStatus.OPEN,
        )
        DebtorDebt.objects.create(
            provider=p2,
            source_app="billing",
            source_model="Bill",
            source_id="m1-parity-p2-legacy",
            total=Decimal("100.00"),
            paid_amount=Decimal("10.00"),
            status=DebtorDebt.Status.OPEN,
            party_type=PartyType.PROVIDER,
            party_name=p2.name,
            currency_code="SYP",
        )
        DebtRecord.objects.create(
            direction=DebtDirection.RECEIVABLE,
            cause_type=DebtCauseType.MANUAL,
            cause_id="m1-parity-p3-rec-only",
            source_app="debts",
            other_party_type=OtherPartyType.PROVIDER,
            other_party_id=str(p3.id),
            provider=p3,
            total_syp=Decimal("250.00"),
            remaining_syp=Decimal("250.00"),
            total_usd=Decimal("0.00"),
            remaining_usd=Decimal("0.00"),
            status=DebtStatus.OPEN,
        )

        items = self._providers_list_items()
        for provider in (p1, p2, p3):
            row = self._provider_row(provider.id, items=items)
            self._assert_row_matches_projection(provider_id=provider.id, row=row)

        # Receivable-only provider has zero displayed payable totals.
        p3_row = self._provider_row(p3.id, items=items)
        self.assertEqual(Decimal(p3_row["debt_totals"]["SYP"]), Decimal("0.00"))
        self.assertEqual(int(p3_row["unpaid_bills_count"]), 0)

    def test_include_all_zero_still_excludes_receivable_only_provider(self):
        provider = Provider.objects.create(name="M1 Receivable Only Hidden")
        DebtRecord.objects.create(
            direction=DebtDirection.RECEIVABLE,
            cause_type=DebtCauseType.MANUAL,
            cause_id="m1-hidden-rec-only",
            source_app="debts",
            other_party_type=OtherPartyType.PROVIDER,
            other_party_id=str(provider.id),
            provider=provider,
            total_syp=Decimal("100.00"),
            remaining_syp=Decimal("100.00"),
            total_usd=Decimal("0.00"),
            remaining_usd=Decimal("0.00"),
            status=DebtStatus.OPEN,
        )

        items = self._providers_list_items(include_all="0")
        ids = {int(row["id"]) for row in items}
        self.assertNotIn(provider.id, ids)
