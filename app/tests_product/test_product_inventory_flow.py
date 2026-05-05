from decimal import Decimal

from django.test import TestCase

from accounts.models import AccountProfile
from billing import services as BillingSV
from inventory.models import ProductMovement, q3
from stock import services as StockSV
from stock.services import MissingFifoCostBasisError
from stock.models import StockFifoLayer
from catalog.models import UnitType
from .utils import (
    create_user_with_role,
    create_provider,
    create_collection_set,
    create_product,
    create_money_container,
    create_stock_container,
    ensure_currency,
    ensure_fx,
)


class ProductInventoryFlowTests(TestCase):
    def setUp(self):
        self.user = create_user_with_role("mgr4", AccountProfile.Role.MANAGER)
        ensure_currency("SYP", "SYP")
        ensure_currency("USD", "USD")
        ensure_fx(Decimal("10000"))
        self.container = create_stock_container("store", "Store", is_store=True)
        self.wh1 = create_stock_container("wh1", "WH1", is_store=False)
        self.money_container = create_money_container(name="DrawerInv", user=self.user)
        self.provider = create_provider("Prov-Inv")

    def _purchase(self, prod, qty="10"):
        return BillingSV.create_bill(
            actor=self.user,
            provider_id=self.provider.id,
            status="paid",
            paid_amount=Decimal("10.00"),
            items=[
                {
                    "product_id": prod.id,
                    "unit_index": 1,
                    "qty_raw": qty,
                    "cost": "1.00",
                    "price": "2.00",
                    "currency": "SYP",
                }
            ],
            update_product_defaults=False,
            container=self.container,
            money_container_id=self.money_container.id,
            settlement_currency="SYP",
        )

    def test_movement_qty_primary_stable_and_fifo_rebuild(self):
        _, pset = create_collection_set("C-I1", "S-I1")
        prod = create_product(
            name="ProdInv",
            set_obj=pset,
            unit_primary=UnitType.PIECE,
            unit_secondary=UnitType.GRAM,
            conversion_factor=Decimal("2"),
        )
        self._purchase(prod, qty="10")

        mv = ProductMovement.objects.filter(
            product=prod,
            movement_type=ProductMovement.MovementType.PURCHASE,
        ).first()
        self.assertIsNotNone(mv)
        self.assertEqual(mv.qty_primary, Decimal("10.00"))

        stock_before = StockSV.total_stock_primary(prod)

        prod.notes = "updated"
        prod.save(update_fields=["notes"])

        StockSV.rebuild_all_from_inventory()
        stock_after = StockSV.total_stock_primary(prod)
        self.assertEqual(q3(stock_before), q3(stock_after))

    def test_stock_transfer_movements_stable(self):
        _, pset = create_collection_set("C-I2", "S-I2")
        prod = create_product(
            name="ProdTransfer",
            set_obj=pset,
            unit_primary=UnitType.PIECE,
            unit_secondary=UnitType.GRAM,
            conversion_factor=Decimal("2"),
        )
        self._purchase(prod, qty="5")

        layer = StockFifoLayer.objects.filter(product=prod, container=self.container).first()
        self.assertIsNotNone(layer)

        StockSV.transfer_from_batch(
            actor=self.user,
            batch=layer,
            to_container=self.wh1,
            qty_primary=Decimal("2.00"),
            ref="TXTEST",
            line_no=1,
        )

        mvs = ProductMovement.objects.filter(
            source_app="stock",
            source_model="TransferBatch",
            source_id__contains="TXTEST",
        )
        self.assertEqual(mvs.count(), 2)
        qtys = sorted([q3(mv.qty_primary) for mv in mvs])
        self.assertEqual(qtys, [Decimal("-2.00"), Decimal("2.00")])

    def test_fifo_rebuild_preserves_cost_currency(self):
        _, pset = create_collection_set("C-I3", "S-I3")
        prod = create_product(
            name="ProdRebuildCur",
            set_obj=pset,
            unit_primary=UnitType.PIECE,
            unit_secondary="",
            conversion_factor=None,
        )
        prod.allow_usd_purchasing = True
        prod.default_purchase_currency = "USD"
        prod.default_cost_usd = Decimal("3.50")
        prod.save(update_fields=["allow_usd_purchasing", "default_purchase_currency", "default_cost_usd"])

        BillingSV.create_bill(
            actor=self.user,
            provider_id=self.provider.id,
            status="paid",
            paid_amount=Decimal("7.00"),
            items=[
                {
                    "product_id": prod.id,
                    "unit_index": 1,
                    "qty_raw": "2",
                    "cost": "3.50",
                    "price": "4.00",
                    "currency": "USD",
                }
            ],
            update_product_defaults=False,
            container=self.container,
            money_container_id=self.money_container.id,
            settlement_currency="USD",
        )

        before = list(StockFifoLayer.objects.filter(product=prod, container=self.container).values_list("cost_currency", flat=True))
        self.assertTrue(before)
        self.assertTrue(all(c == "USD" for c in before))

        StockSV.rebuild_all_from_inventory()

        after = list(StockFifoLayer.objects.filter(product=prod, container=self.container).values_list("cost_currency", flat=True))
        self.assertTrue(after)
        self.assertTrue(all(c == "USD" for c in after))

    def test_fifo_consume_without_cost_basis_raises(self):
        _, pset = create_collection_set("C-I4", "S-I4")
        prod = create_product(
            name="ProdNoFifo",
            set_obj=pset,
            unit_primary=UnitType.PIECE,
            unit_secondary="",
            conversion_factor=None,
        )
        with self.assertRaises(MissingFifoCostBasisError):
            StockSV.fifo_consume(
                product=prod,
                container=self.container,
                qty_out_primary=Decimal("1.00"),
            )
