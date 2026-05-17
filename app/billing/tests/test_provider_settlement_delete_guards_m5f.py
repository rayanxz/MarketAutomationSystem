from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from accounts.models import AccountProfile
from billing import services as BillingSV
from billing.models import Bill, Provider, ProviderReturn
from catalog.models import Product, ProductCollection, ProductSet, UnitType
from debts.models import ProviderSettlementAllocation
from debts.provider_account_settlement_execution import execute_provider_account_settlement
from financials import services as FinSV
from financials.models import ContainerFeature, Currency, MoneyContainer, MoneyContainerCurrency
from stock.models import ProductContainer


CONFLICT_MSG = (
    "Cannot delete this document because it is linked to provider account settlement actions. "
    "Reverse those actions first."
)


def _ensure_currency(code: str) -> Currency:
    cur, _ = Currency.objects.get_or_create(
        code=code,
        defaults={"name": code, "decimals": 2, "is_active": True},
    )
    return cur


def _ensure_container_currency(container: MoneyContainer, code: str) -> None:
    cur = _ensure_currency(code)
    MoneyContainerCurrency.objects.get_or_create(
        container=container,
        currency=cur,
        defaults={"is_enabled": True},
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
        allow_syp_purchasing=True,
        allow_usd_purchasing=True,
        allow_syp_sales=True,
        allow_usd_sales=True,
    )


class ProviderSettlementDeleteGuardsM5FTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.actor = User.objects.create_user(username="m5f_delete_guard_mgr", password="123456")
        AccountProfile.objects.create(user=cls.actor, role=AccountProfile.Role.MANAGER)

        cls.store = _ensure_store_container()
        cls.provider = Provider.objects.create(name="M5F Provider")
        cls.product = _create_min_product("M5F Product")

    def setUp(self):
        self.cash = MoneyContainer.objects.create(
            name=f"M5F Cash {self._testMethodName}",
            container_type=MoneyContainer.ContainerType.DRAWER,
            is_active=True,
            created_by=self.actor,
        )
        self.cash.allowed_users.add(self.actor)
        for feature_code, feature_name in (
            ("purchase_bills", "Purchase Bills"),
            ("provider_returns", "Provider Returns"),
            ("provider_account_settlement", "Provider Account Settlement"),
        ):
            feature, _ = ContainerFeature.objects.get_or_create(
                code=feature_code,
                defaults={"name": feature_name, "is_active": True},
            )
            if not feature.is_active:
                feature.is_active = True
                feature.save(update_fields=["is_active"])
            self.cash.features.add(feature)
        _ensure_container_currency(self.cash, "SYP")
        _ensure_container_currency(self.cash, "USD")
        FinSV.set_current_fx(actor=self.actor, rate_syp_per_usd=Decimal("20000"))

    def _create_bill(self, *, qty: str = "1", cost: str = "1000", currency: str = "SYP") -> Bill:
        return BillingSV.create_bill(
            actor=self.actor,
            provider_id=self.provider.id,
            status="unpaid",
            paid_amount=Decimal("0"),
            items=[
                {
                    "product_id": self.product.id,
                    "unit_index": 1,
                    "qty_raw": qty,
                    "cost": cost,
                    "currency": currency,
                }
            ],
            container=self.store,
            money_container_id=self.cash.id,
            settlement_currency=currency,
        )

    def _create_return(self, *, bill: Bill, qty_primary: str = "1", currency_code: str = "SYP") -> ProviderReturn:
        bill_item = bill.items.first()
        self.assertIsNotNone(bill_item)
        return BillingSV.create_return(
            actor=self.actor,
            provider_id=self.provider.id,
            status="unpaid",
            paid_amount=Decimal("0"),
            items=[
                {
                    "bill_item_id": bill_item.id,
                    "product_id": self.product.id,
                    "unit_index": 1,
                    "qty_primary": qty_primary,
                    "container_splits": [{"code": "store", "qty_primary": qty_primary}],
                }
            ],
            container=None,
            source_bill_serial=bill.serial,
            money_container_id=self.cash.id,
            currency_code=currency_code,
            valuation_mode="HISTORICAL",
        )

    def test_delete_bill_linked_to_provider_settlement_allocation_fails_cleanly(self):
        bill = self._create_bill(qty="2", cost="1000", currency="SYP")
        execute_provider_account_settlement(
            provider_id=self.provider.id,
            action="pay_provider",
            currency="SYP",
            amount=Decimal("500.00"),
            money_container_id=self.cash.id,
            idempotency_key=f"{self._testMethodName}:exec",
            user=self.actor,
        )

        self.assertTrue(ProviderSettlementAllocation.objects.exists())
        with self.assertRaisesMessage(ValidationError, CONFLICT_MSG):
            BillingSV.delete_bill(actor=self.actor, bill_id=bill.id)
        self.assertTrue(Bill.objects.filter(id=bill.id).exists())

    def test_delete_return_linked_to_provider_settlement_allocation_fails_cleanly(self):
        bill = self._create_bill(qty="2", cost="1000", currency="SYP")
        pret = self._create_return(bill=bill, qty_primary="1", currency_code="SYP")
        execute_provider_account_settlement(
            provider_id=self.provider.id,
            action="receive_from_provider",
            currency="SYP",
            amount=Decimal("100.00"),
            money_container_id=self.cash.id,
            idempotency_key=f"{self._testMethodName}:exec",
            user=self.actor,
        )

        self.assertTrue(
            ProviderSettlementAllocation.objects.filter(legacy_creditor_debt__isnull=False).exists()
        )
        with self.assertRaisesMessage(ValidationError, CONFLICT_MSG):
            BillingSV.delete_return(actor=self.actor, return_id=pret.id)
        self.assertTrue(ProviderReturn.objects.filter(id=pret.id).exists())

    def test_unlinked_documents_still_delete_normally(self):
        bill1 = self._create_bill(qty="1", cost="400", currency="SYP")
        BillingSV.delete_bill(actor=self.actor, bill_id=bill1.id)
        self.assertFalse(Bill.objects.filter(id=bill1.id).exists())

        bill2 = self._create_bill(qty="1", cost="500", currency="SYP")
        pret = self._create_return(bill=bill2, qty_primary="1", currency_code="SYP")
        BillingSV.delete_return(actor=self.actor, return_id=pret.id)
        self.assertFalse(ProviderReturn.objects.filter(id=pret.id).exists())
