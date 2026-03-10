from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import AccountProfile
from billing import services as BillingSV
from billing.models import Provider
from catalog.models import Product, ProductCollection, ProductSet, UnitType
from financials import services as FinSV
from financials.models import Currency, MoneyContainer, MoneyContainerCurrency
from stock.models import ProductContainer


class BillingOperationalCorrectnessPhase5Tests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.manager = User.objects.create_user(username="mgr_phase5_ops", password="pw12345")
        profile, _ = AccountProfile.objects.get_or_create(user=cls.manager)
        profile.role = AccountProfile.Role.MANAGER
        profile.save(update_fields=["role"])

        cls.syp, _ = Currency.objects.get_or_create(
            code="SYP",
            defaults={"name": "Syrian Pound", "decimals": 0, "is_active": True},
        )
        cls.usd, _ = Currency.objects.get_or_create(
            code="USD",
            defaults={"name": "US Dollar", "decimals": 2, "is_active": True},
        )

        cls.store, _ = ProductContainer.objects.get_or_create(
            code="store",
            defaults={"name": "Store", "is_store": True, "is_active": True},
        )
        if not cls.store.is_active:
            cls.store.is_active = True
            cls.store.save(update_fields=["is_active"])

        cls.cash = MoneyContainer.objects.create(
            name="Billing Ops Cash",
            container_type=MoneyContainer.ContainerType.DRAWER,
            is_active=True,
            created_by=cls.manager,
        )
        MoneyContainerCurrency.objects.update_or_create(
            container=cls.cash,
            currency=cls.syp,
            defaults={"is_enabled": True},
        )
        MoneyContainerCurrency.objects.update_or_create(
            container=cls.cash,
            currency=cls.usd,
            defaults={"is_enabled": True},
        )

        cls.provider = Provider.objects.create(name="Ops Provider")
        col = ProductCollection.objects.create(name="Ops Collection")
        st = ProductSet.objects.create(collection=col, name="Ops Set")
        cls.product = Product.objects.create(
            name="Ops Product",
            set=st,
            unit_primary=UnitType.PIECE,
            allow_syp_purchasing=True,
            allow_usd_purchasing=True,
            allow_syp_sales=True,
            allow_usd_sales=True,
            default_cost_syp=Decimal("100"),
            default_price_syp=Decimal("150"),
        )

        FinSV.set_current_fx(actor=cls.manager, rate_syp_per_usd=Decimal("15000"))

    def setUp(self):
        ok = self.client.login(username="mgr_phase5_ops", password="pw12345")
        self.assertTrue(ok)

    def _create_bill(self, *, status: str) -> None:
        paid = Decimal("100") if status == "paid" else Decimal("0")
        BillingSV.create_bill(
            actor=self.manager,
            provider_id=self.provider.id,
            status=status,
            paid_amount=paid,
            items=[
                {
                    "product_id": self.product.id,
                    "unit_index": 1,
                    "qty_raw": "1",
                    "cost": "100",
                    "currency": "SYP",
                }
            ],
            container=self.store,
            money_container_id=self.cash.id,
            settlement_currency="SYP",
        )

    def test_bills_status_filter_fills_page_before_cursor_cut(self):
        for _ in range(3):
            self._create_bill(status="paid")
        for _ in range(6):
            self._create_bill(status="unpaid")

        url = reverse("billing_api_bills_list")
        page1 = self.client.get(url, {"status": "paid", "page_size": "2"})
        self.assertEqual(page1.status_code, 200, page1.content.decode("utf-8"))
        data1 = page1.json()
        self.assertTrue(data1.get("ok"), data1)
        self.assertEqual(len(data1["items"]), 2)
        self.assertTrue(all((it.get("status") or "").lower() == "paid" for it in data1["items"]))

        next_cursor = data1.get("next_cursor")
        self.assertIsNotNone(next_cursor)

        page2 = self.client.get(url, {"status": "paid", "page_size": "2", "cursor": str(next_cursor)})
        self.assertEqual(page2.status_code, 200, page2.content.decode("utf-8"))
        data2 = page2.json()
        self.assertTrue(data2.get("ok"), data2)
        self.assertEqual(len(data2["items"]), 1)
        self.assertTrue(all((it.get("status") or "").lower() == "paid" for it in data2["items"]))
        self.assertTrue(
            set(it["id"] for it in data1["items"]).isdisjoint(set(it["id"] for it in data2["items"]))
        )

    def test_api_bill_save_does_not_fallback_on_internal_typeerror(self):
        payload = {
            "provider": {"id": self.provider.id},
            "container": "store",
            "money_container_id": self.cash.id,
            "currency_code": "SYP",
            "items": [
                {
                    "product_id": self.product.id,
                    "unit_index": 1,
                    "qty_raw": "1",
                    "cost": "100",
                    "currency": "SYP",
                }
            ],
            "pay": {"status": "unpaid", "paid_amount": "0"},
        }
        url = reverse("billing_api_bill_save")
        with patch("billing.views.SV.create_bill", side_effect=TypeError("internal type mismatch")) as mocked:
            resp = self.client.post(url, data=json.dumps(payload), content_type="application/json")

        self.assertEqual(resp.status_code, 500, resp.content.decode("utf-8"))
        data = resp.json()
        self.assertFalse(data.get("ok", True))
        self.assertIn("save failed", data.get("error", ""))
        mocked.assert_called_once()
