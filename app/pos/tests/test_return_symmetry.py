from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.db import transaction

from audit_log.models import AuditLog
from billing import services as BillingSV
from billing.models import Provider, ProviderReturn
from catalog.models import Product, ProductCollection, ProductSet, UnitType
from debts import services as DebtSV
from debts.models import CreditorDebt, DebtorDebt, PartyType
from django.db.models import Q
from financials import services as FinSV
from financials.models import Currency, MoneyContainer, MoneyContainerCurrency, ContainerFeature
from inventory import services as InvSV
from inventory.models import DEC0, q3
from stock.models import ProductContainer, StockEntry

from pos.models import CustomerProfile, SalesBill, SalesBillRow
from pos import services_returns as ReturnSV


class PosReturnSymmetryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.user = User.objects.create_user(username="symmetry", password="pw12345", is_staff=True)

        cls.syp, _ = Currency.objects.get_or_create(
            code="SYP",
            defaults={"name": "Syrian Pound", "decimals": 0, "is_active": True},
        )
        cls.usd, _ = Currency.objects.get_or_create(
            code="USD",
            defaults={"name": "US Dollar", "decimals": 2, "is_active": True},
        )

        FinSV.set_current_fx(actor=cls.user, rate_syp_per_usd=Decimal("10000"))

        cls.store_provider, _ = ProductContainer.objects.get_or_create(
            code="prov_store",
            defaults={"name": "Provider Store", "is_store": False, "is_active": True},
        )
        cls.store_sales, _ = ProductContainer.objects.get_or_create(
            code="sales_store",
            defaults={"name": "Sales Store", "is_store": False, "is_active": True},
        )

        cls.pos_feature, _ = ContainerFeature.objects.get_or_create(
            code="pos_sales",
            defaults={"name": "POS Sales", "is_active": True, "sort_order": 10},
        )

        cls.cash_provider = MoneyContainer.objects.create(
            name="Provider Cash",
            container_type=MoneyContainer.ContainerType.DRAWER,
            is_active=True,
            created_by=cls.user,
            balance_syp=Decimal("1000000"),
            balance_usd=Decimal("100"),
        )
        cls.cash_provider.features.add(cls.pos_feature)
        cls.cash_provider.allowed_users.add(cls.user)

        cls.cash_sales = MoneyContainer.objects.create(
            name="Sales Cash",
            container_type=MoneyContainer.ContainerType.DRAWER,
            is_active=True,
            created_by=cls.user,
            balance_syp=Decimal("1000000"),
            balance_usd=Decimal("100"),
        )
        cls.cash_sales.features.add(cls.pos_feature)
        cls.cash_sales.allowed_users.add(cls.user)

        MoneyContainerCurrency.objects.update_or_create(
            container=cls.cash_provider,
            currency=cls.syp,
            defaults={"is_enabled": True},
        )
        MoneyContainerCurrency.objects.update_or_create(
            container=cls.cash_provider,
            currency=cls.usd,
            defaults={"is_enabled": True},
        )
        MoneyContainerCurrency.objects.update_or_create(
            container=cls.cash_sales,
            currency=cls.syp,
            defaults={"is_enabled": True},
        )
        MoneyContainerCurrency.objects.update_or_create(
            container=cls.cash_sales,
            currency=cls.usd,
            defaults={"is_enabled": True},
        )

        col = ProductCollection.objects.create(name="Return Symmetry")
        cls.prod_set = ProductSet.objects.create(collection=col, name="Return Symmetry Set")

        cls.provider = Provider.objects.create(name="Provider A")
        cls.customer = CustomerProfile.objects.create(name="Customer A", created_by=cls.user)

    def _create_product(self, name: str) -> Product:
        return Product.objects.create(
            name=name,
            set=self.prod_set,
            unit_primary=UnitType.PIECE,
            cost=Decimal("10"),
            price=Decimal("15"),
            allow_syp_sales=False,
            allow_usd_sales=True,
            allow_syp_purchasing=True,
            allow_usd_purchasing=True,
            default_sale_currency="USD",
            default_price_syp=Decimal("0"),
            default_price_usd=Decimal("15"),
        )

    def _seed_stock(self, product: Product, container: ProductContainer) -> None:
        InvSV.record_purchase_item(
            actor=self.user,
            product=product,
            unit_index=1,
            qty_primary=Decimal("10"),
            unit_cost=Decimal("10"),
            cost_currency="USD",
            source_app="tests",
            source_model="Seed",
            source_id="1",
            container=container,
        )

    def _record_sale(self, product: Product, container: ProductContainer, bill: SalesBill) -> SalesBillRow:
        row = SalesBillRow.objects.create(
            bill=bill,
            product_id=product.id,
            product_name=product.name,
            product_number="001",
            qty=Decimal("6"),
            uom_index=1,
            unit_price=Decimal("15"),
            sale_currency="USD",
            disc_amount=Decimal("0"),
            disc_pct=Decimal("0"),
        )

        InvSV.record_sale_item(
            actor=self.user,
            product=product,
            unit_index=1,
            qty_primary=Decimal("6"),
            unit_cost=Decimal("10"),
            source_app="pos",
            source_model="SalesBill",
            source_id=str(bill.id),
            container=container,
        )

        return row

    def _new_sale_bill(self) -> SalesBill:
        return SalesBill.objects.create(
            customer=self.customer,
            customer_name=self.customer.name,
            cashier=self.user,
            pay_status=SalesBill.PAY_PARTIAL,
            total_amount=Decimal("0"),
            total_syp=Decimal("0"),
            total_usd=Decimal("0"),
            paid_amount=Decimal("0"),
            settlement_mode=SalesBill.SETTLE_SPLIT,
            settlement_currency=None,
            fx_rate_used=Decimal("10000"),
            parked=False,
            finalized=True,
            money_container=self.cash_sales,
        )

    def test_debt_only_return_symmetry(self):
        prod_p = self._create_product("Provider Debt Item")
        prod_s = self._create_product("Sales Debt Item")

        self._seed_stock(prod_p, self.store_provider)
        self._seed_stock(prod_s, self.store_sales)

        # ----- Provider return (unpaid) -----
        audit_before_p = AuditLog.objects.count()
        stock_before_p = StockEntry.objects.get(product=prod_p, container=self.store_provider).qty_primary
        cash_before_p_usd = self.cash_provider.balance_usd
        cash_before_p_syp = self.cash_provider.balance_syp

        pret = BillingSV.create_return(
            actor=self.user,
            provider_id=self.provider.id,
            status="unpaid",
            paid_amount=Decimal("0"),
            items=[
                {
                    "product_id": prod_p.id,
                    "unit_index": 1,
                    "qty_raw": "2",
                    "cost": "15",
                    "currency": "USD",
                }
            ],
            container=self.store_provider,
            source_bill_serial=None,
            money_container_id=None,
            currency_code="USD",
            valuation_mode="HISTORICAL",
        )

        stock_after_p = StockEntry.objects.get(product=prod_p, container=self.store_provider).qty_primary
        cash_after_p_usd = MoneyContainer.objects.get(pk=self.cash_provider.id).balance_usd
        cash_after_p_syp = MoneyContainer.objects.get(pk=self.cash_provider.id).balance_syp
        debt_p = CreditorDebt.objects.filter(
            source_app="billing",
            source_model="ProviderReturn",
            currency_code="USD",
        ).filter(
            Q(source_id=str(pret.id))
            | Q(legacy_source_id=str(pret.id))
            | Q(source_id=f"{pret.id}:USD")
            | Q(legacy_source_id=f"{pret.id}:USD")
        ).get()
        audit_after_p = AuditLog.objects.count()

        self.assertEqual(stock_before_p, Decimal("10.000"))
        self.assertEqual(stock_after_p, Decimal("8.000"))
        self.assertEqual(cash_before_p_usd, cash_after_p_usd)
        self.assertEqual(cash_before_p_syp, cash_after_p_syp)
        self.assertEqual(debt_p.remaining, Decimal("30.000"))
        self.assertTrue(pret.status in {ProviderReturn.Status.UNPAID, ProviderReturn.Status.PAID, ProviderReturn.Status.PARTIAL})
        self.assertEqual(audit_after_p - audit_before_p, 1)

        # ----- Sales return (debt-only) -----
        bill = self._new_sale_bill()
        self._record_sale(prod_s, self.store_sales, bill)
        DebtSV.create_debtor_entry(
            provider=None,
            total=Decimal("100"),
            paid_amount=Decimal("0"),
            source_app="pos",
            source_model="SalesBill",
            source_id=str(bill.id),
            currency_code="USD",
            party_type=PartyType.CUSTOMER,
            party_name=bill.customer_name,
            customer_id=bill.customer_id,
        )

        audit_before_s = AuditLog.objects.count()
        stock_before_s = StockEntry.objects.get(product=prod_s, container=self.store_sales).qty_primary
        cash_before_s_usd = self.cash_sales.balance_usd
        cash_before_s_syp = self.cash_sales.balance_syp
        debt_before_s = DebtorDebt.objects.filter(
            source_app="pos",
            source_model="SalesBill",
            currency_code="USD",
        ).filter(
            Q(source_id=str(bill.id))
            | Q(legacy_source_id=str(bill.id))
            | Q(source_id=f"{bill.id}:USD")
            | Q(legacy_source_id=f"{bill.id}:USD")
        ).get()

        ret = ReturnSV.create_sales_return_draft(
            actor=self.user,
            sale_bill_id=bill.id,
            stock_container_id=self.store_sales.id,
            items=[{"sale_row_id": bill.rows.first().id, "qty": Decimal("2"), "reason": ""}],
        )
        ReturnSV.post_sales_return(
            actor=self.user,
            return_id=ret.id,
            settle_mode="cash",
            money_container_id=self.cash_sales.id,
        )
        ret.refresh_from_db()

        stock_after_s = StockEntry.objects.get(product=prod_s, container=self.store_sales).qty_primary
        cash_after_s_usd = MoneyContainer.objects.get(pk=self.cash_sales.id).balance_usd
        cash_after_s_syp = MoneyContainer.objects.get(pk=self.cash_sales.id).balance_syp
        debt_after_s = DebtorDebt.objects.filter(
            source_app="pos",
            source_model="SalesBill",
            currency_code="USD",
        ).filter(
            Q(source_id=str(bill.id))
            | Q(legacy_source_id=str(bill.id))
            | Q(source_id=f"{bill.id}:USD")
            | Q(legacy_source_id=f"{bill.id}:USD")
        ).get()
        audit_after_s = AuditLog.objects.count()

        self.assertEqual(stock_before_s, Decimal("4.000"))
        self.assertEqual(stock_after_s, Decimal("6.000"))
        self.assertEqual(cash_before_s_usd, cash_after_s_usd)
        self.assertEqual(cash_before_s_syp, cash_after_s_syp)
        self.assertEqual(debt_before_s.remaining, Decimal("100.000"))
        self.assertEqual(debt_after_s.remaining, Decimal("70.000"))
        self.assertEqual(ret.status, ret.Status.POSTED)
        self.assertEqual(audit_after_s - audit_before_s, 2)

        self.assertEqual(abs(stock_after_p - stock_before_p), abs(stock_after_s - stock_before_s))
        self.assertEqual(abs(cash_after_p_usd - cash_before_p_usd), abs(cash_after_s_usd - cash_before_s_usd))
        self.assertEqual(abs(debt_p.remaining - Decimal("0.000")), abs(debt_after_s.remaining - debt_before_s.remaining))

    def test_cash_only_return_symmetry(self):
        prod_p = self._create_product("Provider Cash Item")
        prod_s = self._create_product("Sales Cash Item")

        self._seed_stock(prod_p, self.store_provider)
        self._seed_stock(prod_s, self.store_sales)

        # ----- Provider return (paid) -----
        audit_before_p = AuditLog.objects.count()
        stock_before_p = StockEntry.objects.get(product=prod_p, container=self.store_provider).qty_primary
        cash_before_p_usd = self.cash_provider.balance_usd
        cash_before_p_syp = self.cash_provider.balance_syp

        pret = BillingSV.create_return(
            actor=self.user,
            provider_id=self.provider.id,
            status="paid",
            paid_amount=Decimal("30"),
            items=[
                {
                    "product_id": prod_p.id,
                    "unit_index": 1,
                    "qty_raw": "2",
                    "cost": "15",
                    "currency": "USD",
                }
            ],
            container=self.store_provider,
            source_bill_serial=None,
            money_container_id=self.cash_provider.id,
            currency_code="USD",
            valuation_mode="HISTORICAL",
        )

        stock_after_p = StockEntry.objects.get(product=prod_p, container=self.store_provider).qty_primary
        cash_after_p_usd = MoneyContainer.objects.get(pk=self.cash_provider.id).balance_usd
        cash_after_p_syp = MoneyContainer.objects.get(pk=self.cash_provider.id).balance_syp
        debt_p = CreditorDebt.objects.filter(
            source_app="billing",
            source_model="ProviderReturn",
            currency_code="USD",
        ).filter(
            Q(source_id=str(pret.id))
            | Q(legacy_source_id=str(pret.id))
            | Q(source_id=f"{pret.id}:USD")
            | Q(legacy_source_id=f"{pret.id}:USD")
        ).get()
        audit_after_p = AuditLog.objects.count()

        self.assertEqual(stock_before_p, Decimal("10.000"))
        self.assertEqual(stock_after_p, Decimal("8.000"))
        self.assertEqual(cash_after_p_usd, cash_before_p_usd + Decimal("30"))
        self.assertEqual(cash_before_p_syp, cash_after_p_syp)
        self.assertEqual(debt_p.remaining, Decimal("0.000"))
        self.assertTrue(pret.status in {ProviderReturn.Status.UNPAID, ProviderReturn.Status.PAID, ProviderReturn.Status.PARTIAL})
        self.assertEqual(audit_after_p - audit_before_p, 1)

        # ----- Sales return (cash-only) -----
        bill = self._new_sale_bill()
        self._record_sale(prod_s, self.store_sales, bill)

        audit_before_s = AuditLog.objects.count()
        stock_before_s = StockEntry.objects.get(product=prod_s, container=self.store_sales).qty_primary
        cash_before_s_usd = self.cash_sales.balance_usd
        cash_before_s_syp = self.cash_sales.balance_syp

        ret = ReturnSV.create_sales_return_draft(
            actor=self.user,
            sale_bill_id=bill.id,
            stock_container_id=self.store_sales.id,
            items=[{"sale_row_id": bill.rows.first().id, "qty": Decimal("2"), "reason": ""}],
        )
        ReturnSV.post_sales_return(
            actor=self.user,
            return_id=ret.id,
            settle_mode="cash",
            money_container_id=self.cash_sales.id,
        )
        ret.refresh_from_db()

        stock_after_s = StockEntry.objects.get(product=prod_s, container=self.store_sales).qty_primary
        cash_after_s_usd = MoneyContainer.objects.get(pk=self.cash_sales.id).balance_usd
        cash_after_s_syp = MoneyContainer.objects.get(pk=self.cash_sales.id).balance_syp
        audit_after_s = AuditLog.objects.count()

        self.assertEqual(stock_before_s, Decimal("4.000"))
        self.assertEqual(stock_after_s, Decimal("6.000"))
        self.assertEqual(cash_after_s_usd, cash_before_s_usd - Decimal("30"))
        self.assertEqual(cash_before_s_syp, cash_after_s_syp)
        self.assertEqual(ret.status, ret.Status.POSTED)
        self.assertEqual(audit_after_s - audit_before_s, 2)

        self.assertEqual(abs(stock_after_p - stock_before_p), abs(stock_after_s - stock_before_s))
        self.assertEqual(abs(cash_after_p_usd - cash_before_p_usd), abs(cash_after_s_usd - cash_before_s_usd))

    def test_mixed_debt_cash_return_symmetry(self):
        prod_p = self._create_product("Provider Mixed Item")
        prod_s = self._create_product("Sales Mixed Item")

        self._seed_stock(prod_p, self.store_provider)
        self._seed_stock(prod_s, self.store_sales)

        # ----- Provider return (partial) -----
        audit_before_p = AuditLog.objects.count()
        stock_before_p = StockEntry.objects.get(product=prod_p, container=self.store_provider).qty_primary
        cash_before_p_usd = self.cash_provider.balance_usd

        pret = BillingSV.create_return(
            actor=self.user,
            provider_id=self.provider.id,
            status="partial",
            paid_amount=Decimal("20"),
            items=[
                {
                    "product_id": prod_p.id,
                    "unit_index": 1,
                    "qty_raw": "2",
                    "cost": "15",
                    "currency": "USD",
                }
            ],
            container=self.store_provider,
            source_bill_serial=None,
            money_container_id=self.cash_provider.id,
            currency_code="USD",
            valuation_mode="HISTORICAL",
        )

        stock_after_p = StockEntry.objects.get(product=prod_p, container=self.store_provider).qty_primary
        cash_after_p_usd = MoneyContainer.objects.get(pk=self.cash_provider.id).balance_usd
        debt_p = CreditorDebt.objects.filter(
            source_app="billing",
            source_model="ProviderReturn",
            currency_code="USD",
        ).filter(
            Q(source_id=str(pret.id))
            | Q(legacy_source_id=str(pret.id))
            | Q(source_id=f"{pret.id}:USD")
            | Q(legacy_source_id=f"{pret.id}:USD")
        ).get()
        audit_after_p = AuditLog.objects.count()

        self.assertEqual(stock_before_p, Decimal("10.000"))
        self.assertEqual(stock_after_p, Decimal("8.000"))
        self.assertEqual(cash_after_p_usd, cash_before_p_usd + Decimal("20"))
        self.assertEqual(debt_p.remaining, Decimal("10.000"))
        self.assertTrue(pret.status in {ProviderReturn.Status.UNPAID, ProviderReturn.Status.PAID, ProviderReturn.Status.PARTIAL})
        self.assertEqual(audit_after_p - audit_before_p, 1)

        # ----- Sales return (mixed) -----
        bill = self._new_sale_bill()
        self._record_sale(prod_s, self.store_sales, bill)
        DebtSV.create_debtor_entry(
            provider=None,
            total=Decimal("100"),
            paid_amount=Decimal("90"),
            source_app="pos",
            source_model="SalesBill",
            source_id=str(bill.id),
            currency_code="USD",
            party_type=PartyType.CUSTOMER,
            party_name=bill.customer_name,
            customer_id=bill.customer_id,
        )

        audit_before_s = AuditLog.objects.count()
        stock_before_s = StockEntry.objects.get(product=prod_s, container=self.store_sales).qty_primary
        cash_before_s_usd = self.cash_sales.balance_usd
        debt_before_s = DebtorDebt.objects.filter(
            source_app="pos",
            source_model="SalesBill",
            currency_code="USD",
        ).filter(
            Q(source_id=str(bill.id))
            | Q(legacy_source_id=str(bill.id))
            | Q(source_id=f"{bill.id}:USD")
            | Q(legacy_source_id=f"{bill.id}:USD")
        ).get()

        ret = ReturnSV.create_sales_return_draft(
            actor=self.user,
            sale_bill_id=bill.id,
            stock_container_id=self.store_sales.id,
            items=[{"sale_row_id": bill.rows.first().id, "qty": Decimal("2"), "reason": ""}],
        )
        ReturnSV.post_sales_return(
            actor=self.user,
            return_id=ret.id,
            settle_mode="cash",
            money_container_id=self.cash_sales.id,
        )
        ret.refresh_from_db()

        stock_after_s = StockEntry.objects.get(product=prod_s, container=self.store_sales).qty_primary
        cash_after_s_usd = MoneyContainer.objects.get(pk=self.cash_sales.id).balance_usd
        debt_after_s = DebtorDebt.objects.filter(
            source_app="pos",
            source_model="SalesBill",
            currency_code="USD",
        ).filter(
            Q(source_id=str(bill.id))
            | Q(legacy_source_id=str(bill.id))
            | Q(source_id=f"{bill.id}:USD")
            | Q(legacy_source_id=f"{bill.id}:USD")
        ).get()
        audit_after_s = AuditLog.objects.count()

        self.assertEqual(stock_before_s, Decimal("4.000"))
        self.assertEqual(stock_after_s, Decimal("6.000"))
        self.assertEqual(cash_after_s_usd, cash_before_s_usd - Decimal("20"))
        self.assertEqual(debt_before_s.remaining, Decimal("10.000"))
        self.assertEqual(debt_after_s.remaining, Decimal("0.000"))
        self.assertEqual(ret.status, ret.Status.POSTED)
        self.assertEqual(audit_after_s - audit_before_s, 2)

        self.assertEqual(abs(stock_after_p - stock_before_p), abs(stock_after_s - stock_before_s))
        self.assertEqual(abs(cash_after_p_usd - cash_before_p_usd), abs(cash_after_s_usd - cash_before_s_usd))
        self.assertEqual(abs(debt_p.remaining - Decimal("0.000")), abs(debt_after_s.remaining - debt_before_s.remaining))

    def test_over_return_rejection_symmetry(self):
        prod_p = self._create_product("Provider Over Item")
        prod_s = self._create_product("Sales Over Item")

        self._seed_stock(prod_p, self.store_provider)
        self._seed_stock(prod_s, self.store_sales)

        stock_before_p = StockEntry.objects.get(product=prod_p, container=self.store_provider).qty_primary
        cash_before_p_usd = self.cash_provider.balance_usd
        debt_before_p = CreditorDebt.objects.filter(source_model="ProviderReturn", currency_code="USD").count()

        bill = BillingSV.create_bill(
            actor=self.user,
            provider_id=self.provider.id,
            status="unpaid",
            paid_amount=Decimal("0"),
            items=[],
            update_product_defaults=False,
            container=self.store_provider,
            money_container_id=None,
            settlement_currency="USD",
            fx_usd_syp=Decimal("10000"),
        )
        bill_item = bill.items.create(
            product=prod_p,
            unit_index=1,
            qty_primary=Decimal("10"),
            cost=Decimal("10"),
            price=Decimal("15"),
            line_total=Decimal("100"),
            currency="USD",
        )

        with transaction.atomic():
            with self.assertRaises(ValueError):
                BillingSV.create_return(
                    actor=self.user,
                    provider_id=self.provider.id,
                    status="unpaid",
                    paid_amount=Decimal("0"),
                    items=[
                        {
                            "bill_item_id": bill_item.id,
                            "product_id": prod_p.id,
                            "unit_index": 1,
                            "qty_primary": "20",
                            "cost": "10",
                            "currency": "USD",
                            "container_splits": [
                                {"code": "prov_store", "qty_primary": "20"},
                            ],
                        }
                    ],
                    container=None,
                    source_bill_serial=bill.serial,
                    money_container_id=None,
                    currency_code="USD",
                    valuation_mode="HISTORICAL",
                )

        stock_after_p = StockEntry.objects.get(product=prod_p, container=self.store_provider).qty_primary
        cash_after_p_usd = MoneyContainer.objects.get(pk=self.cash_provider.id).balance_usd
        debt_after_p = CreditorDebt.objects.filter(source_model="ProviderReturn", currency_code="USD").count()

        self.assertEqual(stock_before_p, stock_after_p)
        self.assertEqual(cash_before_p_usd, cash_after_p_usd)
        self.assertEqual(debt_before_p, debt_after_p)

        bill = self._new_sale_bill()
        self._record_sale(prod_s, self.store_sales, bill)

        stock_before_s = StockEntry.objects.get(product=prod_s, container=self.store_sales).qty_primary
        cash_before_s_usd = self.cash_sales.balance_usd
        debt_before_s = DebtorDebt.objects.filter(
            source_model="SalesBill",
            currency_code="USD",
        ).filter(
            Q(source_id=str(bill.id))
            | Q(legacy_source_id=str(bill.id))
            | Q(source_id=f"{bill.id}:USD")
            | Q(legacy_source_id=f"{bill.id}:USD")
        ).count()

        audit_before_s = AuditLog.objects.count()

        with self.assertRaises(ValueError):
            ReturnSV.create_sales_return_draft(
                actor=self.user,
                sale_bill_id=bill.id,
                stock_container_id=self.store_sales.id,
                items=[{"sale_row_id": bill.rows.first().id, "qty": Decimal("20"), "reason": ""}],
            )

        stock_after_s = StockEntry.objects.get(product=prod_s, container=self.store_sales).qty_primary
        cash_after_s_usd = MoneyContainer.objects.get(pk=self.cash_sales.id).balance_usd
        debt_after_s = DebtorDebt.objects.filter(
            source_model="SalesBill",
            currency_code="USD",
        ).filter(
            Q(source_id=str(bill.id))
            | Q(legacy_source_id=str(bill.id))
            | Q(source_id=f"{bill.id}:USD")
            | Q(legacy_source_id=f"{bill.id}:USD")
        ).count()
        audit_after_s = AuditLog.objects.count()

        self.assertEqual(stock_after_s, stock_before_s)
        self.assertEqual(cash_after_s_usd, cash_before_s_usd)
        self.assertEqual(debt_after_s, debt_before_s)
        self.assertEqual(audit_after_s - audit_before_s, 0)
