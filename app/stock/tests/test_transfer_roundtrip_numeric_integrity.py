from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import AccountProfile
from catalog.models import Product, ProductCollection, ProductSet, UnitType
from inventory import services as InvSV
from inventory.models import SaleCostPart, q3
from stock import services as StockSV
from stock.models import ProductContainer, StockEntry, StockFifoLayer


class StockTransferRoundtripNumericIntegrityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.actor = User.objects.create_user(username="stock_roundtrip_mgr", password="pw12345")
        AccountProfile.objects.create(user=cls.actor, role=AccountProfile.Role.MANAGER)

        cls.store, _ = ProductContainer.objects.get_or_create(
            code="store",
            defaults={"name": "Store", "is_store": True, "is_active": True},
        )
        cls.wh1, _ = ProductContainer.objects.get_or_create(
            code="wh1",
            defaults={"name": "Warehouse 1", "is_store": False, "is_active": True},
        )

        col = ProductCollection.objects.create(name="Transfer Roundtrip Collection")
        pset = ProductSet.objects.create(collection=col, name="Transfer Roundtrip Set")
        cls.product = Product.objects.create(
            name="Transfer Roundtrip Product",
            set=pset,
            unit_primary=UnitType.PIECE,
            allow_syp_purchasing=True,
            allow_usd_purchasing=True,
            allow_syp_sales=True,
            allow_usd_sales=True,
            default_cost_syp=Decimal("1000"),
            default_cost_usd=Decimal("5"),
            default_price_syp=Decimal("1200"),
            default_price_usd=Decimal("6"),
        )

        InvSV.record_purchase_item(
            actor=cls.actor,
            product=cls.product,
            unit_index=1,
            qty_primary=Decimal("10"),
            unit_cost=Decimal("5"),
            cost_currency="USD",
            source_app="tests",
            source_model="StockTransferRoundtripSeed",
            source_id="seed-1",
            container=cls.store,
        )

    def _layer(self, *, container: ProductContainer) -> StockFifoLayer:
        return (
            StockFifoLayer.objects
            .filter(product=self.product, container=container, qty_remaining__gt=0)
            .order_by("created_at", "id")
            .first()
        )

    def test_transfer_sell_transfer_back_preserves_fifo_and_quantities(self):
        store_entry_before = StockEntry.objects.get(product=self.product, container=self.store)
        self.assertEqual(q3(store_entry_before.qty_primary), Decimal("10.00"))

        first_layer = self._layer(container=self.store)
        self.assertIsNotNone(first_layer)

        StockSV.transfer_from_batch(
            actor=self.actor,
            batch=first_layer,
            to_container=self.wh1,
            qty_primary=Decimal("4"),
            ref="RT-1",
            line_no=1,
        )

        sale_mv = InvSV.record_sale_item(
            actor=self.actor,
            product=self.product,
            unit_index=1,
            qty_primary=Decimal("3"),
            unit_cost=Decimal("0"),
            sale_unit_price_at_txn=Decimal("8"),
            sale_currency_at_txn="USD",
            source_app="tests",
            source_model="StockTransferRoundtripSale",
            source_id="sale-1",
            container=self.wh1,
        )
        sale_parts = list(SaleCostPart.objects.filter(movement=sale_mv).select_related("fifo_layer"))
        self.assertGreater(len(sale_parts), 0)
        self.assertEqual(q3(sum((p.qty_primary for p in sale_parts), Decimal("0"))), Decimal("3.00"))
        self.assertEqual(q3(sum((p.total_cost for p in sale_parts), Decimal("0"))), Decimal("15.00"))
        self.assertTrue(all((p.fifo_layer.cost_currency or "").upper() == "USD" for p in sale_parts))

        wh1_layer_after_sale = self._layer(container=self.wh1)
        self.assertIsNotNone(wh1_layer_after_sale)
        self.assertEqual(q3(wh1_layer_after_sale.qty_remaining), Decimal("1.00"))

        StockSV.transfer_from_batch(
            actor=self.actor,
            batch=wh1_layer_after_sale,
            to_container=self.store,
            qty_primary=Decimal("1"),
            ref="RT-2",
            line_no=2,
        )

        store_entry_after = StockEntry.objects.get(product=self.product, container=self.store)
        wh1_entry_after = StockEntry.objects.get(product=self.product, container=self.wh1)
        self.assertEqual(q3(store_entry_after.qty_primary), Decimal("7.00"))
        self.assertEqual(q3(wh1_entry_after.qty_primary), Decimal("0.00"))

        self.product.refresh_from_db(fields=["stock_qty"])
        self.assertEqual(q3(self.product.stock_qty), Decimal("7.00"))

        store_layers = list(
            StockFifoLayer.objects.filter(product=self.product, container=self.store, qty_remaining__gt=0)
        )
        wh1_layers = list(
            StockFifoLayer.objects.filter(product=self.product, container=self.wh1, qty_remaining__gt=0)
        )
        self.assertEqual(
            q3(sum((layer.qty_remaining for layer in store_layers), Decimal("0"))),
            Decimal("7.00"),
        )
        self.assertEqual(
            q3(sum((layer.qty_remaining for layer in wh1_layers), Decimal("0"))),
            Decimal("0.00"),
        )
        self.assertTrue(all(layer.qty_remaining >= 0 for layer in store_layers))
        self.assertTrue(all(layer.qty_remaining >= 0 for layer in wh1_layers))
