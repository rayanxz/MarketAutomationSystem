from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import AccountProfile
from catalog.models import Product, ProductCollection, ProductSet, UnitType
from inventory.models import ProductMovement


class ProductEditHistoryUiTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="manager_hist_ui", password="pass")
        AccountProfile.objects.create(user=self.user, role=AccountProfile.Role.MANAGER)
        self.client.force_login(self.user)

        self.col = ProductCollection.objects.create(name="COL-HIST-UI")
        self.set_obj = ProductSet.objects.create(collection=self.col, name="SET-HIST-UI")

    def _create_product(self, name: str) -> Product:
        return Product.objects.create(
            name=name,
            set=self.set_obj,
            unit_primary=UnitType.PIECE,
            unit_secondary="",
            conversion_factor=None,
            is_active=True,
        )

    def _add_history(self, product: Product) -> None:
        ProductMovement.objects.create(
            product=product,
            qty_primary=Decimal("0"),
            unit_index=1,
            unit_cost=Decimal("0.0000"),
            total_cost=Decimal("0.000"),
            movement_type=ProductMovement.MovementType.ADJUSTMENT,
            source_app="tests",
            source_model="ProductEditHistoryUiTests",
            source_id=str(product.pk),
        )

    def test_edit_with_history_hides_message_and_create_parent_checkbox(self):
        product = self._create_product("P-HIST-LOCK")
        self._add_history(product)

        resp = self.client.get(reverse("manager_product_edit", args=[product.id]))

        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(
            resp,
            "This product has history; core fields are locked. Archive + create a new product if you need different units/collection/set/name.",
        )
        self.assertNotContains(resp, 'name="create_parent"', html=False)
        self.assertNotContains(resp, "إنشاء مجموعة أب جديدة")

    def test_create_mode_still_shows_create_parent_checkbox(self):
        resp = self.client.get(reverse("manager_product_new"))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'name="create_parent"', html=False)
        self.assertContains(resp, "إنشاء مجموعة أب جديدة")

    def test_edit_without_history_still_shows_create_parent_checkbox(self):
        product = self._create_product("P-NO-HIST")

        resp = self.client.get(reverse("manager_product_edit", args=[product.id]))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'name="create_parent"', html=False)
        self.assertContains(resp, "إنشاء مجموعة أب جديدة")
