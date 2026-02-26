from __future__ import annotations

import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import AccountProfile
from catalog.models import ProductCollection, ProductSet, Product, UnitType
from inventory.models import ProductMovement


class StrictHierarchyHardDeleteTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="manager", password="pass")
        AccountProfile.objects.create(user=self.user, role=AccountProfile.Role.MANAGER)
        self.client.force_login(self.user)

    def _create_collection(self, name: str = "C-1") -> ProductCollection:
        return ProductCollection.objects.create(name=name)

    def _create_set(self, *, collection: ProductCollection, name: str = "S-1") -> ProductSet:
        return ProductSet.objects.create(collection=collection, name=name)

    def _create_product(self, *, set_obj: ProductSet, name: str = "P-1") -> Product:
        return Product.objects.create(
            name=name,
            set=set_obj,
            unit_primary=UnitType.PIECE,
            unit_secondary="",
            conversion_factor=None,
        )

    def _add_movement(self, product: Product) -> None:
        ProductMovement.objects.create(
            product=product,
            qty_primary=Decimal("0.000"),
            unit_index=1,
            unit_cost=Decimal("0.0000"),
            total_cost=Decimal("0.000"),
            movement_type=ProductMovement.MovementType.ADJUSTMENT,
            source_app="tests",
            source_model="StrictHierarchyHardDeleteTests",
            source_id=f"mv-{product.id}",
        )

    def test_father_set_delete_empty(self):
        col = self._create_collection("FS-EMPTY-C")
        st = self._create_set(collection=col, name="FS-EMPTY-S")

        resp = self.client.post(
            reverse("edit_apply_batch"),
            data=json.dumps({"ops": [{"op": "delete", "type": "set", "id": st.id}]}),
            content_type="application/json",
        )

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertTrue(data["results"][0]["ok"])
        self.assertFalse(ProductSet.objects.filter(id=st.id).exists())

    def test_father_set_delete_with_deletable_products(self):
        col = self._create_collection("FS-OK-C")
        st = self._create_set(collection=col, name="FS-OK-S")
        p1 = self._create_product(set_obj=st, name="FS-OK-P1")
        p2 = self._create_product(set_obj=st, name="FS-OK-P2")

        resp = self.client.post(
            reverse("edit_apply_batch"),
            data=json.dumps({"ops": [{"op": "delete", "type": "set", "id": st.id}]}),
            content_type="application/json",
        )

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertTrue(data["results"][0]["ok"])
        self.assertFalse(Product.objects.filter(id=p1.id).exists())
        self.assertFalse(Product.objects.filter(id=p2.id).exists())
        self.assertFalse(ProductSet.objects.filter(id=st.id).exists())

    def test_father_set_delete_blocked_when_one_product_has_history(self):
        col = self._create_collection("FS-BLK-C")
        st = self._create_set(collection=col, name="FS-BLK-S")
        p1 = self._create_product(set_obj=st, name="FS-BLK-P1")
        p2 = self._create_product(set_obj=st, name="FS-BLK-P2")
        self._add_movement(p1)

        resp = self.client.post(
            reverse("edit_apply_batch"),
            data=json.dumps({"ops": [{"op": "delete", "type": "set", "id": st.id}]}),
            content_type="application/json",
        )

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertFalse(data["results"][0]["ok"])
        self.assertIn("error", data["results"][0])
        self.assertTrue((data["results"][0]["error"] or "").strip())
        self.assertTrue(Product.objects.filter(id=p1.id).exists())
        self.assertTrue(Product.objects.filter(id=p2.id).exists())
        self.assertTrue(ProductSet.objects.filter(id=st.id).exists())

    def test_father_set_button_disabled_when_flag_false(self):
        col = self._create_collection("FS-BTN-C")
        st = self._create_set(collection=col, name="FS-BTN-S")
        st.can_be_hard_deleted = False
        st.save(update_fields=["can_be_hard_deleted"])

        resp = self.client.get(reverse("api_browser_sets") + f"?cid={col.id}&page=1")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertFalse(data["items"][0]["can_be_hard_deleted"])

    def test_ui_button_labels_renamed(self):
        resp = self.client.get(reverse("manager_collections"))
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode("utf-8")
        self.assertIn("حذف نهائي للزمرة", body)
        self.assertIn("حذف نهائي للمجموعة الأب", body)

    def test_collection_delete_empty(self):
        col = self._create_collection("COL-EMPTY")

        resp = self.client.post(reverse("api_collection_cascade_delete", kwargs={"pk": col.id}))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertFalse(ProductCollection.objects.filter(id=col.id).exists())

    def test_collection_delete_with_deletable_father_sets(self):
        col = self._create_collection("COL-OK")
        s1 = self._create_set(collection=col, name="COL-OK-S1")
        s2 = self._create_set(collection=col, name="COL-OK-S2")
        p1 = self._create_product(set_obj=s1, name="COL-OK-P1")
        p2 = self._create_product(set_obj=s2, name="COL-OK-P2")

        resp = self.client.post(reverse("api_collection_cascade_delete", kwargs={"pk": col.id}))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["deleted_sets"], 2)
        self.assertEqual(data["deleted_products"], 2)
        self.assertFalse(Product.objects.filter(id=p1.id).exists())
        self.assertFalse(Product.objects.filter(id=p2.id).exists())
        self.assertFalse(ProductSet.objects.filter(id=s1.id).exists())
        self.assertFalse(ProductSet.objects.filter(id=s2.id).exists())
        self.assertFalse(ProductCollection.objects.filter(id=col.id).exists())

    def test_collection_delete_blocked_when_one_father_set_not_deletable(self):
        col = self._create_collection("COL-BLK")
        s1 = self._create_set(collection=col, name="COL-BLK-S1")
        s2 = self._create_set(collection=col, name="COL-BLK-S2")
        p1 = self._create_product(set_obj=s1, name="COL-BLK-P1")
        self._create_product(set_obj=s2, name="COL-BLK-P2")
        self._add_movement(p1)

        resp = self.client.post(reverse("api_collection_cascade_delete", kwargs={"pk": col.id}))
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertFalse(data["ok"])
        self.assertIn("error", data)
        self.assertTrue((data["error"] or "").strip())
        self.assertTrue(ProductCollection.objects.filter(id=col.id).exists())

    def test_collection_button_disabled_when_flag_false(self):
        col = self._create_collection("COL-BTN")
        col.can_be_hard_deleted = False
        col.save(update_fields=["can_be_hard_deleted"])

        resp = self.client.get(reverse("api_browser_collections") + "?page=1")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        item = next(x for x in data["items"] if x["id"] == col.id)
        self.assertFalse(item["can_be_hard_deleted"])

    def test_product_first_movement_propagates_flags(self):
        col = self._create_collection("PROP-C")
        st = self._create_set(collection=col, name="PROP-S")
        p = self._create_product(set_obj=st, name="PROP-P")
        self.assertTrue(st.can_be_hard_deleted)
        self.assertTrue(col.can_be_hard_deleted)

        self._add_movement(p)
        st.refresh_from_db()
        col.refresh_from_db()
        self.assertFalse(st.can_be_hard_deleted)
        self.assertFalse(col.can_be_hard_deleted)

    def test_backend_blocks_even_if_frontend_bypassed(self):
        col = self._create_collection("BYP-C")
        st = self._create_set(collection=col, name="BYP-S")
        p = self._create_product(set_obj=st, name="BYP-P")
        self._add_movement(p)

        # Simulate a bypass/tamper where flag is forced back to True.
        ProductSet.objects.filter(id=st.id).update(can_be_hard_deleted=True)
        ProductCollection.objects.filter(id=col.id).update(can_be_hard_deleted=True)

        resp = self.client.post(
            reverse("edit_apply_batch"),
            data=json.dumps({"ops": [{"op": "delete", "type": "set", "id": st.id}]}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data["results"][0]["ok"])
        self.assertIn("error", data["results"][0])
        self.assertTrue((data["results"][0]["error"] or "").strip())
        self.assertTrue(ProductSet.objects.filter(id=st.id).exists())


