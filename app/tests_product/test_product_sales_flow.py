from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import AccountProfile
from billing import services as BillingSV
from pos.models import SalesBillRow, SalesBill
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


class ProductSalesFlowTests(TestCase):
    def setUp(self):
        self.user = create_user_with_role("mgr2", AccountProfile.Role.MANAGER)
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        ensure_currency("SYP", "SYP")
        ensure_currency("USD", "USD")
        ensure_fx(Decimal("10000"))
        self.container = create_stock_container("store", "Store", is_store=True)
        self.money_container = create_money_container(
            name="DrawerPOS",
            user=self.user,
            with_pos_feature=True,
            enable_syp=True,
            enable_usd=True,
        )
        self.provider = create_provider("Prov-POS")

    def _stock_product(self, prod):
        BillingSV.create_bill(
            actor=self.user,
            provider_id=self.provider.id,
            status="paid",
            paid_amount=Decimal("10.000"),
            items=[
                {
                    "product_id": prod.id,
                    "unit_index": 1,
                    "qty_raw": "20",
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

    def test_pos_sale_snapshot_and_display_stability(self):
        _, pset = create_collection_set("C-S1", "S-S1")
        prod = create_product(
            name="ProdPOSSnap",
            set_obj=pset,
            unit_primary=UnitType.PIECE,
            unit_secondary=UnitType.GRAM,
            conversion_factor=Decimal("2"),
            cost=Decimal("1.0000"),
            price=Decimal("2.0000"),
        )
        self._stock_product(prod)

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
        self.assertTrue(resp.json().get("ok"))
        bill_id = resp.json()["bill"]["id"]

        row = SalesBillRow.objects.get(bill_id=bill_id, product_id=prod.id)
        self.assertEqual(row.conv_factor_at_txn, Decimal("2"))
        self.assertTrue(row.unit_1_label_at_txn)

        prod.notes = "updated"
        prod.save(update_fields=["notes"])

        row.refresh_from_db()
        self.assertEqual(row.qty, Decimal("3.000"))
        self.assertEqual(row.conv_factor_at_txn, Decimal("2"))

    def test_pos_sale_primary_unit_qty(self):
        _, pset = create_collection_set("C-S2", "S-S2")
        prod = create_product(
            name="ProdPOSPrimary",
            set_obj=pset,
            unit_primary=UnitType.PIECE,
            unit_secondary=UnitType.GRAM,
            conversion_factor=Decimal("3"),
            cost=Decimal("1.0000"),
            price=Decimal("2.0000"),
        )
        self._stock_product(prod)
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
                    "qty": "5",
                    "uom_index": 1,
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
        self.assertEqual(row.qty, Decimal("5.000"))

    def test_pos_sale_usd_currency(self):
        _, pset = create_collection_set("C-S3", "S-S3")
        prod = create_product(
            name="ProdPOSUSD",
            set_obj=pset,
            unit_primary=UnitType.PIECE,
            unit_secondary="",
            conversion_factor=None,
            cost=Decimal("1.0000"),
            price=Decimal("2.0000"),
        )
        prod.allow_usd_sales = True
        prod.default_price_usd = Decimal("3.0000")
        prod.save(update_fields=["allow_usd_sales", "default_price_usd"])
        self._stock_product(prod)

        self.client.force_login(self.user)
        payload = {
            "id": None,
            "parked": False,
            "pay_status": SalesBill.PAY_FULL,
            "total_amount": "0",
            "paid_amount": "0",
            "settlement_mode": SalesBill.SETTLE_ALL_USD,
            "money_container_id": self.money_container.id,
            "customer_name": "",
            "create_new_customer": False,
            "rows": [
                {
                    "product_id": prod.id,
                    "name": prod.name,
                    "number": str(prod.id),
                    "qty": "1",
                    "uom_index": 1,
                    "unit_price": "3.000",
                    "currency": "USD",
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
        self.assertEqual(row.sale_currency, "USD")

