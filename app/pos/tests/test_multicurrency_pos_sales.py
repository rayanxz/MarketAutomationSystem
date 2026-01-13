from __future__ import annotations

import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from catalog.models import Product, ProductCollection, ProductSet, UnitType
from financials.models import Currency, MoneyContainer, MoneyContainerCurrency, ContainerFeature, Receipt
from financials import services as FinSV
from inventory import services as InvSV
from stock.models import ProductContainer
from pos.models import SalesBill


class PosMultiCurrencySalesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.user = User.objects.create_user(username="cashier", password="pw12345")

        cls.syp, _ = Currency.objects.get_or_create(
            code="SYP",
            defaults={"name": "Syrian Pound", "decimals": 0, "is_active": True},
        )
        cls.usd, _ = Currency.objects.get_or_create(
            code="USD",
            defaults={"name": "US Dollar", "decimals": 2, "is_active": True},
        )

        FinSV.set_current_fx(actor=cls.user, rate_syp_per_usd=Decimal("10000"))

        cls.store, _ = ProductContainer.objects.get_or_create(
            code="store",
            defaults={"name": "Store", "is_store": True, "is_active": True},
        )

        cls.pos_feature, _ = ContainerFeature.objects.get_or_create(
            code="pos_sales",
            defaults={"name": "POS Sales", "is_active": True, "sort_order": 10},
        )

        cls.cash = MoneyContainer.objects.create(
            name="POS Drawer",
            container_type=MoneyContainer.ContainerType.DRAWER,
            is_active=True,
            created_by=cls.user,
        )
        cls.cash.features.add(cls.pos_feature)
        cls.cash.allowed_users.add(cls.user)

        MoneyContainerCurrency.objects.update_or_create(
            container=cls.cash,
            currency=cls.syp,
            defaults={"is_enabled": True},
        )
        MoneyContainerCurrency.objects.update_or_create(
            container=cls.cash,
            currency=cls.usd,
            defaults={"is_enabled": True},
        )

        col = ProductCollection.objects.create(name="POS Collection")
        cls.prod_set = ProductSet.objects.create(collection=col, name="POS Set")

    def setUp(self):
        ok = self.client.login(username="cashier", password="pw12345")
        self.assertTrue(ok)

    def _create_product(
        self,
        *,
        name: str,
        allow_syp: bool,
        allow_usd: bool,
        default_syp: str,
        default_usd: str,
    ) -> Product:
        return Product.objects.create(
            name=name,
            set=self.prod_set,
            unit_primary=UnitType.PIECE,
            cost=Decimal("0"),
            price=Decimal(default_syp),
            allow_syp_sales=allow_syp,
            allow_usd_sales=allow_usd,
            allow_syp_purchasing=True,
            allow_usd_purchasing=True,
            default_sale_currency=None,
            default_price_syp=Decimal(default_syp),
            default_price_usd=Decimal(default_usd),
        )

    def _seed_stock(self, product: Product, qty: str = "10"):
        InvSV.record_purchase_item(
            actor=self.user,
            product=product,
            unit_index=1,
            qty_primary=Decimal(qty),
            unit_cost=Decimal("1"),
            source_app="tests",
            source_model="Seed",
            source_id="1",
            container=self.store,
        )

    def _post_pos_bill(self, payload: dict):
        url = reverse("pos:api_bill_save")
        return self.client.post(
            url,
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_totals_split_for_mixed_currency(self):
        p_syp = self._create_product(
            name="SYP Only",
            allow_syp=True,
            allow_usd=False,
            default_syp="1000",
            default_usd="0",
        )
        p_usd = self._create_product(
            name="USD Only",
            allow_syp=False,
            allow_usd=True,
            default_syp="0",
            default_usd="5",
        )

        payload = {
            "id": None,
            "parked": True,
            "pay_status": "none",
            "paid_amount": "0",
            "total_amount": "0",
            "settlement_mode": "split",
            "rows": [
                {
                    "product_id": p_syp.id,
                    "name": p_syp.name,
                    "number": p_syp.display_code,
                    "qty": "1",
                    "uom_index": 1,
                    "unit_price": "1000",
                    "currency": "SYP",
                    "disc_amount": "0",
                    "disc_pct": "0",
                },
                {
                    "product_id": p_usd.id,
                    "name": p_usd.name,
                    "number": p_usd.display_code,
                    "qty": "2",
                    "uom_index": 1,
                    "unit_price": "5",
                    "currency": "USD",
                    "disc_amount": "0",
                    "disc_pct": "0",
                },
            ],
        }
        resp = self._post_pos_bill(payload)
        self.assertEqual(resp.status_code, 200, resp.content.decode())
        data = resp.json()
        self.assertTrue(data.get("ok"))

        bill = SalesBill.objects.get(pk=data["bill"]["id"])
        self.assertEqual(bill.total_syp, Decimal("1000"))
        self.assertEqual(bill.total_usd, Decimal("10"))

    def test_full_paid_sale_creates_receipt_and_updates_container(self):
        p_syp = self._create_product(
            name="SYP Only",
            allow_syp=True,
            allow_usd=False,
            default_syp="1000",
            default_usd="0",
        )
        p_usd = self._create_product(
            name="USD Only",
            allow_syp=False,
            allow_usd=True,
            default_syp="0",
            default_usd="5",
        )
        self._seed_stock(p_syp)
        self._seed_stock(p_usd)

        payload = {
            "id": None,
            "parked": False,
            "pay_status": "full",
            "paid_amount": "0",
            "total_amount": "0",
            "settlement_mode": "split",
            "money_container_id": self.cash.id,
            "rows": [
                {
                    "product_id": p_syp.id,
                    "name": p_syp.name,
                    "number": p_syp.display_code,
                    "qty": "1",
                    "uom_index": 1,
                    "unit_price": "1000",
                    "currency": "SYP",
                    "disc_amount": "0",
                    "disc_pct": "0",
                },
                {
                    "product_id": p_usd.id,
                    "name": p_usd.name,
                    "number": p_usd.display_code,
                    "qty": "2",
                    "uom_index": 1,
                    "unit_price": "5",
                    "currency": "USD",
                    "disc_amount": "0",
                    "disc_pct": "0",
                },
            ],
        }
        resp = self._post_pos_bill(payload)
        self.assertEqual(resp.status_code, 200, resp.content.decode())
        data = resp.json()
        self.assertTrue(data.get("ok"))

        self.cash.refresh_from_db()
        self.assertEqual(self.cash.balance_syp, Decimal("1000"))
        self.assertEqual(self.cash.balance_usd, Decimal("10"))

        self.assertTrue(
            Receipt.objects.filter(source_app="pos", source_model="SalesBill", source_id=str(data["bill"]["id"])).exists()
        )

    def test_currency_validation_blocks_usd(self):
        p_syp = self._create_product(
            name="SYP Only",
            allow_syp=True,
            allow_usd=False,
            default_syp="1000",
            default_usd="0",
        )

        payload = {
            "id": None,
            "parked": True,
            "pay_status": "none",
            "paid_amount": "0",
            "total_amount": "0",
            "settlement_mode": "split",
            "rows": [
                {
                    "product_id": p_syp.id,
                    "name": p_syp.name,
                    "number": p_syp.display_code,
                    "qty": "1",
                    "uom_index": 1,
                    "unit_price": "1000",
                    "currency": "USD",
                    "disc_amount": "0",
                    "disc_pct": "0",
                },
            ],
        }
        resp = self._post_pos_bill(payload)
        self.assertEqual(resp.status_code, 400, resp.content.decode())
        self.assertFalse(SalesBill.objects.exists())

    def test_usd_price_missing_rejected(self):
        p_usd = self._create_product(
            name="USD Missing",
            allow_syp=True,
            allow_usd=True,
            default_syp="1000",
            default_usd="0",
        )

        payload = {
            "id": None,
            "parked": True,
            "pay_status": "none",
            "paid_amount": "0",
            "total_amount": "0",
            "settlement_mode": "split",
            "rows": [
                {
                    "product_id": p_usd.id,
                    "name": p_usd.name,
                    "number": p_usd.display_code,
                    "qty": "1",
                    "uom_index": 1,
                    "unit_price": "0",
                    "currency": "USD",
                    "disc_amount": "0",
                    "disc_pct": "0",
                },
            ],
        }
        resp = self._post_pos_bill(payload)
        self.assertEqual(resp.status_code, 400, resp.content.decode())
        self.assertIn("USD_PRICE_MISSING", resp.content.decode())
        self.assertFalse(SalesBill.objects.exists())

    def test_usd_price_allowed_when_present(self):
        p_usd = self._create_product(
            name="USD OK",
            allow_syp=True,
            allow_usd=True,
            default_syp="1000",
            default_usd="2",
        )

        payload = {
            "id": None,
            "parked": True,
            "pay_status": "none",
            "paid_amount": "0",
            "total_amount": "0",
            "settlement_mode": "split",
            "rows": [
                {
                    "product_id": p_usd.id,
                    "name": p_usd.name,
                    "number": p_usd.display_code,
                    "qty": "1",
                    "uom_index": 1,
                    "unit_price": "2",
                    "currency": "USD",
                    "disc_amount": "0",
                    "disc_pct": "0",
                },
            ],
        }
        resp = self._post_pos_bill(payload)
        self.assertEqual(resp.status_code, 200, resp.content.decode())
        bill = SalesBill.objects.get(pk=resp.json()["bill"]["id"])
        self.assertEqual(bill.total_usd, Decimal("2"))

    def test_product_payload_defaults(self):
        p = self._create_product(
            name="Dual",
            allow_syp=True,
            allow_usd=True,
            default_syp="2500",
            default_usd="2",
        )
        url = reverse("pos:api_lookup_id", kwargs={"pk": p.id})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200, resp.content.decode())
        data = resp.json()
        self.assertTrue(data.get("ok"))
        prod = data["product"]
        self.assertEqual(prod["effective_default_sale_currency"], "SYP")
        self.assertEqual(Decimal(prod["price"]), Decimal("2500"))
