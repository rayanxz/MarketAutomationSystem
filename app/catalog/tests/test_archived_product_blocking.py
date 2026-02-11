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
from inventory import services as InvSV
from stock import services as StockSV
from stock.models import ProductContainer, StockFifoLayer


class ArchivedProductBlockingTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="manager", password="pass")
        AccountProfile.objects.create(user=self.user, role=AccountProfile.Role.MANAGER)
        self.client.force_login(self.user)

        self.collection = ProductCollection.objects.create(name="C1")
        self.set_obj = ProductSet.objects.create(collection=self.collection, name="S1")

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

    def test_archived_product_not_in_billing_search(self):
        prod = self._create_product(name="Archived-1", active=False)
        url = reverse("billing_api_products_search")
        resp = self.client.get(url, {"q": prod.name, "mode": "name"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data.get("items"), [])

    def test_archived_product_cannot_be_sold_in_pos(self):
        prod = self._create_product(name="Archived-Pos", active=False)
        url = reverse("pos:api_bill_save")
        payload = {
            "parked": True,
            "pay_status": "full",
            "total_amount": "0",
            "paid_amount": "0",
            "settlement_mode": "split",
            "rows": [
                {
                    "product_id": prod.id,
                    "qty": "1",
                    "uom_index": 1,
                    "unit_price": "1.000",
                    "currency": "SYP",
                    "disc_amount": "0",
                    "disc_pct": "0",
                }
            ],
        }
        resp = self.client.post(
            url,
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json().get("error"), "INVALID_PRODUCT")

    def test_archived_product_cannot_be_used_in_provider_return(self):
        prod = self._create_product(name="Archived-PR", active=False)
        provider = Provider.objects.create(name="Prov1")
        container, _ = ProductContainer.objects.get_or_create(
            code="store",
            defaults={"name": "Store", "is_store": True, "is_active": True},
        )
        with self.assertRaises(ValidationError):
            BillingSV.create_return(
                actor=self.user,
                provider_id=provider.id,
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
                container=container,
            )

    def test_archived_product_cannot_be_transferred(self):
        prod = self._create_product(name="Archived-TX", active=False)
        c_from = ProductContainer.objects.create(name="From", code="from", is_store=False, is_active=True)
        c_to = ProductContainer.objects.create(name="To", code="to", is_store=False, is_active=True)
        batch = StockFifoLayer.objects.create(
            product=prod,
            container=c_from,
            qty_remaining=Decimal("5.000"),
            unit_cost=Decimal("1.0000"),
            cost_currency="SYP",
        )
        with self.assertRaises(ValidationError):
            StockSV.transfer_from_batch(
                actor=self.user,
                batch=batch,
                to_container=c_to,
                qty_primary=Decimal("1.000"),
            )

    def test_service_layer_blocks_archived_product(self):
        prod = self._create_product(name="Archived-SVC", active=False)
        container = ProductContainer.objects.create(name="Store2", code="store2", is_store=False, is_active=True)
        with self.assertRaises(ValidationError):
            InvSV.record_movement(
                actor=self.user,
                product=prod,
                unit_index=1,
                qty_primary=Decimal("1.000"),
                unit_cost=Decimal("1.0000"),
                movement_type="adjustment",
                source_app="tests",
                source_model="ArchivedProductBlockingTests",
                source_id="1",
                container=container,
            )
