# billing/tests/test_bill_delete_financials_multicurrency.py
from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from accounts.models import AccountProfile
from billing import services as BillingSV
from billing.models import Bill, BillItem, Provider
from catalog.models import Product, ProductCollection, ProductSet, UnitType
from debts.models import DebtorDebt
from financials import services as FinSV
from financials.models import Currency, MoneyContainer, MoneyContainerCurrency, Receipt, ReceiptStatus
from inventory.models import ProductMovement, DEC0, q3
from stock.models import ProductContainer, StockFifoLayer
from pos.models import SalesBill, SalesBillRow
from pos import services as PosSV


def _ensure_currency(code: str) -> Currency:
    cur, _ = Currency.objects.get_or_create(code=code, defaults={"name": code, "decimals": 2, "is_active": True})
    return cur


def _ensure_container_currency(container: MoneyContainer, code: str) -> None:
    cur = _ensure_currency(code)
    MoneyContainerCurrency.objects.get_or_create(container=container, currency=cur, defaults={"is_enabled": True})


def _ensure_money_container(actor) -> MoneyContainer:
    mc = MoneyContainer.objects.filter(name="Delete Bill Cash").first()
    if mc:
        return mc
    return MoneyContainer.objects.create(
        name="Delete Bill Cash",
        container_type=MoneyContainer.ContainerType.DRAWER,
        is_active=True,
        created_by=actor,
        note="auto-created by test",
    )


def _ensure_store_container() -> ProductContainer:
    cont = ProductContainer.objects.filter(code="store").first()
    if cont:
        return cont
    return ProductContainer.objects.create(name="Store", code="store", is_store=True, is_active=True)


def _create_min_product(name: str) -> Product:
    col = ProductCollection.objects.create(name=f"{name} Collection")
    st = ProductSet.objects.create(collection=col, name=f"{name} Set")
    return Product.objects.create(
        name=name,
        set=st,
        unit_primary=UnitType.PIECE,
        stock_qty=Decimal("0"),
        cost=Decimal("1"),
        price=Decimal("1"),
        allow_syp_purchasing=True,
        allow_usd_purchasing=True,
        allow_syp_sales=True,
        allow_usd_sales=True,
        default_cost_syp=Decimal("10"),
        default_cost_usd=Decimal("1"),
        default_price_syp=Decimal("20"),
        default_price_usd=Decimal("2"),
    )


def _create_provider() -> Provider:
    return Provider.objects.create(name="Test Provider")


class PurchaseBillDeleteFinancialsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.actor = User.objects.create_user(username="mgr_del", password="123")

        prof, _ = AccountProfile.objects.get_or_create(user=cls.actor)
        prof.role = AccountProfile.Role.MANAGER
        prof.save(update_fields=["role"])

        _ensure_currency("SYP")
        _ensure_currency("USD")
        FinSV.set_current_fx(actor=cls.actor, rate_syp_per_usd=Decimal("15000"))

        cls.store = _ensure_store_container()
        cls.cash = _ensure_money_container(cls.actor)
        _ensure_container_currency(cls.cash, "SYP")
        _ensure_container_currency(cls.cash, "USD")

        cls.provider = _create_provider()
        cls.product = _create_min_product("DeleteBillProd")

    def _create_bill(self, *, status: str, paid_amount: Decimal, currency: str = "SYP", qty: str = "2", cost: str = "1000") -> Bill:
        items = [
            {
                "product_id": self.product.id,
                "unit_index": 1,
                "qty_raw": qty,
                "cost": cost,
                "currency": currency,
                "price_syp": "",
                "price_usd": "",
            }
        ]
        return BillingSV.create_bill(
            actor=self.actor,
            provider_id=self.provider.id,
            status=status,
            paid_amount=paid_amount,
            items=items,
            money_container_id=self.cash.id,
            settlement_currency=currency,
        )

    def test_delete_untouched_fully_paid_bill_reverses_all(self):
        self.cash.balance_syp = Decimal("50000")
        self.cash.balance_usd = Decimal("5")
        self.cash.save(update_fields=["balance_syp", "balance_usd"])

        bill = self._create_bill(status="paid", paid_amount=Decimal("2000"), currency="SYP", qty="2", cost="1000")

        self.cash.refresh_from_db()
        self.assertEqual(self.cash.balance_syp, Decimal("48000"))

        item_ids = list(BillItem.objects.filter(bill=bill).values_list("id", flat=True))
        self.assertGreater(len(item_ids), 0)

        BillingSV.delete_bill(actor=self.actor, bill_id=bill.id)

        self.cash.refresh_from_db()
        self.assertEqual(self.cash.balance_syp, Decimal("50000"))

        self.assertFalse(Bill.objects.filter(id=bill.id).exists())
        self.assertEqual(BillItem.objects.filter(id__in=item_ids).count(), 0)

        self.assertEqual(
            StockFifoLayer.objects.filter(
                source_app="billing",
                source_model="BillItem",
                source_id__in=[str(i) for i in item_ids],
            ).count(),
            0,
        )

        self.assertEqual(
            ProductMovement.objects.filter(
                source_app="billing",
                source_model="BillItem",
                source_id__in=[str(i) for i in item_ids],
            ).count(),
            0,
        )

        debts = DebtorDebt.objects.filter(source_app="billing", source_model="Bill", source_id=str(bill.id))
        self.assertEqual(debts.count(), 0)

        receipts = Receipt.objects.filter(
            source_app="billing",
            source_model="Bill",
            source_id=str(bill.id),
        )
        self.assertGreater(receipts.count(), 0)
        self.assertEqual(receipts.filter(status=ReceiptStatus.POSTED).count(), 0)
        self.assertGreater(receipts.filter(status=ReceiptStatus.REVERSED).count(), 0)

    def test_delete_untouched_partial_bill_refunds_paid_part(self):
        base_bal = FinSV.container_balance(container_id=self.cash.id)

        bill = self._create_bill(status="partial", paid_amount=Decimal("500"), currency="SYP", qty="2", cost="1000")
        self.assertTrue(Bill.objects.filter(id=bill.id).exists())

        mid_bal = FinSV.container_balance(container_id=self.cash.id)
        self.assertNotEqual(q3(mid_bal.get("SYP", DEC0)), q3(base_bal.get("SYP", DEC0)))

        BillingSV.delete_bill(actor=self.actor, bill_id=bill.id)

        after_bal = FinSV.container_balance(container_id=self.cash.id)
        self.assertEqual(q3(after_bal.get("SYP", DEC0)), q3(base_bal.get("SYP", DEC0)))

        self.assertFalse(Bill.objects.filter(id=bill.id).exists())
        self.assertEqual(
            DebtorDebt.objects.filter(source_app="billing", source_model="Bill", source_id=str(bill.id)).count(),
            0,
        )

    def test_delete_untouched_unpaid_bill_removes_debt_only(self):
        base_bal = FinSV.container_balance(container_id=self.cash.id)

        bill = self._create_bill(status="unpaid", paid_amount=Decimal("0"), currency="SYP", qty="1", cost="500")
        BillingSV.delete_bill(actor=self.actor, bill_id=bill.id)

        after_bal = FinSV.container_balance(container_id=self.cash.id)
        self.assertEqual(q3(after_bal.get("SYP", DEC0)), q3(base_bal.get("SYP", DEC0)))

        self.assertFalse(Bill.objects.filter(id=bill.id).exists())
        self.assertEqual(
            DebtorDebt.objects.filter(source_app="billing", source_model="Bill", source_id=str(bill.id)).count(),
            0,
        )

    def test_delete_touched_bill_is_blocked(self):
        bill = self._create_bill(status="unpaid", paid_amount=Decimal("0"), currency="SYP", qty="3", cost="1000")

        pos_bill = SalesBill.objects.create(
            cashier=self.actor,
            pay_status=SalesBill.PAY_NONE,
            parked=False,
            finalized=True,
        )
        SalesBillRow.objects.create(
            bill=pos_bill,
            product_id=self.product.id,
            product_name=self.product.name,
            product_number="SKU-1",
            qty=Decimal("1"),
            uom_index=1,
            unit_price=Decimal("100"),
            sale_currency="SYP",
        )
        PosSV.finalize_pos_bill(bill=pos_bill, actor=self.actor)

        with self.assertRaises(ValidationError):
            BillingSV.delete_bill(actor=self.actor, bill_id=bill.id)

        self.assertTrue(Bill.objects.filter(id=bill.id).exists())
        self.assertGreater(ProductMovement.objects.count(), 0)
