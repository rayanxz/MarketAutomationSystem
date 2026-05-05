from __future__ import annotations

import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.test import TestCase
from django.urls import reverse

from audit_log.models import AuditLog
from catalog.models import Product, ProductCollection, ProductSet, UnitType
from debts.models import DebtorDebt
from financials import services as FinSV
from financials.models import Currency, MoneyContainer, MoneyContainerCurrency, ContainerFeature
from inventory import services as InvSV
from stock.models import ProductContainer, StockEntry

from pos.models import SalesBill, SalesBillRow
from pos import services_returns as ReturnSV


class PosSalesReturnTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.user = User.objects.create_user(username="cashier", password="pw12345", is_staff=True)

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
            balance_syp=Decimal("0"),
            balance_usd=Decimal("0"),
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

    def _bill_id_from_save_response(self, resp) -> int:
        data = resp.json()
        bill_ref = str(((data or {}).get("bill") or {}).get("id") or "").strip()
        self.assertTrue(bill_ref, data)
        return SalesBill.objects.only("id").get(public_id=bill_ref).id

    def _get_debtor_entry(self, bill_id: int, currency_code: str = "SYP") -> DebtorDebt | None:
        cur = (currency_code or "SYP").upper()
        src = str(bill_id)
        return (
            DebtorDebt.objects
            .filter(source_app="pos", source_model="SalesBill", currency_code=cur)
            .filter(Q(source_id=src) | Q(legacy_source_id=src) | Q(source_id=f"{src}:{cur}") | Q(legacy_source_id=f"{src}:{cur}"))
            .first()
        )

    def test_draft_return_rejects_excess_qty(self):
        product = self._create_product(
            name="Return Item",
            allow_syp=True,
            allow_usd=False,
            default_syp="50",
            default_usd="0",
        )
        self._seed_stock(product, qty="5")

        payload = {
            "id": None,
            "parked": False,
            "pay_status": "full",
            "total_amount": "0",
            "paid_amount": "0",
            "settlement_mode": "split",
            "money_container_id": self.cash.id,
            "customer_name": "Test",
            "create_new_customer": True,
            "rows": [
                {
                    "product_id": product.id,
                    "name": product.name,
                    "number": "001",
                    "qty": "2",
                    "uom_index": 1,
                    "unit_price": "50",
                    "disc_amount": "0",
                    "disc_pct": "0",
                    "currency": "SYP",
                }
            ],
        }

        resp = self._post_pos_bill(payload)
        print("STATUS:", resp.status_code)
        print("BODY:", resp.content.decode())
        self.assertEqual(resp.status_code, 200)
        bill_id = self._bill_id_from_save_response(resp)

        row = SalesBillRow.objects.get(bill_id=bill_id)

        with self.assertRaises(ValueError):
            ReturnSV.create_sales_return_draft(
                actor=self.user,
                sale_bill_id=bill_id,
                stock_container_id=self.store.id,
                items=[{"sale_row_id": row.id, "qty": Decimal("3"), "reason": ""}],
            )

    def test_post_return_reduces_debt_then_refunds_cash(self):
        product = self._create_product(
            name="Debt Item",
            allow_syp=True,
            allow_usd=False,
            default_syp="50",
            default_usd="0",
        )
        self._seed_stock(product, qty="10")

        payload = {
            "id": None,
            "parked": False,
            "pay_status": "partial",
            "total_amount": "0",
            "paid_amount": "30",
            "settlement_mode": "split",
            "money_container_id": self.cash.id,
            "customer_name": "Debt Customer",
            "create_new_customer": True,
            "rows": [
                {
                    "product_id": product.id,
                    "name": product.name,
                    "number": "001",
                    "qty": "2",
                    "uom_index": 1,
                    "unit_price": "50",
                    "disc_amount": "0",
                    "disc_pct": "0",
                    "currency": "SYP",
                }
            ],
        }

        resp = self._post_pos_bill(payload)
        print("STATUS:", resp.status_code)
        print("BODY:", resp.content.decode())
        self.assertEqual(resp.status_code, 200)
        bill_id = self._bill_id_from_save_response(resp)
        row = SalesBillRow.objects.get(bill_id=bill_id)

        debt_before = self._get_debtor_entry(bill_id, "SYP")
        self.assertIsNotNone(debt_before)
        self.assertEqual(debt_before.remaining, Decimal("70.00"))

        ret = ReturnSV.create_sales_return_draft(
            actor=self.user,
            sale_bill_id=bill_id,
            stock_container_id=self.store.id,
            items=[{"sale_row_id": row.id, "qty": Decimal("1.6"), "reason": "test"}],
        )

        self.cash.refresh_from_db(fields=["balance_syp"])
        balance_before = self.cash.balance_syp

        ReturnSV.post_sales_return(
            actor=self.user,
            return_id=ret.id,
            settle_mode="cash",
            money_container_id=self.cash.id,
        )

        debt_after = self._get_debtor_entry(bill_id, "SYP")
        self.assertIsNotNone(debt_after)
        self.assertEqual(debt_after.remaining, Decimal("0.00"))

        self.cash.refresh_from_db(fields=["balance_syp"])
        balance_after = self.cash.balance_syp
        self.assertEqual(balance_after, balance_before - Decimal("10"))

        entry = StockEntry.objects.get(product=product, container=self.store)
        self.assertEqual(entry.qty_primary, Decimal("9.600"))

    def test_multicurrency_return_refunds_each_currency(self):
        p_syp = self._create_product(
            name="SYP Item",
            allow_syp=True,
            allow_usd=False,
            default_syp="10",
            default_usd="0",
        )
        p_usd = self._create_product(
            name="USD Item",
            allow_syp=False,
            allow_usd=True,
            default_syp="0",
            default_usd="5",
        )
        self._seed_stock(p_syp, qty="5")
        self._seed_stock(p_usd, qty="5")

        payload = {
            "id": None,
            "parked": False,
            "pay_status": "full",
            "total_amount": "0",
            "paid_amount": "0",
            "settlement_mode": "split",
            "money_container_id": self.cash.id,
            "customer_name": "Multi",
            "create_new_customer": True,
            "rows": [
                {
                    "product_id": p_syp.id,
                    "name": p_syp.name,
                    "number": "S1",
                    "qty": "1",
                    "uom_index": 1,
                    "unit_price": "10",
                    "disc_amount": "0",
                    "disc_pct": "0",
                    "currency": "SYP",
                },
                {
                    "product_id": p_usd.id,
                    "name": p_usd.name,
                    "number": "U1",
                    "qty": "1",
                    "uom_index": 1,
                    "unit_price": "5",
                    "disc_amount": "0",
                    "disc_pct": "0",
                    "currency": "USD",
                },
            ],
        }

        resp = self._post_pos_bill(payload)
        self.assertEqual(resp.status_code, 200)
        bill_id = self._bill_id_from_save_response(resp)
        rows = list(SalesBillRow.objects.filter(bill_id=bill_id).order_by("id"))

        ret = ReturnSV.create_sales_return_draft(
            actor=self.user,
            sale_bill_id=bill_id,
            stock_container_id=self.store.id,
            items=[
                {"sale_row_id": rows[0].id, "qty": Decimal("1"), "reason": ""},
                {"sale_row_id": rows[1].id, "qty": Decimal("1"), "reason": ""},
            ],
        )

        self.cash.refresh_from_db(fields=["balance_syp", "balance_usd"])
        before_syp = self.cash.balance_syp
        before_usd = self.cash.balance_usd

        ReturnSV.post_sales_return(
            actor=self.user,
            return_id=ret.id,
            settle_mode="cash",
            money_container_id=self.cash.id,
        )

        self.cash.refresh_from_db(fields=["balance_syp", "balance_usd"])
        self.assertEqual(self.cash.balance_syp, before_syp - Decimal("10"))
        self.assertEqual(self.cash.balance_usd, before_usd - Decimal("5.00"))

    def test_audit_events_created(self):
        product = self._create_product(
            name="Audit Item",
            allow_syp=True,
            allow_usd=False,
            default_syp="10",
            default_usd="0",
        )
        self._seed_stock(product, qty="5")

        payload = {
            "id": None,
            "parked": False,
            "pay_status": "full",
            "total_amount": "0",
            "paid_amount": "0",
            "settlement_mode": "split",
            "money_container_id": self.cash.id,
            "customer_name": "Audit",
            "create_new_customer": True,
            "rows": [
                {
                    "product_id": product.id,
                    "name": product.name,
                    "number": "A1",
                    "qty": "1",
                    "uom_index": 1,
                    "unit_price": "10",
                    "disc_amount": "0",
                    "disc_pct": "0",
                    "currency": "SYP",
                }
            ],
        }

        resp = self._post_pos_bill(payload)
        self.assertEqual(resp.status_code, 200)
        bill_id = self._bill_id_from_save_response(resp)
        row = SalesBillRow.objects.get(bill_id=bill_id)

        ret = ReturnSV.create_sales_return_draft(
            actor=self.user,
            sale_bill_id=bill_id,
            stock_container_id=self.store.id,
            items=[{"sale_row_id": row.id, "qty": Decimal("1"), "reason": "audit"}],
        )

        ReturnSV.post_sales_return(
            actor=self.user,
            return_id=ret.id,
            settle_mode="cash",
            money_container_id=self.cash.id,
        )

        self.assertTrue(
            AuditLog.objects.filter(meta_json__icontains="pos.sale_return_draft").exists()
        )
        self.assertTrue(
            AuditLog.objects.filter(meta_json__icontains="pos.sale_return_posted").exists()
        )



