from __future__ import annotations

import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from catalog.models import ProductCollection, ProductSet, Product, UnitType, ProductBarcode, ProductUnitId
from pos import services_returns as ReturnSV
from pos.models import SalesBill, SalesBillRow, SalesReturn, SalesReturnRow
from stock.models import ProductContainer


class PosArchivedProductBlockingTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="cashier", password="pass", is_staff=True)
        self.client.force_login(self.user)

        col = ProductCollection.objects.create(name="C1")
        self.set_obj = ProductSet.objects.create(collection=col, name="S1")

        self.store, _ = ProductContainer.objects.get_or_create(
            code="store",
            defaults={"name": "Store", "is_store": True, "is_active": True},
        )

    def _create_product(self, *, name: str, active: bool) -> Product:
        return Product.objects.create(
            name=name,
            set=self.set_obj,
            unit_primary=UnitType.PIECE,
            unit_secondary="",
            conversion_factor=None,
            cost=Decimal("1.0000"),
            price=Decimal("2.0000"),
            is_active=active,
        )

    def test_pos_lookup_endpoints_exclude_archived(self):
        prod = self._create_product(name="Archived-1", active=False)
        ProductBarcode.objects.create(product=prod, unit_index=1, barcode="BC-ARCH", is_active=False)
        ProductUnitId.objects.create(product=prod, unit_index=1, value="UID-ARCH", is_active=False)

        resp = self.client.get(reverse("pos:api_barcode_lookup", kwargs={"value": "BC-ARCH"}))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json().get("error"), "NOT_FOUND")

        resp = self.client.get(reverse("pos:api_search_name"), {"q": "Archived"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json().get("hits"), [])

        resp = self.client.get(reverse("pos:api_lookup_code", kwargs={"value": "UID-ARCH"}))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json().get("error"), "NOT_FOUND")

        resp = self.client.get(reverse("pos:api_lookup_id", kwargs={"pk": prod.id}))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json().get("error"), "NOT_FOUND")

    def test_pos_bill_save_rejects_archived_product(self):
        prod = self._create_product(name="Archived-2", active=False)
        url = reverse("pos:api_bill_save")
        payload = {
            "parked": True,
            "pay_status": "full",
            "total_amount": "0",
            "paid_amount": "0",
            "settlement_mode": "split",
            "rows": [
                {
                    "product_id": prod.id,
                    "qty": "1",
                    "uom_index": 1,
                    "unit_price": "1.000",
                    "currency": "SYP",
                    "disc_amount": "0",
                    "disc_pct": "0",
                }
            ],
        }
        resp = self.client.post(
            url,
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json().get("error"), "INVALID_PRODUCT")

    def test_pos_return_draft_blocks_archived_product(self):
        prod = self._create_product(name="Archived-3", active=False)
        bill = SalesBill.objects.create(
            cashier=self.user,
            parked=False,
            finalized=True,
            pay_status=SalesBill.PAY_FULL,
            total_amount=Decimal("1.000"),
            total_syp=Decimal("1.000"),
            paid_amount=Decimal("1.000"),
            settlement_mode=SalesBill.SETTLE_SPLIT,
        )
        row = SalesBillRow.objects.create(
            bill=bill,
            product_id=prod.id,
            product_name=prod.name,
            product_number=prod.display_code,
            conv_factor_at_txn=Decimal("1.0000"),
            unit_1_label_at_txn=prod.get_unit_primary_display(),
            unit_2_label_at_txn="",
            qty=Decimal("1.000"),
            uom_index=1,
            unit_price=Decimal("1.000"),
            sale_currency="SYP",
        )

        with self.assertRaises(ValidationError):
            ReturnSV.create_sales_return_draft(
                actor=self.user,
                sale_bill_id=bill.id,
                stock_container_id=self.store.id,
                items=[{"sale_row_id": row.id, "qty": "1", "reason": ""}],
            )

    def test_pos_return_post_blocks_archived_product(self):
        prod = self._create_product(name="Archived-4", active=False)
        bill = SalesBill.objects.create(
            cashier=self.user,
            parked=False,
            finalized=True,
            pay_status=SalesBill.PAY_FULL,
            total_amount=Decimal("1.000"),
            total_syp=Decimal("1.000"),
            paid_amount=Decimal("1.000"),
            settlement_mode=SalesBill.SETTLE_SPLIT,
        )
        sale_row = SalesBillRow.objects.create(
            bill=bill,
            product_id=prod.id,
            product_name=prod.name,
            product_number=prod.display_code,
            conv_factor_at_txn=Decimal("1.0000"),
            unit_1_label_at_txn=prod.get_unit_primary_display(),
            unit_2_label_at_txn="",
            qty=Decimal("1.000"),
            uom_index=1,
            unit_price=Decimal("1.000"),
            sale_currency="SYP",
        )

        ret = SalesReturn.objects.create(
            sale_bill=bill,
            customer=None,
            stock_container=self.store,
            status=SalesReturn.Status.DRAFT,
            created_by=self.user,
            total_syp=Decimal("0.000"),
            total_usd=Decimal("0.000"),
        )
        SalesReturnRow.objects.create(
            ret=ret,
            sale_row=sale_row,
            product=prod,
            uom_index=1,
            conv_factor_at_txn=Decimal("1.0000"),
            unit_1_label_at_txn=prod.get_unit_primary_display(),
            unit_2_label_at_txn="",
            qty_returned=Decimal("1.000"),
            currency_code="SYP",
            unit_price_at_sale=Decimal("1.000"),
            line_total=Decimal("1.000"),
        )

        with self.assertRaises(ValueError):
            ReturnSV.post_sales_return(
                actor=self.user,
                return_id=ret.id,
                settle_mode="credit",
            )
