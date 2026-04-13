from __future__ import annotations

import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import AccountProfile
from catalog.models import Product, ProductCollection, ProductSet, UnitType
from debts.models import CreditorDebt, DebtorDebt, PartyType
from financials import services as FinSV
from financials.models import ContainerFeature, Currency, MoneyContainer, MoneyContainerCurrency
from inventory import services as InvSV
from inventory.models import DEC0, q3
from pos import services_returns as ReturnSV
from pos.models import SalesBill, SalesBillRow, SalesReturn
from stock.models import ProductContainer, StockEntry


class PosSequencePathNumericIntegrityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.user = User.objects.create_user(username="pos_seq_cashier", password="pw12345")
        AccountProfile.objects.create(user=cls.user, role=AccountProfile.Role.CASHIER)

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

        pos_feature, _ = ContainerFeature.objects.get_or_create(
            code="pos_sales",
            defaults={"name": "POS Sales", "is_active": True, "sort_order": 10},
        )
        cls.cash = MoneyContainer.objects.create(
            name="POS Sequence Cash",
            container_type=MoneyContainer.ContainerType.DRAWER,
            is_active=True,
            created_by=cls.user,
            balance_syp=Decimal("0"),
            balance_usd=Decimal("0"),
        )
        cls.cash.features.add(pos_feature)
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

        col = ProductCollection.objects.create(name="POS Sequence Collection")
        cls.prod_set = ProductSet.objects.create(collection=col, name="POS Sequence Set")

    def setUp(self):
        ok = self.client.login(username="pos_seq_cashier", password="pw12345")
        self.assertTrue(ok)

    def _product(
        self,
        *,
        name: str,
        allow_syp: bool,
        allow_usd: bool,
        default_price_syp: str,
        default_price_usd: str,
    ) -> Product:
        return Product.objects.create(
            name=name,
            set=self.prod_set,
            unit_primary=UnitType.PIECE,
            allow_syp_sales=allow_syp,
            allow_usd_sales=allow_usd,
            allow_syp_purchasing=True,
            allow_usd_purchasing=True,
            default_price_syp=Decimal(default_price_syp),
            default_price_usd=Decimal(default_price_usd),
            default_cost_syp=Decimal("500"),
            default_cost_usd=Decimal("2"),
        )

    def _seed_stock(self, product: Product, *, qty: str, unit_cost: str, cost_currency: str) -> None:
        InvSV.record_purchase_item(
            actor=self.user,
            product=product,
            unit_index=1,
            qty_primary=Decimal(qty),
            unit_cost=Decimal(unit_cost),
            cost_currency=cost_currency,
            source_app="tests",
            source_model="PosSeqSeed",
            source_id=f"seed-{product.id}",
            container=self.store,
        )

    def _post_bill(self, payload: dict) -> dict:
        resp = self.client.post(
            reverse("pos:api_bill_save"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, resp.content.decode())
        body = resp.json()
        self.assertTrue(body.get("ok"), body)
        return body

    def _bill_id_from_save_payload(self, data: dict) -> int:
        bill_ref = str(((data or {}).get("bill") or {}).get("id") or "").strip()
        self.assertTrue(bill_ref, data)
        return SalesBill.objects.only("id").get(public_id=bill_ref).id

    def test_partial_sale_cash_return_reduces_debt_then_refunds_remaining_cash(self):
        product = self._product(
            name="POS Seq Partial Sale Product",
            allow_syp=True,
            allow_usd=False,
            default_price_syp="100",
            default_price_usd="0",
        )
        self._seed_stock(product, qty="5", unit_cost="50", cost_currency="SYP")
        stock_before = StockEntry.objects.get(product=product, container=self.store).qty_primary
        bal_before = FinSV.container_balance(container_id=self.cash.id)

        bill_data = self._post_bill(
            {
                "id": None,
                "parked": False,
                "pay_status": "partial",
                "paid_amount": "40",
                "total_amount": "0",
                "settlement_mode": "all_syp",
                "money_container_id": self.cash.id,
                "customer_name": "POS Seq Customer",
                "create_new_customer": True,
                "rows": [
                    {
                        "product_id": product.id,
                        "name": product.name,
                        "number": str(product.id),
                        "qty": "1",
                        "uom_index": 1,
                        "unit_price": "100",
                        "currency": "SYP",
                        "disc_amount": "0",
                        "disc_pct": "0",
                    }
                ],
            }
        )
        bill_id = self._bill_id_from_save_payload(bill_data)
        bill = SalesBill.objects.get(pk=bill_id)
        debtor = DebtorDebt.objects.get(
            source_app="pos",
            source_model="SalesBill",
            source_id=str(bill_id),
            currency_code="SYP",
            party_type=PartyType.CUSTOMER,
        )
        self.assertEqual(debtor.total, Decimal("100"))
        self.assertEqual(debtor.paid_amount, Decimal("40"))
        self.assertEqual(debtor.remaining, Decimal("60"))

        bal_after_sale = FinSV.container_balance(container_id=self.cash.id)
        self.assertEqual(
            bal_after_sale.get("SYP", DEC0),
            bal_before.get("SYP", DEC0) + Decimal("40"),
        )

        row = SalesBillRow.objects.get(bill_id=bill_id)
        ret = ReturnSV.create_sales_return_draft(
            actor=self.user,
            sale_bill_id=bill.id,
            stock_container_id=self.store.id,
            items=[{"sale_row_id": row.id, "qty": Decimal("1"), "reason": "cash return after partial sale"}],
        )
        posted = ReturnSV.post_sales_return(
            actor=self.user,
            return_id=ret.id,
            settle_mode="cash",
            money_container_id=self.cash.id,
        )
        self.assertEqual(posted.status, SalesReturn.Status.POSTED)

        debtor.refresh_from_db()
        self.assertEqual(debtor.total, Decimal("40"))
        self.assertEqual(debtor.paid_amount, Decimal("40"))
        self.assertEqual(debtor.remaining, Decimal("0"))
        self.assertEqual(debtor.status, DebtorDebt.Status.CLOSED)

        self.assertFalse(
            CreditorDebt.objects.filter(
                source_app="pos",
                source_model="SalesReturn",
                source_id=str(ret.id),
                currency_code="SYP",
                party_type=PartyType.CUSTOMER,
            ).exists()
        )

        bal_after_return = FinSV.container_balance(container_id=self.cash.id)
        self.assertEqual(bal_after_return.get("SYP", DEC0), bal_before.get("SYP", DEC0))
        stock_after = StockEntry.objects.get(product=product, container=self.store).qty_primary
        self.assertEqual(q3(stock_after), q3(stock_before))

    def test_full_split_sale_credit_return_creates_per_currency_customer_credit(self):
        p_syp = self._product(
            name="POS Seq Split SYP",
            allow_syp=True,
            allow_usd=False,
            default_price_syp="1000",
            default_price_usd="0",
        )
        p_usd = self._product(
            name="POS Seq Split USD",
            allow_syp=False,
            allow_usd=True,
            default_price_syp="0",
            default_price_usd="5",
        )
        self._seed_stock(p_syp, qty="5", unit_cost="500", cost_currency="SYP")
        self._seed_stock(p_usd, qty="5", unit_cost="2", cost_currency="USD")

        stock_before_syp = StockEntry.objects.get(product=p_syp, container=self.store).qty_primary
        stock_before_usd = StockEntry.objects.get(product=p_usd, container=self.store).qty_primary
        bal_before = FinSV.container_balance(container_id=self.cash.id)

        bill_data = self._post_bill(
            {
                "id": None,
                "parked": False,
                "pay_status": "full",
                "paid_amount": "0",
                "total_amount": "0",
                "settlement_mode": "split",
                "money_container_id": self.cash.id,
                "customer_name": "POS Seq Split Customer",
                "create_new_customer": True,
                "rows": [
                    {
                        "product_id": p_syp.id,
                        "name": p_syp.name,
                        "number": str(p_syp.id),
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
                        "number": str(p_usd.id),
                        "qty": "2",
                        "uom_index": 1,
                        "unit_price": "5",
                        "currency": "USD",
                        "disc_amount": "0",
                        "disc_pct": "0",
                    },
                ],
            }
        )
        bill_id = self._bill_id_from_save_payload(bill_data)
        bill = SalesBill.objects.get(pk=bill_id)
        self.assertEqual(bill.total_syp, Decimal("1000"))
        self.assertEqual(bill.total_usd, Decimal("10"))
        self.assertEqual(
            DebtorDebt.objects.filter(
                source_app="pos",
                source_model="SalesBill",
                source_id=str(bill_id),
            ).count(),
            0,
        )

        bal_after_sale = FinSV.container_balance(container_id=self.cash.id)
        self.assertEqual(bal_after_sale.get("SYP", DEC0), bal_before.get("SYP", DEC0) + Decimal("1000"))
        self.assertEqual(bal_after_sale.get("USD", DEC0), bal_before.get("USD", DEC0) + Decimal("10"))

        rows = list(SalesBillRow.objects.filter(bill_id=bill_id).order_by("id"))
        ret = ReturnSV.create_sales_return_draft(
            actor=self.user,
            sale_bill_id=bill_id,
            stock_container_id=self.store.id,
            items=[
                {"sale_row_id": rows[0].id, "qty": Decimal("1"), "reason": "split credit syp"},
                {"sale_row_id": rows[1].id, "qty": Decimal("2"), "reason": "split credit usd"},
            ],
        )
        posted = ReturnSV.post_sales_return(
            actor=self.user,
            return_id=ret.id,
            settle_mode="credit",
            money_container_id=None,
        )
        self.assertEqual(posted.status, SalesReturn.Status.POSTED)

        credit_syp = CreditorDebt.objects.get(
            source_app="pos",
            source_model="SalesReturn",
            source_id=str(ret.id),
            currency_code="SYP",
            party_type=PartyType.CUSTOMER,
            customer_id=bill.customer_id,
        )
        credit_usd = CreditorDebt.objects.get(
            source_app="pos",
            source_model="SalesReturn",
            source_id=str(ret.id),
            currency_code="USD",
            party_type=PartyType.CUSTOMER,
            customer_id=bill.customer_id,
        )
        self.assertEqual(credit_syp.total, Decimal("1000"))
        self.assertEqual(credit_syp.collected, DEC0)
        self.assertEqual(credit_usd.total, Decimal("10"))
        self.assertEqual(credit_usd.collected, DEC0)

        bal_after_return = FinSV.container_balance(container_id=self.cash.id)
        self.assertEqual(bal_after_return.get("SYP", DEC0), bal_after_sale.get("SYP", DEC0))
        self.assertEqual(bal_after_return.get("USD", DEC0), bal_after_sale.get("USD", DEC0))

        stock_after_syp = StockEntry.objects.get(product=p_syp, container=self.store).qty_primary
        stock_after_usd = StockEntry.objects.get(product=p_usd, container=self.store).qty_primary
        self.assertEqual(q3(stock_after_syp), q3(stock_before_syp))
        self.assertEqual(q3(stock_after_usd), q3(stock_before_usd))

