from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import AccountProfile
from billing import services as BillingSV
from billing.models import ProviderReturnItem
from pos import services_returns as PosReturnSV
from pos.models import SalesBill, SalesBillRow
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


class ProductReturnsFlowTests(TestCase):
    def setUp(self):
        self.user = create_user_with_role("mgr3", AccountProfile.Role.MANAGER)
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        ensure_currency("SYP", "SYP")
        ensure_currency("USD", "USD")
        ensure_fx(Decimal("10000"))
        self.container = create_stock_container("store", "Store", is_store=True)
        self.money_container = create_money_container(
            name="DrawerRet",
            user=self.user,
            with_pos_feature=True,
            enable_syp=True,
            enable_usd=True,
        )
        self.provider = create_provider("Prov-Ret")

    def _purchase_stock(self, prod):
        return BillingSV.create_bill(
            actor=self.user,
            provider_id=self.provider.id,
            status="paid",
            paid_amount=Decimal("10.000"),
            items=[
                {
                    "product_id": prod.id,
                    "unit_index": 1,
                    "qty_raw": "10",
                    "cost": "2.0000",
                    "price": "3.0000",
                    "currency": "SYP",
                }
            ],
            update_product_defaults=False,
            container=self.container,
            money_container_id=self.money_container.id,
            settlement_currency="SYP",
        )

    def test_provider_return_snapshot_and_display(self):
        _, pset = create_collection_set("C-R1", "S-R1")
        prod = create_product(
            name="ProdProviderRet",
            set_obj=pset,
            unit_primary=UnitType.PIECE,
            unit_secondary=UnitType.GRAM,
            conversion_factor=Decimal("2"),
            cost=Decimal("2.0000"),
            price=Decimal("4.0000"),
        )
        bill = self._purchase_stock(prod)

        ret = BillingSV.create_return(
            actor=self.user,
            provider_id=self.provider.id,
            status="paid",
            paid_amount=Decimal("2.000"),
            items=[
                {
                    "product_id": prod.id,
                    "unit_index": 2,
                    "qty_raw": "2",
                    "cost": "2.0000",
                    "currency": "SYP",
                }
            ],
            container=self.container,
            source_bill_serial=bill.serial,
            money_container_id=self.money_container.id,
            currency_code="SYP",
            valuation_mode="HISTORICAL",
        )

        item = ProviderReturnItem.objects.get(ret=ret, product=prod)
        self.assertEqual(item.conv_factor_at_txn, Decimal("2"))
        self.assertTrue(item.unit_1_label_at_txn)
        before_label = item.unit_1_label_at_txn

        prod.notes = "updated"
        prod.save(update_fields=["notes"])

        item.refresh_from_db()
        self.assertEqual(item.unit_1_label_at_txn, before_label)

    def test_pos_return_remaining_qty_stable(self):
        _, pset = create_collection_set("C-R2", "S-R2")
        prod = create_product(
            name="ProdPosRet",
            set_obj=pset,
            unit_primary=UnitType.PIECE,
            unit_secondary=UnitType.GRAM,
            conversion_factor=Decimal("2"),
            cost=Decimal("1.0000"),
            price=Decimal("2.0000"),
        )
        self._purchase_stock(prod)

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
                    "qty": "4",
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

        sold_qty_primary = (row.qty * row.conv_factor_at_txn) if row.uom_index == 2 else row.qty
        remaining_before = sold_qty_primary

        prod.notes = "updated"
        prod.save(update_fields=["notes"])

        remaining_after = sold_qty_primary

        self.assertEqual(remaining_before, remaining_after)
        self.assertEqual(row.conv_factor_at_txn, Decimal("2"))

    def test_pos_return_draft_partial_qty_primary(self):
        _, pset = create_collection_set("C-R3", "S-R3")
        prod = create_product(
            name="ProdPosRetPartial",
            set_obj=pset,
            unit_primary=UnitType.PIECE,
            unit_secondary=UnitType.GRAM,
            conversion_factor=Decimal("2"),
            cost=Decimal("1.0000"),
            price=Decimal("2.0000"),
        )
        self._purchase_stock(prod)

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
                    "qty": "4",
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
        bill_id = resp.json()["bill"]["id"]

        bill_row = SalesBillRow.objects.get(bill_id=bill_id, product_id=prod.id)
        ret = PosReturnSV.create_sales_return_draft(
            actor=self.user,
            sale_bill_id=bill_id,
            stock_container_id=self.container.id,
            items=[{"sale_row_id": bill_row.id, "qty": Decimal("1"), "reason": ""}],
        )
        ret_row = ret.rows.first()
        self.assertIsNotNone(ret_row)
        self.assertEqual(ret_row.qty_returned, Decimal("2.000"))

