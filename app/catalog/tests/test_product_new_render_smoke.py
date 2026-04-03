from __future__ import annotations

from decimal import Decimal
import re

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import AccountProfile
from catalog.models import Product, ProductBarcode, ProductCollection, ProductSet, ProductUnitId, UnitType
from core.templatetags.formatting import human_number
from inventory.models import ProductMovement


class ProductNewRenderSmokeTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="manager_render_smoke", password="pass")
        AccountProfile.objects.create(user=self.user, role=AccountProfile.Role.MANAGER)
        self.client.force_login(self.user)

        self.col = ProductCollection.objects.create(name="COL-RENDER-SMOKE")
        self.set_obj = ProductSet.objects.create(collection=self.col, name="SET-RENDER-SMOKE")

    def _create_product(self, name: str) -> Product:
        return Product.objects.create(
            name=name,
            set=self.set_obj,
            unit_primary=UnitType.PIECE,
            unit_secondary="",
            conversion_factor=None,
            is_active=True,
        )

    def test_create_mode_renders(self):
        resp = self.client.get(reverse("manager_product_new"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'class="pn-wrap"', html=False)
        self.assertContains(resp, 'id="localIdentifierSearchInput"', html=False)
        self.assertContains(resp, 'id="localIdentifierSearchButton"', html=False)
        html = resp.content.decode("utf-8")
        self.assertRegex(html, r'id="localIdentifierSearchInput"[^>]*\bdisabled\b')
        self.assertRegex(html, r'id="localIdentifierSearchButton"[^>]*\bdisabled\b')

    def test_create_mode_cost_price_inputs_do_not_render_native_min_constraints(self):
        resp = self.client.get(reverse("manager_product_new"))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode("utf-8")
        for name in (
            "default_cost_syp",
            "default_cost_usd",
            "default_price_syp",
            "default_price_usd",
        ):
            self.assertIsNone(
                re.search(rf'<input[^>]*name="{re.escape(name)}"[^>]*\\bmin=', html),
                msg=f"{name} should not render a native min attribute",
            )

    def test_create_mode_renders_currency_group_error_slots(self):
        resp = self.client.get(reverse("manager_product_new"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-live-error-for="purchase_currency_group"', html=False)
        self.assertContains(resp, 'data-live-error-for="sale_currency_group"', html=False)

    def test_edit_mode_without_history_renders(self):
        product = self._create_product("P-RENDER-NO-HIST")
        resp = self.client.get(reverse("manager_product_edit", args=[product.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'class="pn-wrap"', html=False)
        html = resp.content.decode("utf-8")
        self.assertNotRegex(html, r'id="localIdentifierSearchInput"[^>]*\bdisabled\b')
        self.assertNotRegex(html, r'id="localIdentifierSearchButton"[^>]*\bdisabled\b')

    def test_edit_mode_latest_cost_price_use_global_human_number_format(self):
        product = self._create_product("P-RENDER-LATEST-FMT")
        product.latest_cost_syp = Decimal("1234567.8000")
        product.latest_cost_usd = Decimal("12.3400")
        product.latest_price_syp = Decimal("2500000.0000")
        product.latest_price_usd = Decimal("5.5000")
        product.save(
            update_fields=[
                "latest_cost_syp",
                "latest_cost_usd",
                "latest_price_syp",
                "latest_price_usd",
            ]
        )

        resp = self.client.get(reverse("manager_product_edit", args=[product.id]))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode("utf-8")
        self.assertIn(f'value="{human_number(product.latest_cost_syp)}"', html)
        self.assertIn(f'value="{human_number(product.latest_cost_usd)}"', html)
        self.assertIn(f'value="{human_number(product.latest_price_syp)}"', html)
        self.assertIn(f'value="{human_number(product.latest_price_usd)}"', html)

    def test_edit_mode_with_history_renders(self):
        product = self._create_product("P-RENDER-HIST")
        ProductMovement.objects.create(
            product=product,
            qty_primary=Decimal("0"),
            unit_index=1,
            unit_cost=Decimal("0.0000"),
            total_cost=Decimal("0.000"),
            movement_type=ProductMovement.MovementType.ADJUSTMENT,
            source_app="tests",
            source_model="ProductNewRenderSmokeTests",
            source_id=str(product.pk),
        )
        resp = self.client.get(reverse("manager_product_edit", args=[product.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'class="pn-wrap"', html=False)

    def test_edit_mode_renders_with_identifier_highlight_query(self):
        product = self._create_product("P-RENDER-HL")
        ProductUnitId.objects.create(product=product, unit_index=1, value="ID-HL-1", is_active=True)
        ProductBarcode.objects.create(product=product, unit_index=1, barcode="BC-HL-1", is_active=True)

        resp = self.client.get(
            reverse("manager_product_edit", args=[product.id]),
            {"highlight_type": "barcode", "highlight_value": "BC-HL-1"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'class="pn-wrap"', html=False)

    def test_edit_mode_with_history_renders_with_identifier_highlight_query(self):
        product = self._create_product("P-RENDER-HIST-HL")
        ProductBarcode.objects.create(product=product, unit_index=1, barcode="BC-HL-HIST", is_active=True)
        ProductMovement.objects.create(
            product=product,
            qty_primary=Decimal("0"),
            unit_index=1,
            unit_cost=Decimal("0.0000"),
            total_cost=Decimal("0.000"),
            movement_type=ProductMovement.MovementType.ADJUSTMENT,
            source_app="tests",
            source_model="ProductNewRenderSmokeTests",
            source_id=str(product.pk),
        )
        resp = self.client.get(
            reverse("manager_product_edit", args=[product.id]),
            {"highlight_type": "barcode", "highlight_value": "BC-HL-HIST"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'class="pn-wrap"', html=False)
