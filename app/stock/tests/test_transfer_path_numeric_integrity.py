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


class StockTransferPathNumericIntegrityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.actor = User.objects.create_user(username="stock_mgr", password="pw12345")
        AccountProfile.objects.create(user=cls.actor, role=AccountProfile.Role.MANAGER)

        cls.store, _ = ProductContainer.objects.get_or_create(
            code="store",
            defaults={"name": "Store", "is_store": True, "is_active": True},
        )
        cls.wh1, _ = ProductContainer.objects.get_or_create(
            code="wh1",
            defaults={"name": "Warehouse 1", "is_store": False, "is_active": True},
        )

        col = ProductCollection.objects.create(name="Transfer Collection")
        pset = ProductSet.objects.create(collection=col, name="Transfer Set")
        cls.product = Product.objects.create(
            name="Transfer Product",
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
            source_model="StockTransferSeed",
            source_id="seed-1",
            container=cls.store,
        )

    def _store_batch(self) -> StockFifoLayer:
        return (
            StockFifoLayer.objects
            .filter(product=self.product, container=self.store, qty_remaining__gt=0)
            .order_by("created_at", "id")
            .first()
        )

    def test_transfer_from_batch_moves_fifo_and_stock_entries_consistently(self):
        store_entry_before = StockEntry.objects.get(product=self.product, container=self.store)
        self.assertEqual(q3(store_entry_before.qty_primary), Decimal("10.000"))
        self.assertEqual(q3(self.product.stock_qty), Decimal("10.000"))

        batch = self._store_batch()
        self.assertIsNotNone(batch)
        source_id = batch.source_id
        source_app = batch.source_app
        source_model = batch.source_model

        mv_out, mv_in = StockSV.transfer_from_batch(
            actor=self.actor,
            batch=batch,
            to_container=self.wh1,
            qty_primary=Decimal("4"),
            ref="TX-PATH-1",
            line_no=1,
        )

        batch.refresh_from_db()
        self.assertEqual(q3(batch.qty_remaining), Decimal("6.000"))

        dest_layer = (
            StockFifoLayer.objects
            .filter(product=self.product, container=self.wh1, source_id=source_id)
            .order_by("created_at", "id")
            .first()
        )
        self.assertIsNotNone(dest_layer)
        self.assertEqual(q3(dest_layer.qty_remaining), Decimal("4.000"))
        self.assertEqual(dest_layer.unit_cost, Decimal("5.0000"))
        self.assertEqual(dest_layer.cost_currency, "USD")
        self.assertEqual(dest_layer.source_app, source_app)
        self.assertEqual(dest_layer.source_model, source_model)

        store_entry_after = StockEntry.objects.get(product=self.product, container=self.store)
        wh1_entry_after = StockEntry.objects.get(product=self.product, container=self.wh1)
        self.assertEqual(q3(store_entry_after.qty_primary), Decimal("6.000"))
        self.assertEqual(q3(wh1_entry_after.qty_primary), Decimal("4.000"))

        self.product.refresh_from_db(fields=["stock_qty"])
        self.assertEqual(q3(self.product.stock_qty), Decimal("10.000"))

        self.assertEqual(q3(mv_out.qty_primary), Decimal("-4.000"))
        self.assertEqual(q3(mv_in.qty_primary), Decimal("4.000"))

    def test_transfer_then_sale_from_destination_keeps_fifo_cost_traceable(self):
        batch = self._store_batch()
        self.assertIsNotNone(batch)
        source_id = batch.source_id
        source_app = batch.source_app
        source_model = batch.source_model

        StockSV.transfer_from_batch(
            actor=self.actor,
            batch=batch,
            to_container=self.wh1,
            qty_primary=Decimal("4"),
            ref="TX-PATH-2",
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
            source_model="TransferSale",
            source_id="sale-1",
            container=self.wh1,
        )

        sale_parts = list(SaleCostPart.objects.filter(movement=sale_mv).select_related("fifo_layer"))
        self.assertGreater(len(sale_parts), 0)
        self.assertEqual(
            q3(sum((p.qty_primary for p in sale_parts), Decimal("0"))),
            Decimal("3.000"),
        )
        self.assertEqual(
            q3(sum((p.total_cost for p in sale_parts), Decimal("0"))),
            Decimal("15.000"),
        )
        self.assertTrue(all(p.fifo_layer.cost_currency == "USD" for p in sale_parts))
        self.assertTrue(
            any(
                p.fifo_layer.source_app == source_app
                and p.fifo_layer.source_model == source_model
                and p.fifo_layer.source_id == source_id
                for p in sale_parts
            )
        )

        store_entry = StockEntry.objects.get(product=self.product, container=self.store)
        wh1_entry = StockEntry.objects.get(product=self.product, container=self.wh1)
        self.assertEqual(q3(store_entry.qty_primary), Decimal("6.000"))
        self.assertEqual(q3(wh1_entry.qty_primary), Decimal("1.000"))

        self.product.refresh_from_db(fields=["stock_qty"])
        self.assertEqual(q3(self.product.stock_qty), Decimal("7.000"))
