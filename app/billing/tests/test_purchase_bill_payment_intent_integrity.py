from __future__ import annotations

import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from accounts.models import AccountProfile
from billing import services as BillingSV
from billing.models import Bill, BillItem, Provider
from catalog.models import Product, ProductCollection, ProductSet, UnitType
from debts.models import DebtorPayment, DebtorDebt
from financials import services as FinSV
from financials.models import (
    ContainerFeature,
    Currency,
    MoneyContainer,
    MoneyContainerCurrency,
    PostingTargetType,
    Receipt,
    ReceiptKind,
    ReceiptStatus,
)
from stock.models import ProductContainer


class PurchaseBillPaymentIntentIntegrityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.actor = User.objects.create_user(username="bill_intent_mgr", password="123")
        prof, _ = AccountProfile.objects.get_or_create(user=cls.actor)
        prof.role = AccountProfile.Role.MANAGER
        prof.save(update_fields=["role"])

        Currency.objects.get_or_create(
            code="SYP",
            defaults={"name": "Syrian Pound", "decimals": 0, "is_active": True},
        )
        Currency.objects.get_or_create(
            code="USD",
            defaults={"name": "US Dollar", "decimals": 2, "is_active": True},
        )

        cls.store = ProductContainer.objects.filter(code="store").first()
        if not cls.store:
            cls.store = ProductContainer.objects.create(
                name="Main Store",
                code="store",
                is_store=True,
                is_active=True,
            )

        feature, _ = ContainerFeature.objects.get_or_create(
            code="purchase_bills",
            defaults={"name": "Purchase Bills", "is_active": True},
        )
        if not feature.is_active:
            feature.is_active = True
            feature.save(update_fields=["is_active"])
        cls.cash = MoneyContainer.objects.create(
            name="Intent Cash Drawer",
            container_type=MoneyContainer.ContainerType.DRAWER,
            is_active=True,
            created_by=cls.actor,
        )
        cls.cash.features.add(feature)
        cls.cash.allowed_users.add(cls.actor)

        for code in ("SYP", "USD"):
            cur = Currency.objects.get(code=code)
            MoneyContainerCurrency.objects.update_or_create(
                container=cls.cash,
                currency=cur,
                defaults={"is_enabled": True},
            )

        col = ProductCollection.objects.create(name="Intent Collection")
        pset = ProductSet.objects.create(collection=col, name="Intent Set")

        cls.prod_syp = Product.objects.create(
            name="Intent SYP Product",
            set=pset,
            unit_primary=UnitType.PIECE,
            allow_syp_purchasing=True,
            allow_usd_purchasing=False,
            allow_syp_sales=True,
            allow_usd_sales=False,
            default_cost_syp=Decimal("1000"),
            default_cost_usd=Decimal("0"),
            default_price_syp=Decimal("1200"),
            default_price_usd=Decimal("0"),
        )
        cls.prod_usd = Product.objects.create(
            name="Intent USD Product",
            set=pset,
            unit_primary=UnitType.PIECE,
            allow_syp_purchasing=False,
            allow_usd_purchasing=True,
            allow_syp_sales=False,
            allow_usd_sales=True,
            default_cost_syp=Decimal("0"),
            default_cost_usd=Decimal("2"),
            default_price_syp=Decimal("0"),
            default_price_usd=Decimal("3"),
        )
        cls.provider = Provider.objects.create(name="Intent Provider")

        FinSV.set_current_fx(actor=cls.actor, rate_syp_per_usd=Decimal("15000"))

    def setUp(self):
        ok = self.client.login(username="bill_intent_mgr", password="123")
        self.assertTrue(ok)

    def _api_payload(self, *, method: str, paid_syp: str, paid_usd: str) -> dict:
        return {
            "provider": {"id": self.provider.id},
            "container": "store",
            "money_container_id": self.cash.id,
            "currency_code": "SYP",
            "items": [
                {
                    "product_id": self.prod_syp.id,
                    "unit_index": 1,
                    "qty_raw": "1",
                    "cost": "1000",
                    "currency": "SYP",
                },
                {
                    "product_id": self.prod_usd.id,
                    "unit_index": 1,
                    "qty_raw": "1",
                    "cost": "2",
                    "currency": "USD",
                },
            ],
            "pay": {
                "status": "paid",
                "method": method,
                "amount_syp": paid_syp,
                "amount_usd": paid_usd,
                "paid_amount": "0",
                "fx_rate": "15000",
            },
        }

    def test_api_paid_syp_only_persists_intent_and_posts_one_receipt(self):
        payload = self._api_payload(method="syp_only", paid_syp="31000", paid_usd="0")
        resp = self.client.post(
            reverse("billing_api_bill_save"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, resp.content.decode("utf-8"))
        self.assertTrue(resp.json().get("ok"))

        bill = Bill.objects.get(pk=resp.json()["bill"]["id"])
        self.assertEqual(bill.creation_payment_status, "paid")
        self.assertEqual(bill.creation_payment_method, "syp_only")
        self.assertEqual(bill.creation_paid_syp, Decimal("31000"))
        self.assertEqual(bill.creation_paid_usd, Decimal("0"))

        receipts = Receipt.objects.filter(
            source_app="billing",
            source_model="Bill",
            source_id=str(bill.id),
            status=ReceiptStatus.POSTED,
        )
        self.assertEqual(receipts.count(), 1)
        receipt = receipts.first()
        self.assertEqual(receipt.kind, ReceiptKind.COUNTERPARTY_SETTLE)

        self.cash.refresh_from_db()
        self.assertEqual(self.cash.balance_syp, Decimal("-31000"))
        self.assertEqual(self.cash.balance_usd, Decimal("0"))

    def test_separate_payment_keeps_single_receipt_with_both_container_currencies(self):
        payload = self._api_payload(method="separate", paid_syp="1000", paid_usd="2")
        resp = self.client.post(
            reverse("billing_api_bill_save"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, resp.content.decode("utf-8"))
        bill = Bill.objects.get(pk=resp.json()["bill"]["id"])

        receipt = Receipt.objects.get(
            source_app="billing",
            source_model="Bill",
            source_id=str(bill.id),
            status=ReceiptStatus.POSTED,
        )
        container_lines = list(
            receipt.lines.filter(target_type=PostingTargetType.CONTAINER).select_related("currency")
        )
        self.assertEqual({ln.currency.code for ln in container_lines}, {"SYP", "USD"})

    def test_service_usd_only_full_payment_does_not_force_syp_container_movement(self):
        bill = BillingSV.create_bill(
            actor=self.actor,
            provider_id=self.provider.id,
            status="paid",
            paid_amount=Decimal("0"),
            payment_status="paid",
            payment_method="usd_only",
            paid_syp=Decimal("0"),
            paid_usd=Decimal("2.07"),
            items=[
                {"product_id": self.prod_syp.id, "unit_index": 1, "qty_raw": "1", "cost": "1000", "currency": "SYP"},
                {"product_id": self.prod_usd.id, "unit_index": 1, "qty_raw": "1", "cost": "2", "currency": "USD"},
            ],
            container=self.store,
            money_container_id=self.cash.id,
            settlement_currency="USD",
            fx_usd_syp=Decimal("15000"),
        )

        self.assertEqual(bill.creation_payment_status, "paid")
        self.assertEqual(bill.creation_payment_method, "usd_only")
        self.assertEqual(bill.creation_paid_syp, Decimal("0"))
        self.assertEqual(bill.creation_paid_usd, Decimal("2.07"))

        self.cash.refresh_from_db()
        self.assertEqual(self.cash.balance_syp, Decimal("0"))
        self.assertEqual(self.cash.balance_usd, Decimal("-2.07"))

        receipt_ids = set(
            DebtorPayment.objects.filter(
                entry__source_app="billing",
                entry__source_model="Bill",
                entry__source_id=str(bill.id),
                receipt__isnull=False,
            ).values_list("receipt_id", flat=True)
        )
        self.assertEqual(len(receipt_ids), 1)

    def test_bill_view_uses_snapshot_placeholder_when_snapshot_fields_missing(self):
        bill = BillingSV.create_bill(
            actor=self.actor,
            provider_id=self.provider.id,
            status="unpaid",
            paid_amount=Decimal("0"),
            items=[
                {"product_id": self.prod_syp.id, "unit_index": 1, "qty_raw": "1", "cost": "1000", "currency": "SYP"},
            ],
            container=self.store,
            money_container_id=self.cash.id,
            settlement_currency="SYP",
            fx_usd_syp=Decimal("15000"),
        )

        item = BillItem.objects.get(bill=bill)
        item.product_name_at_txn = ""
        item.unit_1_label_at_txn = ""
        item.save(update_fields=["product_name_at_txn", "unit_1_label_at_txn"])

        resp = self.client.get(reverse("billing_bill_view", args=[bill.id]))
        self.assertEqual(resp.status_code, 200)
        row = resp.context["items_rows"][0]
        self.assertEqual(row["product_name"], "—")
        self.assertEqual(row["unit1_label"], "—")

    def test_zero_total_bill_is_non_financial_without_receipt_or_debt(self):
        bill = BillingSV.create_bill(
            actor=self.actor,
            provider_id=self.provider.id,
            status="unpaid",
            paid_amount=Decimal("0"),
            payment_status="unpaid",
            payment_method="none",
            paid_syp=Decimal("0"),
            paid_usd=Decimal("0"),
            items=[
                {"product_id": self.prod_syp.id, "unit_index": 1, "qty_raw": "1", "cost": "0", "currency": "SYP"},
            ],
            container=self.store,
            money_container_id=self.cash.id,
            settlement_currency="SYP",
            fx_usd_syp=Decimal("15000"),
        )

        self.assertEqual(bill.total_syp, Decimal("0"))
        self.assertEqual(bill.total_usd, Decimal("0"))
        self.assertEqual(bill.creation_payment_status, "unpaid")
        self.assertEqual(bill.creation_payment_method, "none")

        self.assertFalse(
            Receipt.objects.filter(
                source_app="billing",
                source_model="Bill",
                source_id=str(bill.id),
            ).exists()
        )
        self.assertFalse(
            DebtorDebt.objects.filter(
                source_app="billing",
                source_model="Bill",
                source_id=str(bill.id),
            ).exists()
        )

        self.cash.refresh_from_db()
        self.assertEqual(self.cash.balance_syp, Decimal("0"))
        self.assertEqual(self.cash.balance_usd, Decimal("0"))

    def test_zero_total_bill_rejects_payment_attempt(self):
        with self.assertRaises(ValidationError):
            BillingSV.create_bill(
                actor=self.actor,
                provider_id=self.provider.id,
                status="paid",
                paid_amount=Decimal("1"),
                payment_status="paid",
                payment_method="syp_only",
                paid_syp=Decimal("1"),
                paid_usd=Decimal("0"),
                items=[
                    {"product_id": self.prod_syp.id, "unit_index": 1, "qty_raw": "1", "cost": "0", "currency": "SYP"},
                ],
                container=self.store,
                money_container_id=self.cash.id,
                settlement_currency="SYP",
                fx_usd_syp=Decimal("15000"),
            )

    def test_bill_view_legacy_missing_creation_snapshot_marks_unknown(self):
        bill = BillingSV.create_bill(
            actor=self.actor,
            provider_id=self.provider.id,
            status="unpaid",
            paid_amount=Decimal("0"),
            items=[
                {"product_id": self.prod_syp.id, "unit_index": 1, "qty_raw": "1", "cost": "1000", "currency": "SYP"},
            ],
            container=self.store,
            money_container_id=self.cash.id,
            settlement_currency="SYP",
            fx_usd_syp=Decimal("15000"),
        )
        bill.creation_payment_status = ""
        bill.creation_payment_method = ""
        bill.creation_paid_syp = Decimal("0")
        bill.creation_paid_usd = Decimal("0")
        bill.save(
            update_fields=[
                "creation_payment_status",
                "creation_payment_method",
                "creation_paid_syp",
                "creation_paid_usd",
            ]
        )

        resp = self.client.get(reverse("billing_bill_view", args=[bill.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["created_payment_status_code"], "unknown")
        self.assertEqual(resp.context["created_payment_method_code"], "UNKNOWN")
        self.assertEqual(resp.context["created_payment_status_label"], "غير معروف (سجل قديم)")
        self.assertEqual(resp.context["created_payment_method_label"], "غير معروف (سجل قديم)")
