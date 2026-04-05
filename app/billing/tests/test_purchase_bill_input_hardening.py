from __future__ import annotations

import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import AccountProfile
from billing.models import Bill, Provider
from catalog.models import Product, ProductCollection, ProductSet, UnitType
from financials import services as FinSV
from financials.models import ContainerFeature, Currency, MoneyContainer, MoneyContainerCurrency
from stock.models import ProductContainer


class PurchaseBillInputHardeningTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.user = user_model.objects.create_user(username="mgr_harden", password="pass")
        AccountProfile.objects.create(user=cls.user, role=AccountProfile.Role.MANAGER)

        cls.syp, _ = Currency.objects.get_or_create(
            code="SYP",
            defaults={"name": "SYP", "decimals": 0, "is_active": True},
        )
        cls.usd, _ = Currency.objects.get_or_create(
            code="USD",
            defaults={"name": "USD", "decimals": 2, "is_active": True},
        )

        FinSV.set_current_fx(actor=cls.user, rate_syp_per_usd=Decimal("12000"))

        cls.cash = MoneyContainer.objects.create(
            name="Hardening Cash",
            container_type=MoneyContainer.ContainerType.DRAWER,
            is_active=True,
            created_by=cls.user,
        )
        feature, _ = ContainerFeature.objects.get_or_create(
            code="purchase_bills",
            defaults={"name": "Purchase Bills", "is_active": True},
        )
        if not feature.is_active:
            feature.is_active = True
            feature.save(update_fields=["is_active"])
        cls.cash.features.add(feature)

        syp_state, _ = MoneyContainerCurrency.objects.get_or_create(
            container=cls.cash,
            currency=cls.syp,
            defaults={"is_enabled": True},
        )
        usd_state, _ = MoneyContainerCurrency.objects.get_or_create(
            container=cls.cash,
            currency=cls.usd,
            defaults={"is_enabled": True},
        )
        if not syp_state.is_enabled:
            syp_state.is_enabled = True
            syp_state.save(update_fields=["is_enabled"])
        if not usd_state.is_enabled:
            usd_state.is_enabled = True
            usd_state.save(update_fields=["is_enabled"])

        cls.store, _ = ProductContainer.objects.get_or_create(
            code="store",
            defaults={"name": "Store", "is_store": True, "is_active": True},
        )

        cls.provider = Provider.objects.create(name="Hardening Provider")
        col = ProductCollection.objects.create(name="Hardening Collection")
        pset = ProductSet.objects.create(collection=col, name="Hardening Set")

        cls.product = Product.objects.create(
            name="Hardening Product",
            set=pset,
            unit_primary=UnitType.PIECE,
            allow_syp_purchasing=True,
            allow_usd_purchasing=True,
            allow_syp_sales=True,
            allow_usd_sales=True,
            default_cost_syp=Decimal("10"),
            default_cost_usd=Decimal("1"),
            default_price_syp=Decimal("20"),
            default_price_usd=Decimal("2"),
        )

        # Secondary-unit product to test qty_raw > 0 but qty_primary quantizes to 0.000
        cls.tiny_conv_product = Product.objects.create(
            name="Tiny Conversion Product",
            set=pset,
            unit_primary=UnitType.PIECE,
            unit_secondary=UnitType.GRAM,
            conversion_factor=Decimal("0.0010"),
            allow_syp_purchasing=True,
            allow_usd_purchasing=False,
            allow_syp_sales=True,
            allow_usd_sales=False,
            default_cost_syp=Decimal("10"),
            default_price_syp=Decimal("20"),
        )

    def setUp(self):
        self.client.force_login(self.user)

    def _payload(self) -> dict:
        return {
            "provider": {"id": self.provider.id},
            "container": "store",
            "money_container_id": self.cash.id,
            "currency_code": "SYP",
            "items": [
                {
                    "product_id": self.product.id,
                    "unit_index": 1,
                    "qty_raw": "1",
                    "cost": "10",
                    "price": "20",
                    "price_syp": "20",
                    "price_usd": "",
                    "currency": "SYP",
                }
            ],
            "pay": {"status": "unpaid", "paid_amount": "0"},
        }

    def _save(self, payload: dict):
        return self.client.post(
            reverse("billing_api_bill_save"),
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_rejects_negative_cost_with_400(self):
        payload = self._payload()
        payload["items"][0]["cost"] = "-1"

        resp = self._save(payload)

        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json().get("ok"))
        self.assertIn("cost must be >= 0", resp.json().get("error", ""))
        self.assertEqual(Bill.objects.count(), 0)

    def test_rejects_negative_price_with_400(self):
        payload = self._payload()
        payload["items"][0]["price"] = "-5"

        resp = self._save(payload)

        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json().get("ok"))
        self.assertIn("price must be >= 0", resp.json().get("error", ""))
        self.assertEqual(Bill.objects.count(), 0)

    def test_rejects_malformed_numeric_payload_with_400(self):
        for bad in ("NaN", "Infinity", "-Infinity", "abc", "1.2.3"):
            with self.subTest(value=bad):
                payload = self._payload()
                payload["items"][0]["cost"] = bad
                resp = self._save(payload)
                self.assertEqual(resp.status_code, 400)
                self.assertFalse(resp.json().get("ok"))

    def test_rejects_qty_that_quantizes_to_zero_after_conversion(self):
        payload = self._payload()
        payload["items"][0]["product_id"] = self.tiny_conv_product.id
        payload["items"][0]["unit_index"] = 2
        payload["items"][0]["qty_raw"] = "0.001"
        payload["items"][0]["currency"] = "SYP"
        payload["items"][0]["cost"] = "10"
        payload["items"][0]["price"] = "20"

        resp = self._save(payload)

        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json().get("ok"))
        self.assertIn("qty is too small after unit conversion", resp.json().get("error", ""))
        self.assertEqual(Bill.objects.count(), 0)

    def test_valid_payload_still_succeeds(self):
        payload = self._payload()

        resp = self._save(payload)

        self.assertEqual(resp.status_code, 200, resp.content.decode("utf-8"))
        self.assertTrue(resp.json().get("ok"))
        self.assertEqual(Bill.objects.count(), 1)

    def test_unpaid_rejects_structured_nonzero_payment(self):
        payload = self._payload()
        payload["pay"] = {
            "status": "unpaid",
            "method": "syp_only",
            "amount_syp": "100",
            "amount_usd": "0",
            "paid_amount": "100",
        }

        resp = self._save(payload)

        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json().get("ok"))
        self.assertIn("status is unpaid", resp.json().get("error", ""))
        self.assertEqual(Bill.objects.count(), 0)

    def test_partial_rejects_separate_payment_mode(self):
        payload = self._payload()
        payload["pay"] = {
            "status": "partial",
            "method": "separate",
            "amount_syp": "5",
            "amount_usd": "0",
            "paid_amount": "5",
        }

        resp = self._save(payload)

        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json().get("ok"))
        self.assertIn("separate payment mode", resp.json().get("error", ""))
        self.assertEqual(Bill.objects.count(), 0)

    def test_partial_rejects_nonzero_usd_when_syp_only_mode(self):
        payload = self._payload()
        payload["pay"] = {
            "status": "partial",
            "method": "syp_only",
            "amount_syp": "5",
            "amount_usd": "1",
            "paid_amount": "5",
        }

        resp = self._save(payload)

        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json().get("ok"))
        self.assertIn("USD amount must be 0", resp.json().get("error", ""))
        self.assertEqual(Bill.objects.count(), 0)

    def test_structured_partial_payment_shape_succeeds(self):
        payload = self._payload()
        payload["pay"] = {
            "status": "partial",
            "method": "syp_only",
            "amount_syp": "5",
            "amount_usd": "0",
            "paid_amount": "0",  # server recalculates from structured amounts
        }

        resp = self._save(payload)

        self.assertEqual(resp.status_code, 200, resp.content.decode("utf-8"))
        self.assertTrue(resp.json().get("ok"))
        self.assertEqual(Bill.objects.count(), 1)

    def test_partial_rejects_when_paid_equals_full_total(self):
        payload = self._payload()
        payload["pay"] = {
            "status": "partial",
            "method": "syp_only",
            "amount_syp": "10",
            "amount_usd": "0",
            "paid_amount": "10",
        }

        resp = self._save(payload)

        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json().get("ok"))
        self.assertIn("choose full payment", resp.json().get("error", ""))
        self.assertEqual(Bill.objects.count(), 0)

    def test_partial_rejects_when_paid_exceeds_full_total(self):
        payload = self._payload()
        payload["pay"] = {
            "status": "partial",
            "method": "syp_only",
            "amount_syp": "11",
            "amount_usd": "0",
            "paid_amount": "11",
        }

        resp = self._save(payload)

        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json().get("ok"))
        self.assertIn("cannot exceed settlement total", resp.json().get("error", ""))
        self.assertEqual(Bill.objects.count(), 0)
