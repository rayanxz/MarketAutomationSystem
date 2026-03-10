# billing/tests/test_provider_returns_multicurrency.py
from __future__ import annotations

from decimal import Decimal
from django.test import TestCase
from django.contrib.auth import get_user_model

from billing import services as BillingSV
from billing.models import Provider, Bill, ProviderReturn
from debts.models import CreditorDebt, CreditorReceipt
from audit_log.models import AuditLog
from catalog.models import Product, ProductCollection, ProductSet, UnitType
from financials.models import MoneyContainer, Currency, MoneyContainerCurrency, Receipt, ReceiptStatus
from financials import services as FinSV
from stock.models import ProductContainer, StockFifoLayer
from inventory.models import ProductMovement, DEC0, q3


def _ensure_currency(code: str) -> Currency:
    cur, _ = Currency.objects.get_or_create(code=code, defaults={"name": code, "decimals": 2, "is_active": True})
    return cur


def _ensure_container_currency(container: MoneyContainer, code: str) -> None:
    cur = _ensure_currency(code)
    MoneyContainerCurrency.objects.get_or_create(container=container, currency=cur, defaults={"is_enabled": True})


def _ensure_money_container(actor) -> MoneyContainer:
    mc = MoneyContainer.objects.filter(name="Test Cash").first()
    if mc:
        return mc
    return MoneyContainer.objects.create(
        ref_code="CASH-RET-01",
        name="Test Cash",
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
        allow_syp_purchasing=True,
        allow_usd_purchasing=True,
        allow_syp_sales=True,
        allow_usd_sales=True,
    )


def _create_provider() -> Provider:
    return Provider.objects.create(name="Test Provider")


class ProviderReturnsMultiCurrencyTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.actor = User.objects.create_user(username="tester", password="pw123456")
        self.store = _ensure_store_container()

        self.cash = _ensure_money_container(self.actor)
        _ensure_container_currency(self.cash, "SYP")
        _ensure_container_currency(self.cash, "USD")

        FinSV.set_current_fx(actor=self.actor, rate_syp_per_usd=Decimal("20000"))

        self.provider = _create_provider()

    def _create_bill(self, *, product: Product, qty: Decimal, cost: Decimal, currency: str, fx: Decimal | None = None) -> Bill:
        return BillingSV.create_bill(
            actor=self.actor,
            provider_id=self.provider.id,
            status="unpaid",
            paid_amount=Decimal("0"),
            items=[
                {
                    "product_id": product.id,
                    "unit_index": 1,
                    "qty_raw": str(qty),
                    "cost": str(cost),
                    "currency": currency,
                }
            ],
            container=self.store,
            money_container_id=self.cash.id,
            settlement_currency=currency,
            fx_usd_syp=fx,
        )

    def test_fifo_consumption_on_return(self):
        product = _create_min_product("P1")
        bill = self._create_bill(product=product, qty=Decimal("5"), cost=Decimal("1000"), currency="SYP")
        item = bill.items.first()
        self.assertIsNotNone(item)

        layer = StockFifoLayer.objects.filter(source_model="BillItem", source_id=str(item.id)).first()
        self.assertIsNotNone(layer)
        self.assertEqual(q3(layer.qty_remaining), q3(Decimal("5")))

        BillingSV.create_return(
            actor=self.actor,
            provider_id=self.provider.id,
            status="unpaid",
            paid_amount=Decimal("0"),
            items=[{
                "bill_item_id": item.id,
                "product_id": product.id,
                "unit_index": 1,
                "qty_primary": "2",
                "container_splits": [{"code": "store", "qty_primary": "2"}],
            }],
            container=None,
            source_bill_serial=bill.serial,
            currency_code="SYP",
            valuation_mode="HISTORICAL",
        )

        layer.refresh_from_db()
        self.assertEqual(q3(layer.qty_remaining), q3(Decimal("3")))

        mv = ProductMovement.objects.filter(source_model="ProviderReturn").first()
        self.assertIsNotNone(mv)
        self.assertTrue(mv.qty_primary < 0)

    def test_multicurrency_totals_on_return(self):
        p1 = _create_min_product("SYP-Prod")
        p2 = _create_min_product("USD-Prod")

        bill = BillingSV.create_bill(
            actor=self.actor,
            provider_id=self.provider.id,
            status="unpaid",
            paid_amount=Decimal("0"),
            items=[
                {"product_id": p1.id, "unit_index": 1, "qty_raw": "1", "cost": "5000", "currency": "SYP"},
                {"product_id": p2.id, "unit_index": 1, "qty_raw": "2", "cost": "3", "currency": "USD"},
            ],
            container=self.store,
            money_container_id=self.cash.id,
            settlement_currency="SYP",
            fx_usd_syp=Decimal("15000"),
        )

        items = list(bill.items.all())
        self.assertEqual(len(items), 2)

        BillingSV.create_return(
            actor=self.actor,
            provider_id=self.provider.id,
            status="unpaid",
            paid_amount=Decimal("0"),
            items=[
                {"bill_item_id": items[0].id, "product_id": items[0].product_id, "unit_index": 1, "qty_primary": "1",
                 "container_splits": [{"code": "store", "qty_primary": "1"}]},
                {"bill_item_id": items[1].id, "product_id": items[1].product_id, "unit_index": 1, "qty_primary": "2",
                 "container_splits": [{"code": "store", "qty_primary": "2"}]},
            ],
            container=None,
            source_bill_serial=bill.serial,
            currency_code="SYP",
            valuation_mode="HISTORICAL",
        )

        pret = ProviderReturn.objects.order_by("-id").first()
        self.assertIsNotNone(pret)
        self.assertEqual(q3(pret.total_syp), q3(Decimal("5000")))
        self.assertEqual(q3(pret.total_usd), q3(Decimal("6")))

    def test_mode1_vs_mode2_fx_difference(self):
        product = _create_min_product("USD-Only")

        bill = self._create_bill(product=product, qty=Decimal("1"), cost=Decimal("2"), currency="USD", fx=Decimal("15000"))
        item = bill.items.first()
        self.assertIsNotNone(item)

        pret_current = BillingSV.create_return(
            actor=self.actor,
            provider_id=self.provider.id,
            status="unpaid",
            paid_amount=Decimal("0"),
            items=[{
                "bill_item_id": item.id,
                "product_id": product.id,
                "unit_index": 1,
                "qty_primary": "1",
                "container_splits": [{"code": "store", "qty_primary": "1"}],
            }],
            container=None,
            source_bill_serial=bill.serial,
            currency_code="SYP",
            valuation_mode="CURRENT_FX",
        )
        self.assertEqual(q3(pret_current.total), q3(Decimal("2") * Decimal("20000")))

        bill2 = self._create_bill(product=product, qty=Decimal("1"), cost=Decimal("2"), currency="USD", fx=Decimal("15000"))
        item2 = bill2.items.first()
        pret_hist = BillingSV.create_return(
            actor=self.actor,
            provider_id=self.provider.id,
            status="unpaid",
            paid_amount=Decimal("0"),
            items=[{
                "bill_item_id": item2.id,
                "product_id": product.id,
                "unit_index": 1,
                "qty_primary": "1",
                "container_splits": [{"code": "store", "qty_primary": "1"}],
            }],
            container=None,
            source_bill_serial=bill2.serial,
            currency_code="SYP",
            valuation_mode="HISTORICAL",
        )
        self.assertEqual(q3(pret_hist.total), q3(Decimal("2") * Decimal("15000")))

    def test_paid_return_increases_container_balance(self):
        product = _create_min_product("Paid-Prod")
        bill = self._create_bill(product=product, qty=Decimal("1"), cost=Decimal("1000"), currency="SYP", fx=Decimal("15000"))
        item = bill.items.first()

        before = FinSV.container_balance(container_id=self.cash.id).get("SYP", DEC0)

        BillingSV.create_return(
            actor=self.actor,
            provider_id=self.provider.id,
            status="paid",
            paid_amount=Decimal("1000"),
            items=[{
                "bill_item_id": item.id,
                "product_id": product.id,
                "unit_index": 1,
                "qty_primary": "1",
                "container_splits": [{"code": "store", "qty_primary": "1"}],
            }],
            container=None,
            source_bill_serial=bill.serial,
            money_container_id=self.cash.id,
            currency_code="SYP",
            valuation_mode="HISTORICAL",
        )

        after = FinSV.container_balance(container_id=self.cash.id).get("SYP", DEC0)
        self.assertEqual(q3(after - before), q3(Decimal("1000")))

    def test_partial_return_creates_creditor_receipt(self):
        product = _create_min_product("ReturnPay")
        bill = self._create_bill(product=product, qty=Decimal("2"), cost=Decimal("1000"), currency="SYP", fx=Decimal("15000"))
        item = bill.items.first()
        self.assertIsNotNone(item)

        pret = BillingSV.create_return(
            actor=self.actor,
            provider_id=self.provider.id,
            status="partial",
            paid_amount=Decimal("500"),
            items=[{
                "bill_item_id": item.id,
                "product_id": product.id,
                "unit_index": 1,
                "qty_primary": "1",
                "container_splits": [{"code": "store", "qty_primary": "1"}],
            }],
            container=None,
            source_bill_serial=bill.serial,
            money_container_id=self.cash.id,
            currency_code="SYP",
            valuation_mode="HISTORICAL",
        )

        entry = CreditorDebt.objects.get(
            source_app="billing",
            source_model="ProviderReturn",
            source_id=str(pret.id),
            currency_code="SYP",
        )
        receipt_row = CreditorReceipt.objects.filter(entry=entry).first()
        self.assertIsNotNone(receipt_row)
        self.assertIsNotNone(receipt_row.receipt_id)

    def test_unpaid_return_creates_creditor_debts(self):
        p1 = _create_min_product("SYP-Prod2")
        p2 = _create_min_product("USD-Prod2")

        bill = BillingSV.create_bill(
            actor=self.actor,
            provider_id=self.provider.id,
            status="unpaid",
            paid_amount=Decimal("0"),
            items=[
                {"product_id": p1.id, "unit_index": 1, "qty_raw": "1", "cost": "7000", "currency": "SYP"},
                {"product_id": p2.id, "unit_index": 1, "qty_raw": "1", "cost": "4", "currency": "USD"},
            ],
            container=self.store,
            money_container_id=self.cash.id,
            settlement_currency="SYP",
            fx_usd_syp=Decimal("15000"),
        )

        items = list(bill.items.all())

        pret = BillingSV.create_return(
            actor=self.actor,
            provider_id=self.provider.id,
            status="unpaid",
            paid_amount=Decimal("0"),
            items=[
                {"bill_item_id": items[0].id, "product_id": items[0].product_id, "unit_index": 1, "qty_primary": "1",
                 "container_splits": [{"code": "store", "qty_primary": "1"}]},
                {"bill_item_id": items[1].id, "product_id": items[1].product_id, "unit_index": 1, "qty_primary": "1",
                 "container_splits": [{"code": "store", "qty_primary": "1"}]},
            ],
            container=None,
            source_bill_serial=bill.serial,
            currency_code="SYP",
            valuation_mode="HISTORICAL",
        )

        syp_entry = CreditorDebt.objects.filter(
            source_model="ProviderReturn",
            source_id=str(pret.id),
            currency_code="SYP",
        ).first()
        usd_entry = CreditorDebt.objects.filter(
            source_model="ProviderReturn",
            source_id=str(pret.id),
            currency_code="USD",
        ).first()
        self.assertIsNotNone(syp_entry)
        self.assertIsNotNone(usd_entry)
        self.assertEqual(q3(syp_entry.total), q3(pret.total_syp))
        self.assertEqual(q3(usd_entry.total), q3(pret.total_usd))

    def test_delete_return_cleans_canonical_and_legacy_creditor_rows(self):
        product = _create_min_product("Delete-Collision")
        bill = self._create_bill(
            product=product,
            qty=Decimal("1"),
            cost=Decimal("4"),
            currency="USD",
            fx=Decimal("15000"),
        )
        item = bill.items.first()

        pret = BillingSV.create_return(
            actor=self.actor,
            provider_id=self.provider.id,
            status="unpaid",
            paid_amount=Decimal("0"),
            items=[{
                "bill_item_id": item.id,
                "product_id": product.id,
                "unit_index": 1,
                "qty_primary": "1",
                "container_splits": [{"code": "store", "qty_primary": "1"}],
            }],
            container=None,
            source_bill_serial=bill.serial,
            currency_code="USD",
            valuation_mode="HISTORICAL",
        )

        CreditorDebt.objects.create(
            provider=self.provider,
            source_app="billing",
            source_model="ProviderReturn",
            source_id=f"{pret.id}:USD",
            legacy_source_id=f"{pret.id}:USD",
            total=Decimal("1"),
            collected=Decimal("0"),
            status=CreditorDebt.Status.OPEN,
            party_type="provider",
            party_name=self.provider.name,
            doc_serial=pret.serial,
            currency_code="USD",
        )

        BillingSV.delete_return(actor=self.actor, return_id=pret.id)

        self.assertFalse(
            CreditorDebt.objects.filter(
                source_app="billing",
                source_model="ProviderReturn",
            )
            .filter(
                source_id__in=[str(pret.id), f"{pret.id}:USD"]
            )
            .exists()
        )

    def test_create_return_rejects_inactive_split_container(self):
        product = _create_min_product("Inactive-Split")
        bill = self._create_bill(product=product, qty=Decimal("1"), cost=Decimal("1000"), currency="SYP", fx=Decimal("15000"))
        item = bill.items.first()
        self.assertIsNotNone(item)

        wh1, _ = ProductContainer.objects.get_or_create(
            code="wh1",
            defaults={"name": "WH1", "is_store": False, "is_active": False},
        )
        if wh1.is_active:
            wh1.is_active = False
            wh1.save(update_fields=["is_active"])

        with self.assertRaisesMessage(ValueError, "inactive container code in return splits"):
            BillingSV.create_return(
                actor=self.actor,
                provider_id=self.provider.id,
                status="unpaid",
                paid_amount=Decimal("0"),
                items=[{
                    "bill_item_id": item.id,
                    "product_id": product.id,
                    "unit_index": 1,
                    "qty_primary": "1",
                    "container_splits": [{"code": "wh1", "qty_primary": "1"}],
                }],
                container=None,
                source_bill_serial=bill.serial,
                currency_code="SYP",
                valuation_mode="HISTORICAL",
            )

    def test_delete_return_audit_contains_reversed_receipt_ids(self):
        product = _create_min_product("Delete-Audit-Receipts")
        bill = self._create_bill(product=product, qty=Decimal("1"), cost=Decimal("1000"), currency="SYP", fx=Decimal("15000"))
        item = bill.items.first()
        self.assertIsNotNone(item)

        pret = BillingSV.create_return(
            actor=self.actor,
            provider_id=self.provider.id,
            status="paid",
            paid_amount=Decimal("1000"),
            items=[{
                "bill_item_id": item.id,
                "product_id": product.id,
                "unit_index": 1,
                "qty_primary": "1",
                "container_splits": [{"code": "store", "qty_primary": "1"}],
            }],
            container=None,
            source_bill_serial=bill.serial,
            money_container_id=self.cash.id,
            currency_code="SYP",
            valuation_mode="HISTORICAL",
        )

        expected_receipt_ids = list(
            Receipt.objects.filter(
                source_app="billing",
                source_model="ProviderReturn",
                source_id=str(pret.id),
                status=ReceiptStatus.POSTED,
            )
            .order_by("-id")
            .values_list("id", flat=True)
        )
        self.assertGreater(len(expected_receipt_ids), 0)

        BillingSV.delete_return(actor=self.actor, return_id=pret.id)

        audit_row = (
            AuditLog.objects.filter(
                target_app="billing",
                target_model="providerreturn",
                target_id=str(pret.id),
                action="delete",
            )
            .order_by("-id")
            .first()
        )
        self.assertIsNotNone(audit_row)
        meta = audit_row.meta or {}
        reversed_ids = ((meta.get("financials") or {}).get("reversed_receipt_ids") or [])
        self.assertEqual(sorted(reversed_ids), sorted(expected_receipt_ids))
        self.assertEqual(
            Receipt.objects.filter(id__in=expected_receipt_ids, status=ReceiptStatus.REVERSED).count(),
            len(expected_receipt_ids),
        )

    def test_return_updates_latest_cost_only(self):
        product = _create_min_product("Cost-Prod")
        product.latest_price_syp = Decimal("9000")
        product.latest_price_usd = Decimal("3")
        product.save(update_fields=["latest_price_syp", "latest_price_usd"])

        bill = self._create_bill(product=product, qty=Decimal("1"), cost=Decimal("1200"), currency="SYP", fx=Decimal("15000"))
        item = bill.items.first()

        BillingSV.create_return(
            actor=self.actor,
            provider_id=self.provider.id,
            status="unpaid",
            paid_amount=Decimal("0"),
            items=[{
                "bill_item_id": item.id,
                "product_id": product.id,
                "unit_index": 1,
                "qty_primary": "1",
                "container_splits": [{"code": "store", "qty_primary": "1"}],
            }],
            container=None,
            source_bill_serial=bill.serial,
            currency_code="SYP",
            valuation_mode="HISTORICAL",
        )

        product.refresh_from_db()
        self.assertEqual(q3(product.latest_cost_syp), q3(Decimal("1200")))
        self.assertEqual(q3(product.latest_price_syp), q3(Decimal("9000")))

    def test_currency_flags_do_not_block_historical_returns(self):
        product = _create_min_product("Flag-Prod")
        bill = self._create_bill(product=product, qty=Decimal("1"), cost=Decimal("800"), currency="SYP", fx=Decimal("15000"))
        item = bill.items.first()

        product.allow_syp_purchasing = False
        product.allow_usd_purchasing = False
        product.save(update_fields=["allow_syp_purchasing", "allow_usd_purchasing"])

        pret = BillingSV.create_return(
            actor=self.actor,
            provider_id=self.provider.id,
            status="unpaid",
            paid_amount=Decimal("0"),
            items=[{
                "bill_item_id": item.id,
                "product_id": product.id,
                "unit_index": 1,
                "qty_primary": "1",
                "container_splits": [{"code": "store", "qty_primary": "1"}],
            }],
            container=None,
            source_bill_serial=bill.serial,
            currency_code="SYP",
            valuation_mode="HISTORICAL",
        )

        self.assertIsNotNone(pret)


