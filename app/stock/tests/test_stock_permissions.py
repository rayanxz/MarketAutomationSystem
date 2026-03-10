from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import AccountProfile
from catalog.models import Product, ProductCollection, ProductSet, UnitType
from stock.models import ProductContainer


class StockPermissionsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.manager = user_model.objects.create_user(username="stock_mgr", password="123")
        cls.cashier = user_model.objects.create_user(username="stock_cash", password="123")

        manager_profile, _ = AccountProfile.objects.get_or_create(user=cls.manager)
        manager_profile.role = AccountProfile.Role.MANAGER
        manager_profile.save(update_fields=["role"])

        cashier_profile, _ = AccountProfile.objects.get_or_create(user=cls.cashier)
        cashier_profile.role = AccountProfile.Role.CASHIER
        cashier_profile.save(update_fields=["role"])

        cls.container, _ = ProductContainer.objects.get_or_create(
            code="store",
            defaults={
                "name": "Store",
                "is_store": True,
                "is_active": True,
            },
        )
        if not cls.container.is_active:
            cls.container.is_active = True
            cls.container.save(update_fields=["is_active"])

        collection = ProductCollection.objects.create(name="Stock Permissions Collection")
        set_obj = ProductSet.objects.create(collection=collection, name="Stock Permissions Set")
        cls.product = Product.objects.create(
            name="Stock Permissions Product",
            set=set_obj,
            unit_primary=UnitType.PIECE,
            stock_qty=Decimal("0"),
            allow_syp_purchasing=True,
            allow_usd_purchasing=True,
            allow_syp_sales=True,
            allow_usd_sales=True,
            default_cost_syp=Decimal("10"),
            default_cost_usd=Decimal("1"),
            default_price_syp=Decimal("20"),
            default_price_usd=Decimal("2"),
        )

    def test_manager_can_access_stock_views_and_apis(self):
        self.client.force_login(self.manager)

        self.assertEqual(self.client.get(reverse("stock:stock_list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("stock:stock_move")).status_code, 200)

        self.assertEqual(
            self.client.get(
                reverse("stock:api_product_search"),
                {"q": "Stock Permissions", "mode": "name"},
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(
                reverse("stock:api_product_stock"),
                {"product_id": self.product.id},
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(
                reverse("stock:api_product_batches"),
                {"product_id": self.product.id, "container": self.container.code},
            ).status_code,
            200,
        )

    def test_cashier_forbidden_from_stock_views_and_apis(self):
        self.client.force_login(self.cashier)

        self.assertEqual(self.client.get(reverse("stock:stock_list")).status_code, 403)
        self.assertEqual(self.client.get(reverse("stock:stock_move")).status_code, 403)

        self.assertEqual(
            self.client.get(
                reverse("stock:api_product_search"),
                {"q": "Stock Permissions", "mode": "name"},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(
                reverse("stock:api_product_stock"),
                {"product_id": self.product.id},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(
                reverse("stock:api_product_batches"),
                {"product_id": self.product.id, "container": self.container.code},
            ).status_code,
            403,
        )
