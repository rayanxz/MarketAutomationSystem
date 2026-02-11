from __future__ import annotations

import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from accounts.models import AccountProfile
from billing import services as BillingSV
from billing.models import Provider
from catalog.models import ProductCollection, ProductSet, Product, UnitType
from financials import services as FinSV
from financials.models import Currency, MoneyContainer, MoneyContainerCurrency
from stock.models import ProductContainer


class BillingArchivedProductBlockingTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="manager", password="pass")
        AccountProfile.objects.create(user=self.user, role=AccountProfile.Role.MANAGER)
        self.client.force_login(self.user)

        self.syp, _ = Currency.objects.get_or_create(
            code="SYP",
            defaults={"name": "SYP", "decimals": 0, "is_active": True},
        )
        self.usd, _ = Currency.objects.get_or_create(
            code="USD",
            defaults={"name": "USD", "decimals": 2, "is_active": True},
        )

        FinSV.set_current_fx(actor=self.user, rate_syp_per_usd=Decimal("10000"))

        self.cash = MoneyContainer.objects.create(
            name="Cash",
            container_type=MoneyContainer.ContainerType.DRAWER,
            is_active=True,
            created_by=self.user,
            balance_syp=Decimal("0"),
            balance_usd=Decimal("0"),
        )
        MoneyContainerCurrency.objects.get_or_create(
            container=self.cash,
            currency=self.syp,
            defaults={"is_enabled": True},
        )
        MoneyContainerCurrency.objects.get_or_create(
            container=self.cash,
            currency=self.usd,
            defaults={"is_enabled": True},
        )

        self.store, _ = ProductContainer.objects.get_or_create(
            code="store",
            defaults={"name": "Store", "is_store": True, "is_active": True},
        )

        self.provider = Provider.objects.create(name="Prov1")
        col = ProductCollection.objects.create(name="C1")
        self.set_obj = ProductSet.objects.create(collection=col, name="S1")

    def _create_product(self, *, name: str, active: bool) -> Product:
        return Product.objects.create(
            name=name,
            set=self.set_obj,
            unit_primary=UnitType.PIECE,
            unit_secondary="",
            conversion_factor=None,
            cost=Decimal("1.0000"),
            price=Decimal("2.0000"),
            is_active=active,
        )

    def test_api_products_search_excludes_archived(self):
        prod = self._create_product(name="Archived-1", active=False)
        url = reverse("billing_api_products_search")
        resp = self.client.get(url, {"q": prod.name, "mode": "name"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json().get("items"), [])

    def test_api_bill_save_rejects_archived_product(self):
        prod = self._create_product(name="Archived-2", active=False)
        url = reverse("billing_api_bill_save")
        payload = {
            "provider": {"id": self.provider.id},
            "container": "store",
            "money_container_id": self.cash.id,
            "currency_code": "SYP",
            "items": [
                {
                    "product_id": prod.id,
                    "unit_index": 1,
                    "qty_raw": "1",
                    "cost": "1.0000",
                    "price": "2.0000",
                    "currency": "SYP",
                }
            ],
            "pay": {"status": "unpaid", "paid_amount": "0"},
        }
        resp = self.client.post(
            url,
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 500)
        self.assertFalse(resp.json().get("ok"))

    def test_create_bill_rejects_archived_product(self):
        prod = self._create_product(name="Archived-3", active=False)
        with self.assertRaises(ValidationError):
            BillingSV.create_bill(
                actor=self.user,
                provider_id=self.provider.id,
                status="unpaid",
                paid_amount=Decimal("0.000"),
                items=[
                    {
                        "product_id": prod.id,
                        "unit_index": 1,
                        "qty_raw": "1",
                        "cost": "1.0000",
                        "price": "2.0000",
                        "currency": "SYP",
                    }
                ],
                container=self.store,
                money_container_id=self.cash.id,
                settlement_currency="SYP",
            )

    def test_create_return_rejects_archived_product(self):
        prod = self._create_product(name="Archived-4", active=False)
        with self.assertRaises(ValidationError):
            BillingSV.create_return(
                actor=self.user,
                provider_id=self.provider.id,
                status="unpaid",
                paid_amount=Decimal("0.000"),
                items=[
                    {
                        "product_id": prod.id,
                        "unit_index": 1,
                        "qty_raw": "1.000",
                        "cost": "1.0000",
                    }
                ],
                container=self.store,
            )

