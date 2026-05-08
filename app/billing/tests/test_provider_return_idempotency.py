from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import time
from unittest import skipIf

from django.contrib.auth import get_user_model
from django.db import OperationalError, close_old_connections, connections, connection
from django.test import TestCase, TransactionTestCase

from billing import services as BillingSV
from billing.models import Bill, Provider, ProviderReturn
from catalog.models import Product, ProductCollection, ProductSet, UnitType
from debts.models import CreditorDebt, DebtCauseType, DebtDirection, DebtRecord, DebtSettlement
from financials import services as FinSV
from financials.models import ContainerFeature, Currency, MoneyContainer, MoneyContainerCurrency, Receipt, ReceiptStatus
from inventory.models import ProductMovement
from stock.models import ProductContainer


def _ensure_currency(code: str) -> Currency:
    cur, _ = Currency.objects.get_or_create(code=code, defaults={"name": code, "decimals": 2, "is_active": True})
    cur.is_active = True
    if cur.decimals != 2:
        cur.decimals = 2
    cur.save(update_fields=["is_active", "decimals"])
    return cur


def _ensure_container_currency(container: MoneyContainer, code: str) -> None:
    cur = _ensure_currency(code)
    MoneyContainerCurrency.objects.update_or_create(
        container=container,
        currency=cur,
        defaults={"is_enabled": True},
    )


def _ensure_feature(code: str) -> ContainerFeature:
    feat, _ = ContainerFeature.objects.get_or_create(
        code=code,
        defaults={"name": code.replace("_", " ").title(), "is_active": True},
    )
    if not feat.is_active:
        feat.is_active = True
        feat.save(update_fields=["is_active"])
    return feat


def _create_product(name: str) -> Product:
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


class _ProviderReturnIdempotencyFixtureMixin:
    actor = None
    provider = None
    store = None
    cash = None
    product = None

    def _setup_fixture(self):
        User = get_user_model()
        self.actor = User.objects.create_user(username=f"ret_idempo_mgr_{time.time_ns()}", password="pw12345")
        self.provider = Provider.objects.create(name=f"Return Idempotency Provider {time.time_ns()}")
        self.store, _ = ProductContainer.objects.get_or_create(
            code="store",
            defaults={"name": "Store", "is_store": True, "is_active": True},
        )
        self.cash = MoneyContainer.objects.create(
            name=f"Idempo Cash {time.time_ns()}",
            container_type=MoneyContainer.ContainerType.DRAWER,
            is_active=True,
            created_by=self.actor,
        )
        self.cash.allowed_users.add(self.actor)
        self.cash.features.add(_ensure_feature("provider_returns"), _ensure_feature("purchase_bills"))
        _ensure_container_currency(self.cash, "SYP")
        _ensure_container_currency(self.cash, "USD")
        FinSV.set_current_fx(actor=self.actor, rate_syp_per_usd=Decimal("20000"))
        self.product = _create_product(f"Idempo Product {time.time_ns()}")

    def _create_source_bill(self, *, qty: str = "5", cost: str = "100", currency: str = "SYP") -> Bill:
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

    def _create_return(
        self,
        *,
        bill: Bill,
        qty_raw: str,
        cost: str,
        status: str,
        paid_amount: str,
        payment_method: str | None = None,
        paid_syp: str | None = None,
        paid_usd: str | None = None,
        settle_purchase_debt: bool = False,
        debt_settlement_amount: str | None = None,
    ) -> ProviderReturn:
        return BillingSV.create_return(
            actor=self.actor,
            provider_id=self.provider.id,
            status=status,
            paid_amount=Decimal(paid_amount),
            items=[
                {
                    "product_id": self.product.id,
                    "unit_index": 1,
                    "qty_raw": qty_raw,
                    "cost": cost,
                    "currency": "SYP",
                }
            ],
            container=self.store,
            source_bill_serial=bill.serial,
            source_bill_public_id=bill.public_id,
            money_container_id=self.cash.id,
            currency_code="SYP",
            valuation_mode="CURRENT_FX",
            payment_method=payment_method,
            paid_syp=Decimal(paid_syp) if paid_syp is not None else None,
            paid_usd=Decimal(paid_usd) if paid_usd is not None else None,
            settle_purchase_debt=settle_purchase_debt,
            debt_settlement_amount=(Decimal(debt_settlement_amount) if debt_settlement_amount is not None else None),
        )


class ProviderReturnIdempotencyTests(_ProviderReturnIdempotencyFixtureMixin, TestCase):
    def setUp(self):
        self._setup_fixture()

    def test_duplicate_service_request_returns_same_return_and_does_not_duplicate_side_effects(self):
        bill = self._create_source_bill(qty="5", cost="100", currency="SYP")

        first = self._create_return(
            bill=bill,
            qty_raw="1",
            cost="100",
            status="paid",
            paid_amount="100",
            payment_method="syp_only",
            paid_syp="100",
            paid_usd="0",
            settle_purchase_debt=True,
            debt_settlement_amount="100",
        )
        second = self._create_return(
            bill=bill,
            qty_raw="1",
            cost="100",
            status="paid",
            paid_amount="100",
            payment_method="syp_only",
            paid_syp="100",
            paid_usd="0",
            settle_purchase_debt=True,
            debt_settlement_amount="100",
        )

        self.assertEqual(first.id, second.id)
        self.assertEqual(ProviderReturn.objects.count(), 1)
        self.assertEqual(
            ProductMovement.objects.filter(
                source_app="billing",
                source_model="ProviderReturn",
            ).values("source_id").distinct().count(),
            1,
        )
        self.assertEqual(
            CreditorDebt.objects.filter(
                source_app="billing",
                source_model="ProviderReturn",
            ).values("source_id").distinct().count(),
            1,
        )
        self.assertEqual(
            Receipt.objects.filter(
                source_app="billing",
                source_model="ProviderReturn",
                source_id=str(first.id),
                status=ReceiptStatus.POSTED,
            ).count(),
            1,
        )
        self.assertEqual(DebtSettlement.objects.count(), 1)

        source_debt = (
            DebtRecord.objects
            .filter(
                direction=DebtDirection.PAYABLE,
                cause_type=DebtCauseType.PURCHASE_BILL,
                cause_id__in=[bill.public_id, str(bill.id)],
            )
            .order_by("id")
            .first()
        )
        self.assertIsNotNone(source_debt)
        self.assertEqual(Decimal(str(source_debt.remaining_syp or "0")), Decimal("400.00"))

    def test_different_payload_creates_new_return(self):
        bill = self._create_source_bill(qty="5", cost="100", currency="SYP")

        first = self._create_return(
            bill=bill,
            qty_raw="1",
            cost="100",
            status="unpaid",
            paid_amount="0",
        )
        second = self._create_return(
            bill=bill,
            qty_raw="2",
            cost="100",
            status="unpaid",
            paid_amount="0",
        )

        self.assertNotEqual(first.id, second.id)
        self.assertEqual(ProviderReturn.objects.count(), 2)


class ProviderReturnIdempotencyConcurrencyTests(_ProviderReturnIdempotencyFixtureMixin, TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self._setup_fixture()

    def tearDown(self):
        close_old_connections()
        connections.close_all()
        super().tearDown()

    def _with_retry(self, fn):
        for attempt in range(20):
            try:
                close_old_connections()
                return fn()
            except OperationalError as exc:
                if "locked" not in str(exc).lower():
                    raise
                time.sleep(0.03 * (attempt + 1))
            finally:
                close_old_connections()
                connections.close_all()
        raise AssertionError("db remained locked during idempotency concurrency test")

    @skipIf(connection.vendor == "sqlite", "SQLite shared test DB is not stable for threaded flush teardown")
    def test_concurrent_identical_service_requests_are_idempotent(self):
        bill = self._create_source_bill(qty="5", cost="100", currency="SYP")

        def _worker(_: int) -> int:
            try:
                ret = self._with_retry(
                    lambda: self._create_return(
                        bill=bill,
                        qty_raw="1",
                        cost="100",
                        status="partial",
                        paid_amount="50",
                        payment_method="syp_only",
                        paid_syp="50",
                        paid_usd="0",
                    )
                )
                return ret.id
            finally:
                close_old_connections()
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            ret_ids = list(pool.map(_worker, [1, 2]))

        self.assertEqual(len(ret_ids), 2)
        self.assertEqual(len(set(ret_ids)), 1)
        ret_id = ret_ids[0]

        self.assertEqual(ProviderReturn.objects.count(), 1)
        self.assertEqual(
            ProductMovement.objects.filter(
                source_app="billing",
                source_model="ProviderReturn",
            ).values("source_id").distinct().count(),
            1,
        )
        self.assertEqual(
            CreditorDebt.objects.filter(
                source_app="billing",
                source_model="ProviderReturn",
            ).values("source_id").distinct().count(),
            1,
        )
        self.assertEqual(
            Receipt.objects.filter(
                source_app="billing",
                source_model="ProviderReturn",
                source_id=str(ret_id),
                status=ReceiptStatus.POSTED,
            ).count(),
            1,
        )
        self.assertEqual(DebtSettlement.objects.count(), 0)
