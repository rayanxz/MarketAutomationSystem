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

    def _edit_payload(self, product: Product, **overrides) -> dict:
        payload = {
            "collection_name": product.set.collection.name,
            "set_name": product.set.name,
            "create_parent": "",
            "name": product.name,
            "unit_primary": product.unit_primary,
            "unit_secondary": product.unit_secondary or "",
            "conversion_factor": str(product.conversion_factor or ""),
            "allow_syp_sales": "on" if product.allow_syp_sales else "",
            "allow_syp_purchasing": "on" if product.allow_syp_purchasing else "",
            "allow_usd_sales": "on" if product.allow_usd_sales else "",
            "allow_usd_purchasing": "on" if product.allow_usd_purchasing else "",
            "default_purchase_currency": product.default_purchase_currency or "SYP",
            "default_sale_currency": product.default_sale_currency or "SYP",
            "default_cost_syp": str(product.default_cost_syp),
            "default_cost_usd": str(product.default_cost_usd),
            "default_price_syp": str(product.default_price_syp),
            "default_price_usd": str(product.default_price_usd),
            "notes": product.notes or "",
        }
        payload.update(overrides)
        return payload

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
        self.assertNotContains(resp, "Ø¥Ù†Ø´Ø§Ø¡ Ù…Ø¬Ù…ÙˆØ¹Ø© Ø£Ø¨ Ø¬Ø¯ÙŠØ¯Ø©")

    def test_create_mode_still_shows_create_parent_checkbox(self):
        resp = self.client.get(reverse("manager_product_new"))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'name="create_parent"', html=False)

    def test_edit_without_history_still_shows_create_parent_checkbox(self):
        product = self._create_product("P-NO-HIST")

        resp = self.client.get(reverse("manager_product_edit", args=[product.id]))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'name="create_parent"', html=False)

    def test_edit_with_history_allows_non_locked_field_save(self):
        product = self._create_product("P-HIST-ALLOWED")
        self._add_history(product)

        payload = self._edit_payload(
            product,
            allow_syp_sales="on",
            allow_syp_purchasing="on",
            default_purchase_currency="SYP",
            default_sale_currency="SYP",
            default_cost_syp="7.5000",
            default_price_syp="11.2500",
            notes="updated note",
        )
        payload["unit_primary_ids[]"] = ["UID-1"]
        payload["barcodes_u1[]"] = ["1234567890"]

        resp = self.client.post(reverse("manager_product_edit", args=[product.id]), data=payload)

        self.assertEqual(resp.status_code, 302)
        product.refresh_from_db()
        self.assertEqual(product.default_cost_syp, Decimal("7.5000"))
        self.assertEqual(product.default_price_syp, Decimal("11.2500"))
        self.assertEqual(product.notes, "updated note")

    def test_edit_with_history_blocks_locked_change_with_clear_message(self):
        product = self._create_product("P-HIST-LOCKED-MSG")
        self._add_history(product)

        payload = self._edit_payload(product, name="P-HIST-LOCKED-MSG-NEW")
        resp = self.client.post(reverse("manager_product_edit", args=[product.id]), data=payload)

        self.assertEqual(resp.status_code, 200)
        self.assertIn("name", resp.context["form"].errors)
        self.assertContains(resp, "\u0647\u0630\u0627 \u0627\u0644\u062d\u0642\u0644 \u0645\u0642\u0641\u0644 \u0628\u0639\u062f \u0648\u062c\u0648\u062f \u062d\u0631\u0643\u0627\u062a \u0639\u0644\u0649 \u0627\u0644\u0645\u0646\u062a\u062c.")
        product.refresh_from_db()
        self.assertEqual(product.name, "P-HIST-LOCKED-MSG")
