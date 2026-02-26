# billing/tests/test_multicurrency_guard.py
from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import AccountProfile
from billing import services as BillingSV
from billing.models import BillItem, Provider
from catalog.models import Product, ProductCollection, ProductSet, UnitType
from financials.models import Currency, MoneyContainer
from financials import services as FinSV
from inventory.models import ProductMovement
from stock.models import ProductContainer


class MultiCurrencyGuardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.actor = User.objects.create_user(username="mgr", password="123")

        prof, _ = AccountProfile.objects.get_or_create(user=cls.actor)
        prof.role = AccountProfile.Role.MANAGER
        prof.save(update_fields=["role"])

        Currency.objects.get_or_create(
            code="SYP",
            defaults={"name": "Syrian Pound", "decimals": 0, "is_active": True},
        )
        Currency.objects.get_or_create(
            code="USD",
            defaults={"name": "US Dollar", "decimals": 2, "is_active": True},
        )

        cls.store = ProductContainer.objects.filter(code="store").first()
        if not cls.store:
            cls.store = ProductContainer.objects.create(
                name="Main Store",
                code="store",
                is_store=True,
                is_active=True,
            )

        cls.cash = MoneyContainer.objects.create(
            name="Test Cash Drawer",
            container_type=MoneyContainer.ContainerType.DRAWER,
            is_active=True,
            created_by=cls.actor,
        )

        cls.provider = Provider.objects.create(name="Test Provider")

        col = ProductCollection.objects.create(name="Test Collection")
        cls.prod_set = ProductSet.objects.create(collection=col, name="Test Set")

        FinSV.set_current_fx(actor=cls.actor, rate_syp_per_usd=Decimal("15000"))

    def test_purchase_bill_allows_usd_when_product_allows_it(self):
        prod = Product.objects.create(
            name="USD Purch",
            set=self.prod_set,
            unit_primary=UnitType.PIECE,
            allow_syp_purchasing=False,
            allow_usd_purchasing=True,
            allow_syp_sales=False,
            allow_usd_sales=True,
            default_cost_usd=Decimal("2"),
            default_price_usd=Decimal("3"),
        )

        bill = BillingSV.create_bill(
            actor=self.actor,
            provider_id=self.provider.id,
            status="unpaid",
            paid_amount=Decimal("0"),
            items=[
                {
                    "product_id": prod.id,
                    "unit_index": 1,
                    "qty_raw": "1",
                    "cost": "2",
                    "currency": "USD",
                    "price_syp": "",
                    "price_usd": "",
                }
            ],
            money_container_id=self.cash.id,
            settlement_currency="USD",
        )

        item = BillItem.objects.get(bill=bill)
        self.assertEqual(item.currency, "USD")
        self.assertGreater(ProductMovement.objects.count(), 0)


