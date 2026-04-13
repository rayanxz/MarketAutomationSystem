# billing/tests/test_multicurrency_purchase_bill.py
from __future__ import annotations

import json
import importlib
from decimal import Decimal

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import AccountProfile
from billing import services as BillingSV
from billing.models import Bill, BillItem, Provider
from debts.models import DebtRecord, DebtDirection, DebtCauseType
from catalog.models import Product, ProductCollection, ProductSet, UnitType
from financials.models import Currency, MoneyContainer, MoneyContainerCurrency, Receipt, ReceiptKind, ReceiptStatus
from financials import services as FinSV
from inventory.models import ProductMovement, DEC0
from stock.models import ProductContainer

mig_backfill = importlib.import_module("catalog.migrations.0012_backfill_product_currency_defaults")


class MultiCurrencyPurchaseBillTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.actor = User.objects.create_user(username="mgr", password="123")

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

        cls.cash = MoneyContainer.objects.create(
            name="Test Cash Drawer",
            container_type=MoneyContainer.ContainerType.DRAWER,
            is_active=True,
            created_by=cls.actor,
        )
        for code in ("SYP", "USD"):
            cur = Currency.objects.get(code=code)
            MoneyContainerCurrency.objects.get_or_create(
                container=cls.cash,
                currency=cur,
                defaults={"is_enabled": True},
            )

        cls.provider = Provider.objects.create(name="Test Provider")

        col = ProductCollection.objects.create(name="Test Collection")
        cls.prod_set = ProductSet.objects.create(collection=col, name="Test Set")

        FinSV.set_current_fx(actor=cls.actor, rate_syp_per_usd=Decimal("15000"))

    def setUp(self):
        ok = self.client.login(username="mgr", password="123")
        self.assertTrue(ok)

    def _product(
        self,
        *,
        name: str,
        allow_syp_purch: bool,
        allow_usd_purch: bool,
        allow_syp_sales: bool | None = None,
        allow_usd_sales: bool | None = None,
    ) -> Product:
        return Product.objects.create(
            name=name,
            set=self.prod_set,
            unit_primary=UnitType.PIECE,
            allow_syp_purchasing=allow_syp_purch,
            allow_usd_purchasing=allow_usd_purch,
            allow_syp_sales=allow_syp_sales if allow_syp_sales is not None else allow_syp_purch,
            allow_usd_sales=allow_usd_sales if allow_usd_sales is not None else allow_usd_purch,
            default_cost_syp=Decimal("10") if allow_syp_purch else Decimal("0"),
            default_cost_usd=Decimal("1") if allow_usd_purch else Decimal("0"),
            default_price_syp=Decimal("20") if (allow_syp_sales if allow_syp_sales is not None else allow_syp_purch) else Decimal("0"),
            default_price_usd=Decimal("2") if (allow_usd_sales if allow_usd_sales is not None else allow_usd_purch) else Decimal("0"),
        )

    def _create_bill(self, *, product: Product, currency: str | None, cost: str, price_syp: str = "", price_usd: str = ""):
        effective_currency = currency or product.get_effective_default_purchase_currency()
        items = [
            {
                "product_id": product.id,
                "unit_index": 1,
                "qty_raw": "1",
                "cost": cost,
                "currency": currency or "",
                "price_syp": price_syp,
                "price_usd": price_usd,
            }
        ]
        return BillingSV.create_bill(
            actor=self.actor,
            provider_id=self.provider.id,
            status="unpaid",
            paid_amount=Decimal("0"),
            items=items,
            money_container_id=self.cash.id,
            settlement_currency=effective_currency,
        )

    def test_reject_disabled_purchase_currency(self):
        prod = self._product(name="SYP only", allow_syp_purch=True, allow_usd_purch=False)

        with self.assertRaises(ValueError):
            self._create_bill(product=prod, currency="USD", cost="5")

        self.assertEqual(Bill.objects.count(), 0)
        self.assertEqual(ProductMovement.objects.count(), 0)

        prod2 = self._product(name="USD only", allow_syp_purch=False, allow_usd_purch=True)
        with self.assertRaises(ValueError):
            self._create_bill(product=prod2, currency="SYP", cost="5")

    def test_default_purchase_currency_resolves_to_only_enabled(self):
        prod = self._product(name="USD only", allow_syp_purch=False, allow_usd_purch=True)
        bill = self._create_bill(product=prod, currency=None, cost="5")
        item = BillItem.objects.get(bill=bill)
        self.assertEqual(item.currency, "USD")

    def test_cost_updates_for_selected_currency(self):
        prod = self._product(name="Both", allow_syp_purch=True, allow_usd_purch=True)
        prod.default_cost_syp = Decimal("20000")
        prod.latest_cost_syp = Decimal("15000")
        prod.default_cost_usd = Decimal("2")
        prod.latest_cost_usd = Decimal("1")
        prod.save(update_fields=["default_cost_syp", "latest_cost_syp", "default_cost_usd", "latest_cost_usd"])

        self._create_bill(product=prod, currency="SYP", cost="30000")
        prod.refresh_from_db()

        self.assertEqual(prod.latest_cost_syp, Decimal("30000"))
        self.assertEqual(prod.default_cost_syp, Decimal("30000"))
        self.assertEqual(prod.latest_cost_usd, Decimal("1"))
        self.assertEqual(prod.default_cost_usd, Decimal("2"))

    def test_price_updates_only_defaults_not_latest(self):
        prod = self._product(name="Both", allow_syp_purch=True, allow_usd_purch=True, allow_syp_sales=True, allow_usd_sales=True)
        prod.default_price_syp = Decimal("25000")
        prod.default_price_usd = Decimal("3")
        prod.latest_price_syp = Decimal("24000")
        prod.latest_price_usd = Decimal("2.5")
        prod.save(update_fields=["default_price_syp", "default_price_usd", "latest_price_syp", "latest_price_usd"])

        self._create_bill(product=prod, currency="SYP", cost="100", price_syp="26000", price_usd="")
        prod.refresh_from_db()
        self.assertEqual(prod.default_price_syp, Decimal("26000"))
        self.assertEqual(prod.default_price_usd, Decimal("3"))
        self.assertEqual(prod.latest_price_syp, Decimal("24000"))
        self.assertEqual(prod.latest_price_usd, Decimal("2.5"))

        self._create_bill(product=prod, currency="USD", cost="5", price_syp="27000", price_usd="4")
        prod.refresh_from_db()
        self.assertEqual(prod.default_price_syp, Decimal("27000"))
        self.assertEqual(prod.default_price_usd, Decimal("4"))
        self.assertEqual(prod.latest_price_syp, Decimal("24000"))
        self.assertEqual(prod.latest_price_usd, Decimal("2.5"))

    def test_product_search_payload_includes_defaults(self):
        prod = self._product(name="Search Me", allow_syp_purch=True, allow_usd_purch=True)
        prod.default_purchase_currency = None
        prod.save(update_fields=["default_purchase_currency"])

        url = reverse("billing_api_products_search")
        resp = self.client.get(url, {"q": "Search Me", "mode": "name"})
        self.assertEqual(resp.status_code, 200, resp.content.decode("utf-8"))
        data = resp.json()
        self.assertTrue(data.get("ok"), data)

        item = next((it for it in data.get("items", []) if it.get("id") == prod.id), None)
        self.assertIsNotNone(item)
        self.assertIn("allow_syp_purchasing", item)
        self.assertIn("allow_usd_purchasing", item)
        self.assertIn("default_cost_syp", item)
        self.assertIn("default_cost_usd", item)
        self.assertEqual(item.get("effective_default_purchase_currency"), "SYP")

    def test_effective_default_currency_helpers(self):
        prod = self._product(name="Helper SYP", allow_syp_purch=True, allow_usd_purch=False)
        self.assertEqual(prod.get_effective_default_purchase_currency(), "SYP")

        prod2 = self._product(name="Helper USD", allow_syp_purch=False, allow_usd_purch=True)
        self.assertEqual(prod2.get_effective_default_purchase_currency(), "USD")

    def test_partial_payment_creates_one_central_debt_with_receipt(self):
        prod = self._product(name="Pay SYP", allow_syp_purch=True, allow_usd_purch=False)
        items = [
            {
                "product_id": prod.id,
                "unit_index": 1,
                "qty_raw": "1",
                "cost": "10",
                "currency": "SYP",
            }
        ]
        bill = BillingSV.create_bill(
            actor=self.actor,
            provider_id=self.provider.id,
            status="partial",
            paid_amount=Decimal("5"),
            items=items,
            money_container_id=self.cash.id,
            settlement_currency="SYP",
        )

        debt = DebtRecord.objects.get(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id=bill.public_id,
        )
        self.assertEqual(debt.remaining_syp, Decimal("5"))
        self.assertEqual(debt.remaining_usd, Decimal("0"))
        self.assertEqual(debt.total_syp, Decimal("5"))
        self.assertEqual(debt.total_usd, Decimal("0"))
        self.assertEqual(debt.status, "open")

        receipts = Receipt.objects.filter(
            source_app="billing",
            source_model="Bill",
            source_id=str(bill.id),
            status=ReceiptStatus.POSTED,
        )
        self.assertEqual(receipts.count(), 1)
        receipt = receipts.first()
        self.assertEqual(receipt.kind, ReceiptKind.COUNTERPARTY_SETTLE)
        self.assertEqual(receipt.status, ReceiptStatus.POSTED)

        prod3 = self._product(name="Helper Both", allow_syp_purch=True, allow_usd_purch=True)
        prod3.default_purchase_currency = None
        prod3.save(update_fields=["default_purchase_currency"])
        self.assertEqual(prod3.get_effective_default_purchase_currency(), "SYP")
        prod3.default_purchase_currency = "USD"
        prod3.save(update_fields=["default_purchase_currency"])
        self.assertEqual(prod3.get_effective_default_purchase_currency(), "USD")

        prod4 = self._product(name="Helper None", allow_syp_purch=False, allow_usd_purch=False)
        self.assertEqual(prod4.get_effective_default_purchase_currency(), "SYP")

    def test_backfill_migration_does_not_overwrite_existing(self):
        if not hasattr(Product, "cost"):
            self.skipTest("Legacy backfill migration targets removed Product.cost/price fields.")
        prod = self._product(name="Backfill", allow_syp_purch=True, allow_usd_purch=True)
        prod.default_cost_syp = Decimal("111")
        prod.default_cost_usd = Decimal("9")
        prod.default_price_syp = Decimal("222")
        prod.default_price_usd = Decimal("8")
        prod.latest_cost_syp = Decimal("333")
        prod.latest_cost_usd = Decimal("7")
        prod.latest_price_syp = Decimal("444")
        prod.latest_price_usd = Decimal("6")
        prod.default_purchase_currency = "USD"
        prod.default_sale_currency = "USD"
        prod.save(
            update_fields=[
                "default_cost_syp",
                "default_cost_usd",
                "default_price_syp",
                "default_price_usd",
                "latest_cost_syp",
                "latest_cost_usd",
                "latest_price_syp",
                "latest_price_usd",
                "default_purchase_currency",
                "default_sale_currency",
            ]
        )

        mig_backfill.forwards(django_apps, None)
        prod.refresh_from_db()

        self.assertEqual(prod.default_cost_syp, Decimal("111"))
        self.assertEqual(prod.default_cost_usd, Decimal("9"))
        self.assertEqual(prod.default_price_syp, Decimal("222"))
        self.assertEqual(prod.default_price_usd, Decimal("8"))
        self.assertEqual(prod.latest_cost_syp, Decimal("333"))
        self.assertEqual(prod.latest_cost_usd, Decimal("7"))
        self.assertEqual(prod.latest_price_syp, Decimal("444"))
        self.assertEqual(prod.latest_price_usd, Decimal("6"))
        self.assertEqual(prod.default_purchase_currency, "USD")
        self.assertEqual(prod.default_sale_currency, "USD")


