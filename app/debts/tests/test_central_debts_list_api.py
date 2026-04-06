from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

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

    def test_filter_by_debt_id_accepts_public_or_numeric_id(self):
        resp_public = self._get("/manager/debts/api/records/", debt_id=self.debt_purchase.public_id)
        self.assertEqual(resp_public.status_code, 200)
        items_public = resp_public.json()["items"]
        self.assertEqual(len(items_public), 1)
        self.assertEqual(items_public[0]["debt_id"], self.debt_purchase.public_id)

        resp_numeric = self._get("/manager/debts/api/records/", debt_id=str(self.debt_return.id))
        self.assertEqual(resp_numeric.status_code, 200)
        items_numeric = resp_numeric.json()["items"]
        self.assertEqual(len(items_numeric), 1)
        self.assertEqual(items_numeric[0]["debt_id"], self.debt_return.public_id)

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
        self.assertEqual(central.other_party_type, OtherPartyType.OTHER)
