from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import AccountProfile
from catalog.models import Product, ProductBarcode, ProductCollection, ProductSet, ProductUnitId, UnitType


class ProductLiveValidationApiTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="manager_live_api", password="pass")
        AccountProfile.objects.create(user=self.user, role=AccountProfile.Role.MANAGER)
        self.client.force_login(self.user)

        self.col = ProductCollection.objects.create(name="COL-LIVE-API")
        self.set_obj = ProductSet.objects.create(collection=self.col, name="SET-LIVE-API")

    def _create_product(self, name: str) -> Product:
        return Product.objects.create(
            name=name,
            set=self.set_obj,
            unit_primary=UnitType.PIECE,
            unit_secondary="",
            conversion_factor=None,
            is_active=True,
        )

    def test_name_validate_reports_existing_name(self):
        self._create_product("Live-Name-1")

        resp = self.client.get(reverse("api_product_name_validate"), {"name": "live-name-1"})

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["exists"])

    def test_name_validate_excludes_current_product_in_edit_mode(self):
        product = self._create_product("Live-Name-2")

        resp = self.client.get(
            reverse("api_product_name_validate"),
            {"name": "LIVE-NAME-2", "exclude_pk": product.id},
        )

        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["exists"])

    def test_identifier_validate_detects_existing_unit_id(self):
        product = self._create_product("Live-Id-1")
        ProductUnitId.objects.create(product=product, unit_index=1, value="UID-LIVE-1", is_active=True)

        resp = self.client.get(
            reverse("api_product_identifier_validate"),
            {"kind": "unit_id", "value": "UID-LIVE-1"},
        )

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["exists"])

    def test_identifier_validate_detects_existing_barcode(self):
        product = self._create_product("Live-Bc-1")
        ProductBarcode.objects.create(product=product, unit_index=1, barcode="123456789", is_active=True)

        resp = self.client.get(
            reverse("api_product_identifier_validate"),
            {"kind": "barcode", "value": "123456789"},
        )

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["exists"])

    def test_identifier_validate_excludes_current_product_in_edit_mode(self):
        product = self._create_product("Live-Self-Id")
        ProductUnitId.objects.create(product=product, unit_index=1, value="UID-LIVE-SELF", is_active=True)
        ProductBarcode.objects.create(product=product, unit_index=1, barcode="00998877", is_active=True)

        id_resp = self.client.get(
            reverse("api_product_identifier_validate"),
            {"kind": "unit_id", "value": "UID-LIVE-SELF", "exclude_pk": product.id},
        )
        bc_resp = self.client.get(
            reverse("api_product_identifier_validate"),
            {"kind": "barcode", "value": "00998877", "exclude_pk": product.id},
        )

        self.assertEqual(id_resp.status_code, 200)
        self.assertFalse(id_resp.json()["exists"])
        self.assertEqual(bc_resp.status_code, 200)
        self.assertFalse(bc_resp.json()["exists"])

    def test_identifier_validate_rejects_invalid_barcode_format(self):
        resp = self.client.get(
            reverse("api_product_identifier_validate"),
            {"kind": "barcode", "value": "١٢٣ABC"},
        )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["valid"])
        self.assertFalse(body["exists"])

    def test_identifier_validate_rejects_invalid_unit_id_format(self):
        resp = self.client.get(
            reverse("api_product_identifier_validate"),
            {"kind": "unit_id", "value": "ABC_123"},
        )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["valid"])
        self.assertFalse(body["exists"])
