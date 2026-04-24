from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import AccountProfile
from billing.models import Provider
from debts import services as DebtSV
from debts.models import (
    DebtRecord,
    DebtDirection,
    DebtCauseType,
    DebtStatus,
    OtherPartyType,
)
from financials import services as FinSV
from pos.models import CustomerProfile


class CentralDebtsListApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.manager = user_model.objects.create_user(
            username="debts_api_mgr",
            password="123456",
        )
        AccountProfile.objects.create(user=cls.manager, role=AccountProfile.Role.MANAGER)

        cls.provider_alpha = Provider.objects.create(name="Provider Alpha")
        cls.provider_beta = Provider.objects.create(name="Provider Beta")
        cls.customer_john = CustomerProfile.objects.create(name="John Customer")
        FinSV.set_current_fx(actor=cls.manager, rate_syp_per_usd=Decimal("21000"))

        cls.debt_purchase = DebtRecord.objects.create(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id="101",
            source_app="billing",
            other_party_type=OtherPartyType.PROVIDER,
            other_party_id=str(cls.provider_alpha.id),
            provider=cls.provider_alpha,
            actor_username="debts_api_mgr",
            total_syp=Decimal("1000"),
            total_usd=Decimal("2"),
            remaining_syp=Decimal("400"),
            remaining_usd=Decimal("1"),
            status=DebtStatus.OPEN,
        )
        cls.debt_return = DebtRecord.objects.create(
            direction=DebtDirection.RECEIVABLE,
            cause_type=DebtCauseType.PROVIDER_RETURN,
            cause_id="202",
            source_app="billing",
            other_party_type=OtherPartyType.PROVIDER,
            other_party_id=str(cls.provider_beta.id),
            provider=cls.provider_beta,
            actor_username="debts_api_mgr",
            total_syp=Decimal("0"),
            total_usd=Decimal("5"),
            remaining_syp=Decimal("0"),
            remaining_usd=Decimal("0"),
            status=DebtStatus.CLOSED,
        )
        cls.debt_pos = DebtRecord.objects.create(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.POS_BILL,
            cause_id="303",
            source_app="pos",
            other_party_type=OtherPartyType.CUSTOMER,
            other_party_id=str(cls.customer_john.id),
            customer=cls.customer_john,
            actor_username="debts_api_mgr",
            total_syp=Decimal("2500"),
            total_usd=Decimal("0"),
            remaining_syp=Decimal("2500"),
            remaining_usd=Decimal("0"),
            status=DebtStatus.OPEN,
        )
        cls.debt_manual_other = DebtRecord.objects.create(
            direction=DebtDirection.RECEIVABLE,
            cause_type=DebtCauseType.MANUAL,
            cause_id="manual:777",
            source_app="debts",
            other_party_type=OtherPartyType.OTHER,
            other_party_id="External Party",
            actor_username="debts_api_mgr",
            total_syp=Decimal("500"),
            total_usd=Decimal("0"),
            remaining_syp=Decimal("100"),
            remaining_usd=Decimal("0"),
            status=DebtStatus.OPEN,
        )

        now = timezone.now()
        DebtRecord.objects.filter(pk=cls.debt_purchase.pk).update(created_at=now - timedelta(days=10))
        DebtRecord.objects.filter(pk=cls.debt_return.pk).update(created_at=now - timedelta(days=5))

    def setUp(self):
        self.client.force_login(self.manager)

    def _get(self, path: str, **params):
        return self.client.get(path, data=params)

    def test_central_list_returns_debtrecord_rows_shape(self):
        resp = self._get("/manager/debts/api/records/", page_size=50)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(len(data["items"]), 4)

        row = data["items"][0]
        for key in (
            "debt_id",
            "debt_type",
            "cause_type",
            "cause_id",
            "other_party_type",
            "other_party_name",
            "total_syp",
            "total_usd",
            "remaining_syp",
            "remaining_usd",
            "actor_username",
            "created_at",
            "status",
            "view_direction",
            "view_entry_id",
            "debt_view_url",
        ):
            self.assertIn(key, row)
        self.assertNotIn("currency_code", row)

    def test_filter_by_debt_type_and_cause_and_cause_id(self):
        resp = self._get(
            "/manager/debts/api/records/",
            debt_type="debtor",
            cause_type="purchase_bill",
            cause_id="101",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(len(data["items"]), 1)
        self.assertEqual(data["items"][0]["debt_id"], self.debt_purchase.public_id)

    def test_filter_by_cause_id_accepts_prefixed_public_ref_for_specific_type(self):
        resp = self._get(
            "/manager/debts/api/records/",
            cause_type="purchase_bill",
            cause_id="PB-101",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(len(data["items"]), 1)
        self.assertEqual(data["items"][0]["debt_id"], self.debt_purchase.public_id)

    def test_filter_by_cause_id_accepts_prefixed_public_ref_in_all_mode(self):
        resp_purchase = self._get("/manager/debts/api/records/", cause_id="PB-101")
        self.assertEqual(resp_purchase.status_code, 200)
        items_purchase = resp_purchase.json()["items"]
        self.assertEqual(len(items_purchase), 1)
        self.assertEqual(items_purchase[0]["debt_id"], self.debt_purchase.public_id)

        resp_return = self._get("/manager/debts/api/records/", cause_id="PR-202")
        self.assertEqual(resp_return.status_code, 200)
        items_return = resp_return.json()["items"]
        self.assertEqual(len(items_return), 1)
        self.assertEqual(items_return[0]["debt_id"], self.debt_return.public_id)

        resp_pos = self._get("/manager/debts/api/records/", cause_id="PS-303")
        self.assertEqual(resp_pos.status_code, 200)
        items_pos = resp_pos.json()["items"]
        self.assertEqual(len(items_pos), 1)
        self.assertEqual(items_pos[0]["debt_id"], self.debt_pos.public_id)

    def test_manual_cause_type_ignores_cause_id_filter_value(self):
        resp = self._get(
            "/manager/debts/api/records/",
            cause_type="manual",
            cause_id="PB-101",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(len(data["items"]), 1)
        self.assertEqual(data["items"][0]["debt_id"], self.debt_manual_other.public_id)

    def test_filter_by_other_party_type_and_name(self):
        resp_provider = self._get(
            "/manager/debts/api/records/",
            other_party_type="provider",
            other_party_name="Beta",
        )
        self.assertEqual(resp_provider.status_code, 200)
        data_provider = resp_provider.json()
        self.assertEqual(len(data_provider["items"]), 1)
        self.assertEqual(data_provider["items"][0]["debt_id"], self.debt_return.public_id)

        resp_customer = self._get(
            "/manager/debts/api/records/",
            other_party_type="customer",
            other_party_name="John",
        )
        self.assertEqual(resp_customer.status_code, 200)
        data_customer = resp_customer.json()
        self.assertEqual(len(data_customer["items"]), 1)
        self.assertEqual(data_customer["items"][0]["debt_id"], self.debt_pos.public_id)

    def test_filter_by_debt_id_accepts_public_id_only(self):
        resp_public = self._get("/manager/debts/api/records/", debt_id=self.debt_purchase.public_id)
        self.assertEqual(resp_public.status_code, 200)
        items_public = resp_public.json()["items"]
        self.assertEqual(len(items_public), 1)
        self.assertEqual(items_public[0]["debt_id"], self.debt_purchase.public_id)

        resp_numeric = self._get("/manager/debts/api/records/", debt_id=str(self.debt_return.id))
        self.assertEqual(resp_numeric.status_code, 200)
        items_numeric = resp_numeric.json()["items"]
        self.assertEqual(len(items_numeric), 0)

    def test_date_filters_accept_dd_mm_yyyy(self):
        target_day = (timezone.now() - timedelta(days=5)).date().strftime("%d/%m/%Y")
        resp = self._get("/manager/debts/api/records/", date_from=target_day, date_to=target_day)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        ids = {row["debt_id"] for row in data["items"]}
        self.assertIn(self.debt_return.public_id, ids)
        self.assertNotIn(self.debt_purchase.public_id, ids)

    def test_cause_id_in_rows_is_displayed_as_public_ref(self):
        resp = self._get("/manager/debts/api/records/", page_size=50)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])

        by_debt_id = {row["debt_id"]: row for row in data["items"]}
        self.assertEqual(by_debt_id[self.debt_purchase.public_id]["cause_id"], "PB-101")
        self.assertEqual(by_debt_id[self.debt_return.public_id]["cause_id"], "PR-202")
        self.assertEqual(by_debt_id[self.debt_pos.public_id]["cause_id"], "PS-303")

    def test_other_party_suggest_endpoint(self):
        resp_provider = self._get(
            "/manager/debts/api/other-party-suggest/",
            other_party_type="provider",
            q="Alpha",
        )
        self.assertEqual(resp_provider.status_code, 200)
        items_provider = resp_provider.json()["items"]
        self.assertEqual(len(items_provider), 1)
        self.assertEqual(items_provider[0]["name"], "Provider Alpha")

        resp_customer = self._get(
            "/manager/debts/api/other-party-suggest/",
            other_party_type="customer",
            q="John",
        )
        self.assertEqual(resp_customer.status_code, 200)
        items_customer = resp_customer.json()["items"]
        self.assertEqual(len(items_customer), 1)
        self.assertEqual(items_customer[0]["name"], "John Customer")

    def test_manual_debt_creation_syncs_to_central_record(self):
        entry = DebtSV.create_manual_debt(
            actor=self.manager,
            direction="debtor",
            party_type="worker",
            provider_id=None,
            party_name="Field Worker",
            amount=Decimal("321"),
            currency_code="SYP",
            initial_payment=None,
            money_container_id=None,
        )
        central = DebtRecord.objects.get(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.MANUAL,
            cause_id=str(entry.id),
        )
        self.assertEqual(central.total_syp, Decimal("321.000"))
        self.assertEqual(central.remaining_syp, Decimal("321.000"))
        self.assertEqual(central.total_usd, Decimal("0.000"))
        self.assertEqual(central.remaining_usd, Decimal("0.000"))
        self.assertEqual(central.fx_syp_per_usd_at_creation, Decimal("21000.000000"))
        self.assertEqual(central.other_party_type, OtherPartyType.OTHER)

    def test_central_debt_creation_fx_snapshot_stays_fixed_after_updates(self):
        debt = DebtSV.upsert_central_debt(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.MANUAL,
            cause_id="fx-lock-1",
            source_app="debts",
            other_party_type=OtherPartyType.OTHER,
            other_party_id="FX Snapshot Party",
            actor_username="debts_api_mgr",
            total_syp=Decimal("100.000"),
            total_usd=Decimal("0.000"),
            remaining_syp=Decimal("100.000"),
            remaining_usd=Decimal("0.000"),
            note="fx lock test",
        )
        self.assertEqual(debt.fx_syp_per_usd_at_creation, Decimal("21000.000000"))

        FinSV.set_current_fx(actor=self.manager, rate_syp_per_usd=Decimal("27500"))
        debt = DebtSV.upsert_central_debt(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.MANUAL,
            cause_id="fx-lock-1",
            source_app="debts",
            other_party_type=OtherPartyType.OTHER,
            other_party_id="FX Snapshot Party",
            actor_username="debts_api_mgr",
            total_syp=Decimal("125.000"),
            total_usd=Decimal("0.000"),
            remaining_syp=Decimal("125.000"),
            remaining_usd=Decimal("0.000"),
            note="fx lock update",
        )
        self.assertEqual(debt.fx_syp_per_usd_at_creation, Decimal("21000.000000"))

    def test_view_link_is_resolved_for_manual_debt(self):
        entry = DebtSV.create_manual_debt(
            actor=self.manager,
            direction="debtor",
            party_type="worker",
            provider_id=None,
            party_name="Manual View Link",
            amount=Decimal("50"),
            currency_code="SYP",
            initial_payment=None,
            money_container_id=None,
        )
        central = DebtRecord.objects.get(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.MANUAL,
            cause_id=str(entry.id),
        )

        resp = self._get("/manager/debts/api/records/", debt_id=central.public_id)
        self.assertEqual(resp.status_code, 200)
        items = resp.json()["items"]
        self.assertEqual(len(items), 1)
        row = items[0]
        self.assertEqual(row["view_direction"], "debtor")
        self.assertEqual(row["view_entry_id"], entry.id)
        self.assertEqual(row["debt_view_url"], f"/manager/debts/view/record/{central.public_id}/")

    def test_view_link_is_empty_when_legacy_entry_not_resolved(self):
        resp = self._get("/manager/debts/api/records/", debt_id=self.debt_pos.public_id)
        self.assertEqual(resp.status_code, 200)
        items = resp.json()["items"]
        self.assertEqual(len(items), 1)
        row = items[0]
        self.assertEqual(row["debt_id"], self.debt_pos.public_id)
        self.assertEqual(row["view_direction"], "")
        self.assertIsNone(row["view_entry_id"])
        self.assertEqual(row["debt_view_url"], f"/manager/debts/view/record/{self.debt_pos.public_id}/")

    def test_central_view_page_opens_by_public_id(self):
        resp = self.client.get(f"/manager/debts/view/record/{self.debt_purchase.public_id}/")
        self.assertEqual(resp.status_code, 200)

    def test_central_view_page_rejects_internal_numeric_id(self):
        resp = self.client.get(f"/manager/debts/view/record/{self.debt_purchase.id}/")
        self.assertEqual(resp.status_code, 404)

    def test_central_view_page_opens_pos_debt_without_legacy_entry(self):
        resp = self.client.get(f"/manager/debts/view/record/{self.debt_pos.public_id}/")
        self.assertEqual(resp.status_code, 200)

    def test_central_view_context_uses_global_2dp_ui_values(self):
        debt = DebtRecord.objects.create(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.MANUAL,
            cause_id="fmt-901",
            source_app="debts",
            other_party_type=OtherPartyType.OTHER,
            other_party_id="Fmt Party",
            actor_username="debts_api_mgr",
            total_syp=Decimal("123.459"),
            total_usd=Decimal("4.129"),
            remaining_syp=Decimal("11.999"),
            remaining_usd=Decimal("0.567"),
            fx_syp_per_usd_at_creation=Decimal("19750.987654"),
            status=DebtStatus.OPEN,
        )
        resp = self.client.get(f"/manager/debts/view/record/{debt.public_id}/")
        self.assertEqual(resp.status_code, 200)

        ui = resp.context["debt_ui"]
        self.assertEqual(ui["total_syp"], Decimal("123.46"))
        self.assertEqual(ui["total_usd"], Decimal("4.13"))
        self.assertEqual(ui["remaining_syp"], Decimal("12.00"))
        self.assertEqual(ui["remaining_usd"], Decimal("0.57"))
        self.assertEqual(ui["fx_syp_per_usd_at_creation"], Decimal("19750.99"))

        settlement_ui = resp.context["settlement_ui"]
        q2 = Decimal("0.01")
        expected_syp = (Decimal("11.999") + (Decimal("0.567") * Decimal("21000"))).quantize(
            q2,
            rounding=ROUND_HALF_UP,
        )
        expected_usd = (Decimal("0.567") + (Decimal("11.999") / Decimal("21000"))).quantize(
            q2,
            rounding=ROUND_HALF_UP,
        )
        self.assertEqual(settlement_ui["total_syp"], expected_syp)
        self.assertEqual(settlement_ui["total_usd"], expected_usd)
        self.assertEqual(settlement_ui["default_currency"], "SYP")
        self.assertEqual(settlement_ui["currency_options"], ["SYP", "USD"])
        self.assertEqual(settlement_ui["fx_syp_per_usd_current"], Decimal("21000.00"))

    def test_central_view_context_settlement_ui_handles_old_debt_without_fx(self):
        debt = DebtRecord.objects.create(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.MANUAL,
            cause_id="fmt-old-001",
            source_app="debts",
            other_party_type=OtherPartyType.OTHER,
            other_party_id="Legacy Party",
            actor_username="debts_api_mgr",
            total_syp=Decimal("300.000"),
            total_usd=Decimal("2.000"),
            remaining_syp=Decimal("100.000"),
            remaining_usd=Decimal("2.000"),
            fx_syp_per_usd_at_creation=None,
            status=DebtStatus.OPEN,
        )
        resp = self.client.get(f"/manager/debts/view/record/{debt.public_id}/")
        self.assertEqual(resp.status_code, 200)

        settlement_ui = resp.context["settlement_ui"]
        self.assertEqual(settlement_ui["total_syp"], Decimal("42100.00"))
        self.assertEqual(settlement_ui["total_usd"], Decimal("2.00"))
        self.assertEqual(settlement_ui["default_currency"], "SYP")
        self.assertEqual(settlement_ui["currency_options"], ["SYP", "USD"])
        self.assertEqual(settlement_ui["fx_syp_per_usd_current"], Decimal("21000.00"))
