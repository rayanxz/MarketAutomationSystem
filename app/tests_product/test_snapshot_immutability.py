import json
from decimal import Decimal
from django.test import TestCase
from django.urls import reverse

from accounts.models import AccountProfile
from billing import services as BillingSV
from billing.models import BillItem
from inventory.models import ProductMovement
from inventory import services as InvSV
from pos import services_returns as ReturnSV
from pos.models import SalesBill, SalesBillRow, SalesReturnRow

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


class ProductSnapshotImmutabilityTests(TestCase):
    def setUp(self):
        self.user = create_user_with_role("snap_mgr", AccountProfile.Role.MANAGER)
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])

        ensure_currency("SYP", "SYP")
        ensure_currency("USD", "USD")
        ensure_fx(Decimal("10000"))

        self.store = create_stock_container("store", "Store", is_store=True)
        self.money_container = create_money_container(
            name="DrawerSnap",
            user=self.user,
            with_pos_feature=True,
        )
        self.money_container.allowed_users.add(self.user)
        self.provider = create_provider("ProvSnap")

        ok = self.client.login(username="snap_mgr", password="pass1234")
        self.assertTrue(ok)

    def test_purchase_bill_snapshots_immutable(self):
        _, pset = create_collection_set("C-Snap-1", "S-Snap-1")
        prod = create_product(
            name="ProdSnapPurchase",
            set_obj=pset,
            unit_primary=UnitType.PIECE,
            unit_secondary=UnitType.GRAM,
            conversion_factor=Decimal("2"),
        )

        original_name = prod.name
        original_unit1 = prod.get_unit_primary_display()
        original_unit2 = prod.get_unit_secondary_display()
        original_conv = prod.conversion_factor

        bill = BillingSV.create_bill(
            actor=self.user,
            provider_id=self.provider.id,
            status="paid",
            paid_amount=Decimal("10.00"),
            items=[
                {
                    "product_id": prod.id,
                    "unit_index": 2,
                    "qty_raw": "3",
                    "cost": "5.00",
                    "price": "9.00",
                    "currency": "SYP",
                }
            ],
            update_product_defaults=False,
            container=self.store,
            money_container_id=self.money_container.id,
            settlement_currency="SYP",
        )

        item = BillItem.objects.get(bill=bill, product=prod)
        mv = ProductMovement.objects.get(
            source_app="billing",
            source_model="BillItem",
            source_id=str(item.id),
            product=prod,
        )

        self.assertEqual(item.product_name_at_txn, original_name)
        self.assertEqual(item.unit_1_label_at_txn, original_unit1)
        self.assertEqual(item.unit_2_label_at_txn, original_unit2)
        self.assertEqual(item.conv_factor_at_txn, original_conv)
        self.assertEqual(item.qty_used_at_txn, Decimal("3"))
        self.assertEqual(item.qty_primary, Decimal("6.00"))

        self.assertEqual(mv.product_name_at_txn, original_name)
        self.assertEqual(mv.unit_1_label_at_txn, original_unit1)
        self.assertEqual(mv.unit_2_label_at_txn, original_unit2)
        self.assertEqual(mv.conversion_factor_at_txn, original_conv)
        self.assertEqual(mv.qty_used_at_txn, Decimal("3"))
        self.assertEqual(mv.qty_primary_at_txn, Decimal("6.00"))
        self.assertGreaterEqual(mv.qty_used_at_txn, Decimal("0"))
        self.assertGreaterEqual(mv.qty_primary_at_txn, Decimal("0"))

        prod.notes = "updated"
        prod.save(update_fields=["notes"])

        item.refresh_from_db()
        mv.refresh_from_db()

        self.assertEqual(item.product_name_at_txn, original_name)
        self.assertEqual(item.unit_1_label_at_txn, original_unit1)
        self.assertEqual(item.unit_2_label_at_txn, original_unit2)
        self.assertEqual(item.conv_factor_at_txn, original_conv)

        self.assertEqual(mv.product_name_at_txn, original_name)
        self.assertEqual(mv.unit_1_label_at_txn, original_unit1)
        self.assertEqual(mv.unit_2_label_at_txn, original_unit2)
        self.assertEqual(mv.conversion_factor_at_txn, original_conv)

    def test_pos_sale_snapshots_immutable(self):
        _, pset = create_collection_set("C-Snap-2", "S-Snap-2")
        prod = create_product(
            name="ProdSnapPOS",
            set_obj=pset,
            unit_primary=UnitType.PIECE,
            unit_secondary=UnitType.GRAM,
            conversion_factor=Decimal("2"),
        )

        original_name = prod.name
        original_unit1 = prod.get_unit_primary_display()
        original_unit2 = prod.get_unit_secondary_display()
        original_conv = prod.conversion_factor

        InvSV.record_purchase_item(
            actor=self.user,
            product=prod,
            unit_index=1,
            qty_primary=Decimal("10"),
            unit_cost=Decimal("1"),
            source_app="tests",
            source_model="Seed",
            source_id="1",
            container=self.store,
        )

        payload = {
            "id": None,
            "parked": False,
            "pay_status": "full",
            "paid_amount": "0",
            "total_amount": "0",
            "settlement_mode": "split",
            "money_container_id": self.money_container.id,
            "rows": [
                {
                    "product_id": prod.id,
                    "name": prod.name,
                    "number": str(prod.id),
                    "qty": "3",
                    "uom_index": 2,
                    "unit_price": "10",
                    "currency": "SYP",
                    "disc_amount": "0",
                    "disc_pct": "0",
                }
            ],
        }

        resp = self.client.post(
            reverse("pos:api_bill_save"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, resp.content.decode())
        data = resp.json()
        self.assertTrue(data.get("ok"))

        bill_ref = str(data["bill"]["id"])
        if bill_ref.upper().startswith("PS-"):
            bill = SalesBill.objects.get(public_id__iexact=bill_ref)
        else:
            bill = SalesBill.objects.get(pk=int(bill_ref))
        row = SalesBillRow.objects.get(bill=bill, product_id=prod.id)
        mv = ProductMovement.objects.get(
            source_app="pos",
            source_model="SalesBill",
            source_id=str(bill.id),
            product=prod,
        )

        self.assertEqual(row.product_name_at_txn, original_name)
        self.assertEqual(row.unit_1_label_at_txn, original_unit1)
        self.assertEqual(row.unit_2_label_at_txn, original_unit2)
        self.assertEqual(row.conv_factor_at_txn, original_conv)
        self.assertEqual(row.qty_primary_at_txn, Decimal("6.00"))

        self.assertEqual(mv.product_name_at_txn, original_name)
        self.assertEqual(mv.unit_1_label_at_txn, original_unit1)
        self.assertEqual(mv.unit_2_label_at_txn, original_unit2)
        self.assertEqual(mv.conversion_factor_at_txn, original_conv)
        self.assertEqual(mv.qty_primary_at_txn, Decimal("6.00"))
        self.assertGreaterEqual(mv.qty_used_at_txn, Decimal("0"))
        self.assertGreaterEqual(mv.qty_primary_at_txn, Decimal("0"))

        prod.notes = "updated"
        prod.save(update_fields=["notes"])

        row.refresh_from_db()
        mv.refresh_from_db()

        self.assertEqual(row.product_name_at_txn, original_name)
        self.assertEqual(row.unit_1_label_at_txn, original_unit1)
        self.assertEqual(row.unit_2_label_at_txn, original_unit2)
        self.assertEqual(row.conv_factor_at_txn, original_conv)

        self.assertEqual(mv.product_name_at_txn, original_name)
        self.assertEqual(mv.unit_1_label_at_txn, original_unit1)
        self.assertEqual(mv.unit_2_label_at_txn, original_unit2)
        self.assertEqual(mv.conversion_factor_at_txn, original_conv)

    def test_pos_return_snapshots_immutable(self):
        _, pset = create_collection_set("C-Snap-3", "S-Snap-3")
        prod = create_product(
            name="ProdSnapReturn",
            set_obj=pset,
            unit_primary=UnitType.PIECE,
            unit_secondary=UnitType.GRAM,
            conversion_factor=Decimal("2"),
        )

        original_name = prod.name
        original_unit1 = prod.get_unit_primary_display()
        original_unit2 = prod.get_unit_secondary_display()
        original_conv = prod.conversion_factor

        InvSV.record_purchase_item(
            actor=self.user,
            product=prod,
            unit_index=1,
            qty_primary=Decimal("10"),
            unit_cost=Decimal("1"),
            source_app="tests",
            source_model="Seed",
            source_id="1",
            container=self.store,
        )

        payload = {
            "id": None,
            "parked": False,
            "pay_status": "full",
            "paid_amount": "0",
            "total_amount": "0",
            "settlement_mode": "split",
            "money_container_id": self.money_container.id,
            "rows": [
                {
                    "product_id": prod.id,
                    "name": prod.name,
                    "number": str(prod.id),
                    "qty": "4",
                    "uom_index": 2,
                    "unit_price": "10",
                    "currency": "SYP",
                    "disc_amount": "0",
                    "disc_pct": "0",
                }
            ],
        }

        resp = self.client.post(
            reverse("pos:api_bill_save"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, resp.content.decode())
        data = resp.json()
        self.assertTrue(data.get("ok"))

        bill_ref = str(data["bill"]["id"])
        if bill_ref.upper().startswith("PS-"):
            bill = SalesBill.objects.get(public_id__iexact=bill_ref)
        else:
            bill = SalesBill.objects.get(pk=int(bill_ref))
        row = SalesBillRow.objects.get(bill=bill, product_id=prod.id)

        ret = ReturnSV.create_sales_return_draft(
            actor=self.user,
            sale_bill_id=bill.id,
            stock_container_id=self.store.id,
            items=[{"sale_row_id": row.id, "qty": "2", "reason": "test"}],
        )
        ReturnSV.post_sales_return(
            actor=self.user,
            return_id=ret.id,
            settle_mode="cash",
            money_container_id=self.money_container.id,
        )

        ret_row = SalesReturnRow.objects.get(ret=ret, product=prod)
        mv = ProductMovement.objects.get(
            source_app="pos",
            source_model="SalesReturn",
            source_id=str(ret.id),
            product=prod,
        )

        self.assertEqual(ret_row.product_name_at_txn, original_name)
        self.assertEqual(ret_row.unit_1_label_at_txn, original_unit1)
        self.assertEqual(ret_row.unit_2_label_at_txn, original_unit2)
        self.assertEqual(ret_row.conv_factor_at_txn, original_conv)
        self.assertEqual(ret_row.qty_used_at_txn, Decimal("2"))

        self.assertEqual(mv.product_name_at_txn, original_name)
        self.assertEqual(mv.unit_1_label_at_txn, original_unit1)
        self.assertEqual(mv.unit_2_label_at_txn, original_unit2)
        self.assertEqual(mv.conversion_factor_at_txn, original_conv)
        self.assertEqual(mv.qty_used_at_txn, Decimal("2"))
        self.assertGreaterEqual(mv.qty_used_at_txn, Decimal("0"))
        self.assertGreaterEqual(mv.qty_primary_at_txn, Decimal("0"))

        prod.notes = "updated"
        prod.save(update_fields=["notes"])

        ret_row.refresh_from_db()
        mv.refresh_from_db()

        self.assertEqual(ret_row.product_name_at_txn, original_name)
        self.assertEqual(ret_row.unit_1_label_at_txn, original_unit1)
        self.assertEqual(ret_row.unit_2_label_at_txn, original_unit2)
        self.assertEqual(ret_row.conv_factor_at_txn, original_conv)

        self.assertEqual(mv.product_name_at_txn, original_name)
        self.assertEqual(mv.unit_1_label_at_txn, original_unit1)
        self.assertEqual(mv.unit_2_label_at_txn, original_unit2)
        self.assertEqual(mv.conversion_factor_at_txn, original_conv)



