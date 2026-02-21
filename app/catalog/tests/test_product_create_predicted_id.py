from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import AccountProfile
from catalog.models import Product, ProductCollection, ProductSet, UnitType


class ProductCreatePredictedIdTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="manager_pred_id", password="pass")
        AccountProfile.objects.create(user=self.user, role=AccountProfile.Role.MANAGER)
        self.client.force_login(self.user)

        self.col = ProductCollection.objects.create(name="COL-PRED")
        self.set_obj = ProductSet.objects.create(collection=self.col, name="SET-PRED")

    def _create_product(self, name: str) -> Product:
        return Product.objects.create(
            name=name,
            set=self.set_obj,
            unit_primary=UnitType.PIECE,
            unit_secondary="",
            conversion_factor=None,
            cost=Decimal("1.0000"),
            price=Decimal("2.0000"),
            is_active=True,
        )

    def test_create_page_shows_predicted_next_id(self):
        p = self._create_product("EXISTING-P1")
        resp = self.client.get(reverse("manager_product_new"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, f"رمز المنتج: {p.id + 1}")

    def test_create_page_validation_error_still_shows_predicted_next_id(self):
        p = self._create_product("EXISTING-P2")
        resp = self.client.post(reverse("manager_product_new"), data={})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, f"رمز المنتج: {p.id + 1}")

    def test_edit_mode_still_shows_actual_product_id(self):
        p = self._create_product("EDIT-ID-PRODUCT")
        resp = self.client.get(reverse("manager_product_edit", args=[p.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, f"رمز المنتج: {p.id}")

    def test_create_success_uses_predicted_id_in_normal_case(self):
        p = self._create_product("EXISTING-P3")
        predicted = p.id + 1
        resp_get = self.client.get(reverse("manager_product_new"))
        self.assertEqual(resp_get.status_code, 200)
        self.assertContains(resp_get, f"رمز المنتج: {predicted}")

        resp_post = self.client.post(
            reverse("manager_product_new"),
            data={
                "collection_name": self.col.name,
                "set_name": self.set_obj.name,
                "name": "CREATED-FROM-PREDICTED",
                "unit_primary": UnitType.PIECE,
                "unit_secondary": "",
                "conversion_factor": "",
                "cost": "",
                "price": "",
                "default_cost_syp": "",
                "default_cost_usd": "",
                "default_price_syp": "",
                "default_price_usd": "",
                "notes": "",
            },
        )
        self.assertEqual(resp_post.status_code, 302)
        created = Product.objects.get(name="CREATED-FROM-PREDICTED")
        self.assertEqual(created.id, predicted)

    def test_predicted_id_tracks_sequence_after_highest_id_delete(self):
        p1 = self._create_product("GAP-P1")
        p2 = self._create_product("GAP-P2")
        p3 = self._create_product("GAP-P3")
        self.assertEqual([p1.id, p2.id, p3.id], [1, 2, 3])

        Product.objects.filter(id=p3.id).delete()

        resp_get = self.client.get(reverse("manager_product_new"))
        self.assertEqual(resp_get.status_code, 200)
        self.assertContains(resp_get, "رمز المنتج: 4")

        resp_post = self.client.post(
            reverse("manager_product_new"),
            data={
                "collection_name": self.col.name,
                "set_name": self.set_obj.name,
                "name": "CREATED-AFTER-GAP",
                "unit_primary": UnitType.PIECE,
                "unit_secondary": "",
                "conversion_factor": "",
                "cost": "",
                "price": "",
                "default_cost_syp": "",
                "default_cost_usd": "",
                "default_price_syp": "",
                "default_price_usd": "",
                "notes": "",
            },
        )
        self.assertEqual(resp_post.status_code, 302)
        created = Product.objects.get(name="CREATED-AFTER-GAP")
        self.assertEqual(created.id, 4)
