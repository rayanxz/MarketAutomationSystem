from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import AccountProfile
from catalog.models import ProductCollection, ProductSet, Product, UnitType, ProductBarcode, ProductUnitId
from inventory.models import ProductMovement
from stock.models import ProductContainer, StockFifoLayer


class ProductPolicyTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="manager", password="pass")
        AccountProfile.objects.create(user=self.user, role=AccountProfile.Role.MANAGER)
        self.client.force_login(self.user)
        ProductContainer.objects.get_or_create(code="store", defaults={"name": "Store"})
        ProductContainer.objects.get_or_create(code="wh1", defaults={"name": "WH1"})
        ProductContainer.objects.get_or_create(code="wh2", defaults={"name": "WH2"})

    def _create_product(self, *, collection_name: str, set_name: str, product_name: str, active: bool = True) -> Product:
        col, _ = ProductCollection.objects.get_or_create(name=collection_name)
        st, _ = ProductSet.objects.get_or_create(collection=col, name=set_name)
        p = Product.objects.create(
            name=product_name,
            set=st,
            unit_primary=UnitType.PIECE,
            unit_secondary="",
            conversion_factor=None,
            cost=Decimal("1.0000"),
            price=Decimal("2.0000"),
            is_active=active,
        )
        return p

    def _post_product(self, *, name: str, barcode: str | None = None, confirm_reuse: bool = False):
        data = {
            "collection_name": "C",
            "set_name": "S",
            "create_parent": "",
            "name": name,
            "unit_primary": UnitType.PIECE,
            "unit_secondary": "",
            "conversion_factor": "",
            "cost": "1.0000",
            "price": "2.0000",
            "allow_syp_sales": "on",
            "allow_syp_purchasing": "on",
            "allow_usd_sales": "",
            "allow_usd_purchasing": "",
            "default_purchase_currency": "SYP",
            "default_sale_currency": "SYP",
            "default_cost_syp": "1.0000",
            "default_cost_usd": "0.0000",
            "default_price_syp": "2.0000",
            "default_price_usd": "0.0000",
            "notes": "",
        }
        if barcode:
            data["barcodes_u1[]"] = [barcode]
        if confirm_reuse:
            data["confirm_reuse_name"] = "1"
        url = reverse("manager_product_new")
        return self.client.post(url, data=data)

    def test_hard_delete_allowed_when_no_history_and_zero_stock(self):
        prod = self._create_product(collection_name="C1", set_name="S1", product_name="P1")
        url = reverse("manager_product_delete", kwargs={"pk": prod.id})
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(Product.objects.filter(id=prod.id).exists())

    def test_soft_delete_allowed_when_history_and_zero_stock(self):
        prod = self._create_product(collection_name="C2", set_name="S2", product_name="P2")
        ProductMovement.objects.create(
            product=prod,
            qty_primary=Decimal("0"),
            unit_index=1,
            unit_cost=Decimal("0.0000"),
            total_cost=Decimal("0.000"),
            movement_type=ProductMovement.MovementType.ADJUSTMENT,
            source_app="tests",
            source_model="ProductPolicyTests",
            source_id="1",
        )
        url = reverse("manager_product_delete", kwargs={"pk": prod.id})
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 302)
        prod.refresh_from_db()
        self.assertFalse(prod.is_active)

    def test_soft_delete_syncs_identifiers(self):
        prod = self._create_product(collection_name="C5", set_name="S5", product_name="P5")
        ProductBarcode.objects.create(product=prod, unit_index=1, barcode="BC-1", is_active=True)
        ProductUnitId.objects.create(product=prod, unit_index=1, value="UID-1", is_active=True)
        ProductMovement.objects.create(
            product=prod,
            qty_primary=Decimal("0"),
            unit_index=1,
            unit_cost=Decimal("0.0000"),
            total_cost=Decimal("0.000"),
            movement_type=ProductMovement.MovementType.ADJUSTMENT,
            source_app="tests",
            source_model="ProductPolicyTests",
            source_id="2",
        )
        url = reverse("manager_product_delete", kwargs={"pk": prod.id})
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 302)

        prod.refresh_from_db()
        self.assertFalse(prod.is_active)
        self.assertFalse(ProductBarcode.objects.filter(product=prod, is_active=True).exists())
        self.assertFalse(ProductUnitId.objects.filter(product=prod, is_active=True).exists())

    def test_delete_blocked_when_history_and_stock_not_zero(self):
        prod = self._create_product(collection_name="C3", set_name="S3", product_name="P3")
        container = ProductContainer.objects.get(code="store")
        StockFifoLayer.objects.create(
            product=prod,
            container=container,
            qty_remaining=Decimal("1.000"),
            unit_cost=Decimal("1.0000"),
            cost_currency="SYP",
        )
        url = reverse("manager_product_delete", kwargs={"pk": prod.id})
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("delete_blocked=1", resp.url)
        prod.refresh_from_db()
        self.assertTrue(prod.is_active)

    def test_delete_blocked_when_history_and_stock_negative(self):
        prod = self._create_product(collection_name="C4", set_name="S4", product_name="P4")
        container = ProductContainer.objects.get(code="wh1")
        StockFifoLayer.objects.create(
            product=prod,
            container=container,
            qty_remaining=Decimal("-1.000"),
            unit_cost=Decimal("1.0000"),
            cost_currency="SYP",
        )
        url = reverse("manager_product_delete", kwargs={"pk": prod.id})
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("delete_blocked=1", resp.url)
        prod.refresh_from_db()
        self.assertTrue(prod.is_active)

    def test_name_reuse_requires_confirmation(self):
        prod = self._create_product(collection_name="C", set_name="S", product_name="P4", active=False)
        prod.is_active = False
        prod.save(update_fields=["is_active"])

        resp = self._post_product(name="P4")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context["confirm_reuse_name"])
        self.assertEqual(Product.objects.filter(name="P4").count(), 1)

        resp = self._post_product(name="P4", confirm_reuse=True)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Product.objects.filter(name="P4").count(), 2)

    def test_name_duplicate_active_blocked(self):
        self._create_product(collection_name="C", set_name="S", product_name="P5", active=True)
        resp = self._post_product(name="P5")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("name", resp.context["form"].errors)

    def test_barcode_reuse_inactive_allowed_and_active_blocked(self):
        prod_inactive = self._create_product(collection_name="C", set_name="S", product_name="P6", active=False)
        ProductBarcode.objects.create(product=prod_inactive, unit_index=1, barcode="BC-1", is_active=False)

        resp = self._post_product(name="P6-NEW", barcode="BC-1")
        self.assertEqual(resp.status_code, 302)

        prod_active = self._create_product(collection_name="C", set_name="S", product_name="P7", active=True)
        ProductBarcode.objects.create(product=prod_active, unit_index=1, barcode="BC-2", is_active=True)

        resp = self._post_product(name="P7-NEW", barcode="BC-2")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(any("barcodes_u1" in k for k in resp.context["form"].errors.keys()))
