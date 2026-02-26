from __future__ import annotations

import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TransactionTestCase
from django.urls import reverse

from audit_log.models import AuditLog
from pos.models import SalesBill, PosShift
from financials.models import MoneyContainer, MoneyContainerCurrency, ContainerFeature, Currency
from catalog.models import Product, ProductCollection, ProductSet, UnitType


class PosAuditTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="cashier1",
            password="pw12345",
        )
        self.client.login(username="cashier1", password="pw12345")

        self.url_bill_save   = reverse("pos:api_bill_save")
        self.url_shift_start = reverse("pos:api_shift_start")
        self.url_shift_end   = reverse("pos:api_shift_end")

        self.syp, _ = Currency.objects.get_or_create(
            code="SYP",
            defaults={"name": "Syrian Pound", "decimals": 0, "is_active": True},
        )

        pos_feature, _ = ContainerFeature.objects.get_or_create(
            code="pos_sales",
            defaults={"name": "POS Sales", "is_active": True, "sort_order": 10},
        )
        self.cash = MoneyContainer.objects.create(
            name="Audit Cash",
            container_type=MoneyContainer.ContainerType.DRAWER,
            is_active=True,
            created_by=self.user,
        )
        self.cash.features.add(pos_feature)
        self.cash.allowed_users.add(self.user)
        MoneyContainerCurrency.objects.update_or_create(
            container=self.cash,
            currency=self.syp,
            defaults={"is_enabled": True},
        )

        col = ProductCollection.objects.create(name="POS Audit Collection")
        prod_set = ProductSet.objects.create(collection=col, name="POS Audit Set")
        self.product = Product.objects.create(
            name="Audit Item",
            set=prod_set,
            unit_primary=UnitType.PIECE,
            allow_syp_sales=True,
            allow_usd_sales=False,
            allow_syp_purchasing=True,
            allow_usd_purchasing=True,
            default_cost_syp=1,
            default_cost_usd=0,
            default_price_syp=1,
            default_price_usd=0,
        )

    # -------------------------
    # helpers
    # -------------------------
    def _post_json(self, url: str, data: dict, status=200):
        resp = self.client.post(
            url,
            data=json.dumps(data),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, status, resp.content.decode())
        return resp.json()

    def _assert_audit_kind(self, kind: str):
        self.assertTrue(
            AuditLog.objects.filter(meta_json__icontains=f'"kind": "{kind}"').exists(),
            f"Audit kind missing: {kind}",
        )

    # =========================
    # SALE BILLS
    # =========================
    def test_sale_bill_parked_logged(self):
        payload = {
            "id": None,
            "parked": True,
            "pay_status": "full",
            "total_amount": "0",
            "paid_amount": "0",
            "customer_name": "Parked",
            "rows": [],
        }

        out = self._post_json(self.url_bill_save, payload)
        self.assertTrue(out["ok"])

        self._assert_audit_kind("pos.sale_bill_pended")

    @patch("pos.api_bills.POSSV.finalize_pos_bill", autospec=True)
    def test_sale_bill_saved_logged(self, _finalize_mock):
        payload = {
            "id": None,
            "parked": False,
            "pay_status": "full",
            "total_amount": "10",
            "paid_amount": "10",
            "settlement_mode": "split",
            "money_container_id": self.cash.id,
            "customer_name": "Saved",
            "rows": [
                {
                    "product_id": self.product.id,
                    "name": "Item",
                    "number": "001",
                    "qty": "1",
                    "uom_index": 1,
                    "unit_price": "10",
                    "disc_amount": "0",
                    "disc_pct": "0",
                }
            ],
        }

        out = self._post_json(self.url_bill_save, payload)
        self.assertTrue(out["ok"])

        self._assert_audit_kind("pos.sale_bill_saved")

    def test_sale_bill_delete_parked_logged(self):
        # create parked bill
        payload = {
            "id": None,
            "parked": True,
            "pay_status": "full",
            "total_amount": "0",
            "paid_amount": "0",
            "customer_name": "To delete",
            "rows": [],
        }

        out = self._post_json(self.url_bill_save, payload)
        bill_id = out["bill"]["id"]

        url_delete = reverse("pos:api_bill_delete", kwargs={"pk": bill_id})
        resp = self.client.post(url_delete, content_type="application/json")
        self.assertEqual(resp.status_code, 200)

        bill = SalesBill.objects.get(pk=bill_id)
        self.assertTrue(bill.is_deleted)

        self._assert_audit_kind("pos.sale_bill_deleted")

    # =========================
    # SHIFTS
    # =========================
    def test_shift_start_logged(self):
        out = self._post_json(self.url_shift_start, {})
        self.assertTrue(out["ok"])

        if out["created"]:
            self._assert_audit_kind("pos.shift_started")

    def test_shift_end_logged(self):
        start = self._post_json(self.url_shift_start, {})
        shift_id = start["id"]

        out = self._post_json(self.url_shift_end, {"id": shift_id})
        self.assertTrue(out["ok"])

        shift = PosShift.objects.get(pk=shift_id)
        self.assertIsNotNone(shift.ended_at)

        self._assert_audit_kind("pos.shift_ended")
