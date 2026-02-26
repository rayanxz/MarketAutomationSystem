from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import AccountProfile
from catalog.models import ProductCollection, ProductSet, Product, UnitType, ProductBarcode


class CatalogSearchArchivedTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="manager", password="pass")
        AccountProfile.objects.create(user=self.user, role=AccountProfile.Role.MANAGER)
        self.client.force_login(self.user)

        col = ProductCollection.objects.create(name="C1")
        self.set_obj = ProductSet.objects.create(collection=col, name="S1")

    def _create_product(self, *, name: str, active: bool) -> Product:
        return Product.objects.create(
            name=name,
            set=self.set_obj,
            unit_primary=UnitType.PIECE,
            unit_secondary="",
            conversion_factor=None,
            is_active=active,
        )

    def test_api_product_search_excludes_archived_by_name(self):
        archived = self._create_product(name="Archived Name", active=False)
        active = self._create_product(name="Active Name", active=True)

        url = reverse("api_product_search")
        resp = self.client.get(url, {"q": archived.name, "mode": "name", "scope": "product"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json().get("items"), [])

        resp = self.client.get(url, {"q": active.name, "mode": "name", "scope": "product"})
        self.assertEqual(resp.status_code, 200)
        items = resp.json().get("items") or []
        self.assertTrue(any(i.get("id") == active.id for i in items))

    def test_api_product_search_excludes_archived_by_barcode(self):
        archived = self._create_product(name="Archived Barcode", active=False)
        ProductBarcode.objects.create(product=archived, unit_index=1, barcode="BC-ARCH", is_active=False)

        url = reverse("api_product_search")
        resp = self.client.get(url, {"q": "BC-ARCH", "mode": "barcode"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json().get("items"), [])

    def test_api_product_search_show_disabled_includes_archived_by_name(self):
        archived = self._create_product(name="Archived Visible", active=False)
        self._create_product(name="Active Visible", active=True)

        url = reverse("api_product_search")
        resp = self.client.get(
            url,
            {"q": archived.name, "mode": "name", "scope": "product", "show_disabled": 1},
        )
        self.assertEqual(resp.status_code, 200)
        items = resp.json().get("items") or []
        self.assertTrue(any(i.get("id") == archived.id for i in items))

    def test_api_product_search_show_disabled_includes_archived_by_barcode(self):
        archived = self._create_product(name="Archived Barcode Visible", active=False)
        ProductBarcode.objects.create(product=archived, unit_index=1, barcode="BC-ARCH-V", is_active=False)

        url = reverse("api_product_search")
        resp = self.client.get(url, {"q": "BC-ARCH-V", "mode": "barcode", "show_disabled": 1})
        self.assertEqual(resp.status_code, 200)
        items = resp.json().get("items") or []
        self.assertTrue(any(i.get("id") == archived.id for i in items))


