from decimal import Decimal

from django.test import TestCase
from accounts.models import AccountProfile
from billing import services as BillingSV
from billing.models import BillItem
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


class ProductPurchaseFlowTests(TestCase):
    def setUp(self):
        self.user = create_user_with_role("mgr1", AccountProfile.Role.MANAGER)
        ensure_currency("SYP", "SYP")
        ensure_currency("USD", "USD")
        ensure_fx(Decimal("10000"))
        self.container = create_stock_container("store", "Store", is_store=True)
        self.money_container = create_money_container(name="Drawer1", user=self.user)
        self.provider = create_provider("Prov-A")

    def test_purchase_bill_snapshot_and_display_stability(self):
        _, pset = create_collection_set("C-B1", "S-B1")
        prod = create_product(
            name="ProdPurchaseSnap",
            set_obj=pset,
            unit_primary=UnitType.PIECE,
            unit_secondary=UnitType.GRAM,
            conversion_factor=Decimal("2"),
            cost=Decimal("5.0000"),
            price=Decimal("9.0000"),
        )

        bill = BillingSV.create_bill(
            actor=self.user,
            provider_id=self.provider.id,
            status="paid",
            paid_amount=Decimal("10.000"),
            items=[
                {
                    "product_id": prod.id,
                    "unit_index": 2,
                    "qty_raw": "3",
                    "cost": "5.0000",
                    "price": "9.0000",
                    "currency": "SYP",
                }
            ],
            update_product_defaults=False,
            container=self.container,
            money_container_id=self.money_container.id,
            settlement_currency="SYP",
        )

        item = BillItem.objects.get(bill=bill, product=prod)
        self.assertEqual(item.qty_primary, Decimal("6.000"))
        self.assertEqual(item.conv_factor_at_txn, Decimal("2"))
        self.assertTrue(item.unit_1_label_at_txn)

        prod.notes = "updated"
        prod.save(update_fields=["notes"])

        qty_u2 = (item.qty_primary / item.conv_factor_at_txn) if item.unit_index == 2 else item.qty_primary
        self.assertEqual(qty_u2, Decimal("3.000"))

    def test_purchase_bill_primary_unit_qty_primary(self):
        _, pset = create_collection_set("C-B2", "S-B2")
        prod = create_product(
            name="ProdPurchasePrimary",
            set_obj=pset,
            unit_primary=UnitType.PIECE,
            unit_secondary=UnitType.GRAM,
            conversion_factor=Decimal("5"),
            cost=Decimal("1.0000"),
            price=Decimal("2.0000"),
        )

        bill = BillingSV.create_bill(
            actor=self.user,
            provider_id=self.provider.id,
            status="paid",
            paid_amount=Decimal("10.000"),
            items=[
                {
                    "product_id": prod.id,
                    "unit_index": 1,
                    "qty_raw": "4",
                    "cost": "1.0000",
                    "price": "2.0000",
                    "currency": "SYP",
                }
            ],
            update_product_defaults=False,
            container=self.container,
            money_container_id=self.money_container.id,
            settlement_currency="SYP",
        )

        item = BillItem.objects.get(bill=bill, product=prod)
        self.assertEqual(item.qty_primary, Decimal("4.000"))

    def test_purchase_bill_usd_currency_snapshot(self):
        _, pset = create_collection_set("C-B3", "S-B3")
        prod = create_product(
            name="ProdPurchaseUSD",
            set_obj=pset,
            unit_primary=UnitType.PIECE,
            unit_secondary="",
            conversion_factor=None,
            cost=Decimal("1.0000"),
            price=Decimal("2.0000"),
        )
        prod.allow_usd_purchasing = True
        prod.save(update_fields=["allow_usd_purchasing"])

        bill = BillingSV.create_bill(
            actor=self.user,
            provider_id=self.provider.id,
            status="paid",
            paid_amount=Decimal("10.000"),
            items=[
                {
                    "product_id": prod.id,
                    "unit_index": 1,
                    "qty_raw": "1",
                    "cost": "2.5000",
                    "price": "3.0000",
                    "currency": "USD",
                }
            ],
            update_product_defaults=False,
            container=self.container,
            money_container_id=self.money_container.id,
            settlement_currency="USD",
            fx_usd_syp=Decimal("10000"),
        )
        item = BillItem.objects.get(bill=bill, product=prod)
        self.assertEqual(item.currency, "USD")
