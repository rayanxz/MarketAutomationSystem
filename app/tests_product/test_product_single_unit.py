from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import AccountProfile
from billing import services as BillingSV
from billing.models import BillItem
from catalog.models import UnitType, Product
from inventory.models import ProductMovement
from pos.models import SalesBill, SalesBillRow
from pos import services_returns as PosReturnSV
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


class ProductSingleUnitTests(TestCase):
    def setUp(self):
        self.user = create_user_with_role("mgr_single", AccountProfile.Role.MANAGER)
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        ensure_currency("SYP", "SYP")
        ensure_currency("USD", "USD")
        ensure_fx(Decimal("10000"))
        self.container = create_stock_container("store", "Store", is_store=True)
        self.money_container = create_money_container(name="DrawerSingle", user=self.user, with_pos_feature=True)
        self.provider = create_provider("Prov-Single")

    def test_single_unit_purchase_forces_unit_index_primary(self):
        _, pset = create_collection_set("C-SU1", "S-SU1")
        prod = create_product(
            name="ProdSinglePurchase",
            set_obj=pset,
            unit_primary=UnitType.PIECE,
            unit_secondary="",
            conversion_factor=None,
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
        self.assertEqual(item.unit_index, 1)
        self.assertEqual(item.conv_factor_at_txn, Decimal("1"))
        self.assertEqual(item.qty_primary, Decimal("3.000"))

        mv = ProductMovement.objects.filter(
            source_app="billing",
            source_model="BillItem",
            source_id=str(item.id),
        ).first()
        self.assertIsNotNone(mv)
        self.assertEqual(mv.unit_index, 1)

    def test_single_unit_missing_secondary_forces_primary(self):
        _, pset = create_collection_set("C-SU1B", "S-SU1B")
        prod = create_product(
            name="ProdSingleNoSecondary",
            set_obj=pset,
            unit_primary=UnitType.PIECE,
            unit_secondary="",
            conversion_factor=None,
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
        self.assertEqual(item.unit_index, 1)
        self.assertEqual(item.conv_factor_at_txn, Decimal("1"))
        self.assertEqual(item.qty_primary, Decimal("3.000"))

        mv = ProductMovement.objects.filter(
            source_app="billing",
            source_model="BillItem",
            source_id=str(item.id),
        ).first()
        self.assertIsNotNone(mv)
        self.assertEqual(mv.unit_index, 1)

    def test_edit_to_single_unit_does_not_change_old_bill_item(self):
        _, pset = create_collection_set("C-SU2", "S-SU2")
        prod = create_product(
            name="ProdSingleEdit",
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
                    "qty_raw": "2",
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
        self.assertEqual(item.unit_index, 2)
        self.assertEqual(item.conv_factor_at_txn, Decimal("2"))

        prod.notes = "updated"
        prod.save(update_fields=["notes"])

        item.refresh_from_db()
        self.assertEqual(item.unit_index, 2)
        self.assertEqual(item.conv_factor_at_txn, Decimal("2"))

    def test_single_unit_pos_sale_forces_primary_unit(self):
        _, pset = create_collection_set("C-SU3", "S-SU3")
        prod = create_product(
            name="ProdSinglePOS",
            set_obj=pset,
            unit_primary=UnitType.PIECE,
            unit_secondary="",
            conversion_factor=None,
            cost=Decimal("1.0000"),
            price=Decimal("2.0000"),
        )

        BillingSV.create_bill(
            actor=self.user,
            provider_id=self.provider.id,
            status="paid",
            paid_amount=Decimal("10.000"),
            items=[
                {
                    "product_id": prod.id,
                    "unit_index": 1,
                    "qty_raw": "10",
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

        self.client.force_login(self.user)
        payload = {
            "id": None,
            "parked": False,
            "pay_status": SalesBill.PAY_FULL,
            "total_amount": "0",
            "paid_amount": "0",
            "settlement_mode": SalesBill.SETTLE_ALL_SYP,
            "money_container_id": self.money_container.id,
            "customer_name": "",
            "create_new_customer": False,
            "rows": [
                {
                    "product_id": prod.id,
                    "name": prod.name,
                    "number": str(prod.id),
                    "qty": "3",
                    "uom_index": 2,
                    "unit_price": "2.000",
                    "currency": "SYP",
                    "disc_amount": "0",
                    "disc_pct": "0",
                    "notes": "",
                }
            ],
        }

        resp = self.client.post(
            reverse("pos:api_bill_save"),
            data=payload,
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        bill_id = resp.json()["bill"]["id"]

        row = SalesBillRow.objects.get(bill_id=bill_id, product_id=prod.id)
        self.assertEqual(row.uom_index, 1)
        self.assertEqual(row.conv_factor_at_txn, Decimal("1"))

        mv = ProductMovement.objects.filter(
            source_app="pos",
            source_model="SalesBill",
            source_id=str(bill_id),
        ).first()
        self.assertIsNotNone(mv)
        self.assertEqual(mv.unit_index, 1)

        ret = PosReturnSV.create_sales_return_draft(
            actor=self.user,
            sale_bill_id=bill_id,
            stock_container_id=self.container.id,
            items=[{"sale_row_id": row.id, "qty": Decimal("1"), "reason": ""}],
        )
        ret_row = ret.rows.first()
        self.assertIsNotNone(ret_row)
        self.assertEqual(ret_row.uom_index, 1)
        self.assertEqual(ret_row.qty_returned, Decimal("1.000"))

