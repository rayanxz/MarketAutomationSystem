from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import AccountProfile
from catalog.models import Product, ProductBarcode, ProductCollection, ProductSet, ProductUnitId, UnitType


class ProductIdentifierValidationRulesTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="manager_ident_rules", password="pass")
        AccountProfile.objects.create(user=self.user, role=AccountProfile.Role.MANAGER)
        self.client.force_login(self.user)

        self.col = ProductCollection.objects.create(name="COL-ID-RULES")
        self.set_obj = ProductSet.objects.create(collection=self.col, name="SET-ID-RULES")

    def _new_payload(self, *, name: str) -> dict:
        return {
            "collection_name": self.col.name,
            "set_name": self.set_obj.name,
            "create_parent": "",
            "name": name,
            "unit_primary": UnitType.PIECE,
            "unit_secondary": "",
            "conversion_factor": "",
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

    def _create_product(self, name: str) -> Product:
        return Product.objects.create(
            name=name,
            set=self.set_obj,
            unit_primary=UnitType.PIECE,
            unit_secondary="",
            conversion_factor=None,
            is_active=True,
        )

    def test_create_accepts_valid_code_and_numeric_barcode(self):
        payload = self._new_payload(name="P-ID-VALID")
        payload["unit_primary_ids[]"] = ["PRD-001"]
        payload["barcodes_u1[]"] = ["001234567890"]

        resp = self.client.post(reverse("manager_product_new"), data=payload)

        self.assertEqual(resp.status_code, 302)
        p = Product.objects.get(name="P-ID-VALID")
        self.assertTrue(ProductUnitId.objects.filter(product=p, value="PRD-001").exists())
        self.assertTrue(ProductBarcode.objects.filter(product=p, barcode="001234567890").exists())

    def test_create_rejects_arabic_digits_in_barcode(self):
        payload = self._new_payload(name="P-ID-BAR-AR")
        payload["barcodes_u1[]"] = ["١٢٣٤٥٦"]

        resp = self.client.post(reverse("manager_product_new"), data=payload)

        self.assertEqual(resp.status_code, 200)
        self.assertIn("barcodes_u1", resp.context["form"].errors)
        self.assertFalse(Product.objects.filter(name="P-ID-BAR-AR").exists())

    def test_create_rejects_non_allowed_chars_in_code(self):
        payload = self._new_payload(name="P-ID-CODE-BAD")
        payload["unit_primary_ids[]"] = ["ABC_123"]

        resp = self.client.post(reverse("manager_product_new"), data=payload)

        self.assertEqual(resp.status_code, 200)
        self.assertIn("unit_primary_ids", resp.context["form"].errors)
        self.assertFalse(Product.objects.filter(name="P-ID-CODE-BAD").exists())

    def test_edit_rejects_invalid_code_and_barcode_formats(self):
        product = self._create_product("P-ID-EDIT")
        payload = self._new_payload(name=product.name)
        payload["unit_primary_ids[]"] = ["منتج1"]
        payload["barcodes_u1[]"] = ["ABC123"]

        resp = self.client.post(reverse("manager_product_edit", kwargs={"pk": product.id}), data=payload)

        self.assertEqual(resp.status_code, 200)
        self.assertIn("unit_primary_ids", resp.context["form"].errors)
        self.assertIn("barcodes_u1", resp.context["form"].errors)
