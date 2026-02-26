# billing/tests/test_purchase_bill_side_effects.py
from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import AccountProfile
from billing import services as BillingSV
from billing.models import Bill, BillItem, Provider
from catalog.models import Product, ProductCollection, ProductSet, UnitType
from financials.models import Currency, MoneyContainer, Receipt
from financials import services as FinSV
from inventory.models import ProductMovement, DEC0, q3
from stock.models import ProductContainer, StockFifoLayer


class PurchaseBillSideEffectsTests(TestCase):
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

    def _product(
        self,
        *,
        name: str,
        allow_syp_purch: bool,
        allow_usd_purch: bool,
    ) -> Product:
        return Product.objects.create(
            name=name,
            set=self.prod_set,
            unit_primary=UnitType.PIECE,
            allow_syp_purchasing=allow_syp_purch,
            allow_usd_purchasing=allow_usd_purch,
            allow_syp_sales=allow_syp_purch,
            allow_usd_sales=allow_usd_purch,
            default_cost_syp=Decimal("10") if allow_syp_purch else Decimal("0"),
            default_cost_usd=Decimal("1") if allow_usd_purch else Decimal("0"),
            default_price_syp=Decimal("20") if allow_syp_purch else Decimal("0"),
            default_price_usd=Decimal("2") if allow_usd_purch else Decimal("0"),
        )

    def _create_bill(self, *, product: Product, currency: str, cost: str, qty: str = "2") -> Bill:
        items = [
            {
                "product_id": product.id,
                "unit_index": 1,
                "qty_raw": qty,
                "cost": cost,
                "currency": currency,
                "price_syp": "",
                "price_usd": "",
            }
        ]
        return BillingSV.create_bill(
            actor=self.actor,
            provider_id=self.provider.id,
            status="unpaid",
            paid_amount=Decimal("0"),
            items=items,
            money_container_id=self.cash.id,
            settlement_currency=currency,
        )

    def test_create_bill_creates_inventory_movements(self):
        prod = self._product(name="SYP purch", allow_syp_purch=True, allow_usd_purch=False)
        bill = self._create_bill(product=prod, currency="SYP", cost="1000", qty="3")

        self.assertGreater(q3(bill.total_syp), DEC0)

        items = list(BillItem.objects.filter(bill=bill))
        self.assertEqual(len(items), 1)

        mvs = ProductMovement.objects.filter(
            source_app="billing",
            source_model="BillItem",
            source_id=str(items[0].id),
        )
        self.assertEqual(mvs.count(), 1)
        mv = mvs.first()
        self.assertGreater(q3(mv.qty_primary), DEC0)

        fifo = StockFifoLayer.objects.filter(
            product=prod,
            container=self.store,
            source_app="billing",
            source_model="BillItem",
            source_id=str(items[0].id),
        )
        self.assertGreater(fifo.count(), 0)
        self.assertEqual(q3(fifo.first().qty_remaining), q3(Decimal("3")))

    def test_currency_validation_blocks_side_effects(self):
        prod = self._product(name="SYP only", allow_syp_purch=True, allow_usd_purch=False)
        with self.assertRaises(ValueError):
            self._create_bill(product=prod, currency="USD", cost="5")

        self.assertEqual(Bill.objects.count(), 0)
        self.assertEqual(ProductMovement.objects.count(), 0)
        self.assertEqual(StockFifoLayer.objects.count(), 0)

    def test_cost_updates_and_movements(self):
        prod = self._product(name="SYP cost", allow_syp_purch=True, allow_usd_purch=False)
        prod.default_cost_syp = Decimal("10000")
        prod.latest_cost_syp = Decimal("10000")
        prod.save(update_fields=["default_cost_syp", "latest_cost_syp"])

        self._create_bill(product=prod, currency="SYP", cost="15000", qty="1")

        prod.refresh_from_db()
        self.assertEqual(prod.default_cost_syp, Decimal("15000"))
        self.assertEqual(prod.latest_cost_syp, Decimal("15000"))

        self.assertGreater(ProductMovement.objects.count(), 0)

    def test_billitem_currency_and_posting_consistency(self):
        prod = self._product(name="USD purch", allow_syp_purch=False, allow_usd_purch=True)
        bill = self._create_bill(product=prod, currency="USD", cost="3", qty="1")

        item = BillItem.objects.get(bill=bill)
        self.assertEqual(item.currency, "USD")
        self.assertEqual(q3(item.cost), q3(Decimal("3")))

        if Receipt.objects.exists():
            rqs = Receipt.objects.filter(
                source_app="billing",
                source_model="Bill",
                source_id=str(bill.id),
            )
            self.assertGreater(rqs.count(), 0)


