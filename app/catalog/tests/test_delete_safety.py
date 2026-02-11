from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import AccountProfile
from catalog.models import ProductCollection, ProductSet, Product, UnitType


class ProductDeleteSafetyTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="manager", password="pass")
        AccountProfile.objects.create(user=self.user, role=AccountProfile.Role.MANAGER)
        self.client.force_login(self.user)

    def _create_product(self, *, collection_name: str, set_name: str, product_name: str) -> Product:
        col = ProductCollection.objects.create(name=collection_name)
        st = ProductSet.objects.create(collection=col, name=set_name)
        return Product.objects.create(
            name=product_name,
            set=st,
            unit_primary=UnitType.PIECE,
            unit_secondary="",
            conversion_factor=None,
            cost="1.0000",
            price="2.0000",
        )

    def test_api_collection_cascade_delete_archives_products(self):
        prod = self._create_product(collection_name="C1", set_name="S1", product_name="P1")
        col = prod.set.collection

        url = reverse("api_collection_cascade_delete", kwargs={"pk": col.id})
        resp = self.client.post(url)

        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertFalse(data.get("ok"))
        self.assertIn("archive not implemented", data.get("error", ""))

        prod.refresh_from_db()
        self.assertTrue(Product.objects.filter(id=prod.id).exists())
        self.assertFalse(prod.is_active)
        self.assertTrue(ProductSet.objects.filter(id=prod.set_id).exists())
        self.assertTrue(ProductCollection.objects.filter(id=col.id).exists())

    def test_edit_apply_batch_delete_set_and_collection_archives_products(self):
        prod_set = self._create_product(collection_name="C2", set_name="S2", product_name="P2")
        prod_col = self._create_product(collection_name="C3", set_name="S3", product_name="P3")

        url = reverse("edit_apply_batch")
        payload = {
            "ops": [
                {"op": "delete", "type": "set", "id": prod_set.set_id},
                {"op": "delete", "type": "collection", "id": prod_col.set.collection_id},
            ]
        }
        resp = self.client.post(url, data=payload, content_type="application/json")

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data.get("ok"))
        self.assertEqual(len(data.get("results", [])), 2)
        for res in data.get("results", []):
            self.assertFalse(res.get("ok"))
            self.assertIn("archive not implemented", res.get("error", ""))

        prod_set.refresh_from_db()
        prod_col.refresh_from_db()
        self.assertTrue(Product.objects.filter(id=prod_set.id).exists())
        self.assertTrue(Product.objects.filter(id=prod_col.id).exists())
        self.assertFalse(prod_set.is_active)
        self.assertFalse(prod_col.is_active)
        self.assertTrue(ProductSet.objects.filter(id=prod_set.set_id).exists())
        self.assertTrue(ProductCollection.objects.filter(id=prod_col.set.collection_id).exists())
