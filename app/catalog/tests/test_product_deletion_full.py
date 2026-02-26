from __future__ import annotations

import json
from decimal import Decimal
from threading import Event, Thread
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from accounts.models import AccountProfile
from billing import services as BillingSV
from billing.models import Bill, BillItem, Provider
from catalog.models import (
    Product,
    ProductBarcode,
    ProductCollection,
    ProductSet,
    ProductUnitId,
    UnitType,
)
from catalog.services import deletion_policy as DelSV
from financials import services as FinSV
from financials.models import Currency, MoneyContainer, MoneyContainerCurrency
from inventory import services as InvSV
from inventory.models import ProductMovement
from pos import services as PosSV
from pos.models import SalesBill, SalesBillRow
from stock.models import ProductContainer, StockFifoLayer


class ProductDeletionTestMixin:
    def setUp(self):
        super().setUp()
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="manager", password="pass")
        AccountProfile.objects.create(user=self.user, role=AccountProfile.Role.MANAGER)
        self.client.force_login(self.user)

        self.store, _ = ProductContainer.objects.get_or_create(
            code="store",
            defaults={"name": "Store", "is_store": True, "is_active": True},
        )
        self.wh1, _ = ProductContainer.objects.get_or_create(
            code="wh1",
            defaults={"name": "WH1", "is_active": True},
        )
        self.wh2, _ = ProductContainer.objects.get_or_create(
            code="wh2",
            defaults={"name": "WH2", "is_active": True},
        )
        self.extra, _ = ProductContainer.objects.get_or_create(
            code="extra",
            defaults={"name": "Extra", "is_active": True},
        )

        self.collection = ProductCollection.objects.create(name="COL-A")
        self.set_obj = ProductSet.objects.create(collection=self.collection, name="SET-A")

    def create_product(self, *, name: str = "PROD-A", active: bool = True) -> Product:
        return Product.objects.create(
            name=name,
            set=self.set_obj,
            unit_primary=UnitType.PIECE,
            unit_secondary="",
            conversion_factor=None,
            cost=Decimal("1.0000"),
            price=Decimal("2.0000"),
            is_active=active,
            allow_syp_sales=True,
            allow_syp_purchasing=True,
            allow_usd_sales=False,
            allow_usd_purchasing=False,
            default_purchase_currency="SYP",
            default_sale_currency="SYP",
            default_cost_syp=Decimal("1.0000"),
            default_cost_usd=Decimal("0.0000"),
            default_price_syp=Decimal("2.0000"),
            default_price_usd=Decimal("0.0000"),
        )

    def add_stock(self, *, product: Product, container: ProductContainer, qty: str) -> None:
        StockFifoLayer.objects.create(
            product=product,
            container=container,
            qty_remaining=Decimal(qty),
            unit_cost=Decimal("1.0000"),
            cost_currency="SYP",
        )

    def add_identifiers(self, *, product: Product, barcode: str, unit_id: str) -> None:
        ProductBarcode.objects.create(
            product=product,
            unit_index=ProductBarcode.UnitIndex.PRIMARY,
            barcode=barcode,
            is_active=product.is_active,
        )
        ProductUnitId.objects.create(
            product=product,
            unit_index=ProductUnitId.UnitIndex.PRIMARY,
            value=unit_id,
            is_active=product.is_active,
        )

    def make_provider(self, *, name: str = "Prov-A") -> Provider:
        return Provider.objects.create(name=name)

    def make_bill_with_item(self, *, product: Product) -> Bill:
        bill = Bill.objects.create(provider=self.make_provider(name="Prov-B"), created_by=self.user, total=Decimal("0"))
        BillItem.objects.create(
            bill=bill,
            product=product,
            product_name_at_txn=product.name,
            unit_index=1,
            conv_factor_at_txn=Decimal("1.0000"),
            unit_1_label_at_txn="piece",
            unit_2_label_at_txn="",
            qty_used_at_txn=Decimal("1.000"),
            qty_primary=Decimal("1.000"),
            cost=Decimal("1.0000"),
            price=Decimal("2.0000"),
            line_total=Decimal("1.000"),
            currency="SYP",
        )
        return bill


class ProductDisableBehaviorTests(ProductDeletionTestMixin, TestCase):
    def test_disable_with_zero_stock_succeeds(self):
        p = self.create_product(name="DIS-ZERO")
        DelSV.disable_product(p)
        p.refresh_from_db()
        self.assertFalse(p.is_active)

    def test_disable_with_positive_store_stock_blocked(self):
        p = self.create_product(name="DIS-POS")
        self.add_stock(product=p, container=self.store, qty="1.000")
        with self.assertRaises(DelSV.ProductDisableBlockedError):
            DelSV.disable_product(p)
        p.refresh_from_db()
        self.assertTrue(p.is_active)

    def test_disable_with_negative_wh1_stock_blocked(self):
        p = self.create_product(name="DIS-NEG")
        self.add_stock(product=p, container=self.wh1, qty="-1.000")
        with self.assertRaises(DelSV.ProductDisableBlockedError):
            DelSV.disable_product(p)
        p.refresh_from_db()
        self.assertTrue(p.is_active)

    def test_disable_with_multiple_container_stock_blocked(self):
        p = self.create_product(name="DIS-MULTI")
        self.add_stock(product=p, container=self.store, qty="1.000")
        self.add_stock(product=p, container=self.wh2, qty="-0.500")
        with self.assertRaises(DelSV.ProductDisableBlockedError):
            DelSV.disable_product(p)

    def test_disable_twice_is_idempotent(self):
        p = self.create_product(name="DIS-IDEMP")
        DelSV.disable_product(p)
        p2, _ = DelSV.disable_product(p)
        self.assertFalse(p2.is_active)

    def test_disable_already_disabled_noop_when_zero_stock(self):
        p = self.create_product(name="DIS-ALREADY", active=False)
        p2, _ = DelSV.disable_product(p)
        self.assertFalse(p2.is_active)

    def test_disable_endpoint_blocked_redirect_when_stock_nonzero(self):
        p = self.create_product(name="DIS-ENDPOINT")
        self.add_stock(product=p, container=self.store, qty="1.000")
        resp = self.client.post(reverse("manager_product_delete", kwargs={"pk": p.id}))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("delete_blocked=1", resp.url)

    def test_disable_uses_only_store_wh1_wh2(self):
        p = self.create_product(name="DIS-SCOPE")
        self.add_stock(product=p, container=self.extra, qty="5.000")
        DelSV.disable_product(p)
        p.refresh_from_db()
        self.assertFalse(p.is_active)


class ProductReactivationTests(ProductDeletionTestMixin, TestCase):
    def test_reactivate_disabled_product_succeeds(self):
        p = self.create_product(name="REA-1", active=False)
        p2 = DelSV.reactivate_product(p)
        self.assertTrue(p2.is_active)

    def test_reactivate_active_product_noop(self):
        p = self.create_product(name="REA-2", active=True)
        p2 = DelSV.reactivate_product(p)
        self.assertTrue(p2.is_active)

    def test_after_reactivation_product_usable_in_inventory_flow(self):
        p = self.create_product(name="REA-3")
        DelSV.disable_product(p)
        DelSV.reactivate_product(p)
        mv = InvSV.record_movement(
            actor=self.user,
            product=p,
            unit_index=1,
            qty_primary=Decimal("1.000"),
            unit_cost=Decimal("1.0000"),
            cost_currency="SYP",
            movement_type=ProductMovement.MovementType.ADJUSTMENT,
            source_app="tests",
            source_model="ProductReactivationTests",
            source_id="1",
            container=self.store,
        )
        self.assertEqual(mv.product_id, p.id)

    def test_reactivation_restores_identifier_activity(self):
        p = self.create_product(name="REA-4")
        self.add_identifiers(product=p, barcode="REA-BC-1", unit_id="REA-UID-1")
        DelSV.disable_product(p)
        self.assertFalse(ProductBarcode.objects.filter(product=p, is_active=True).exists())
        self.assertFalse(ProductUnitId.objects.filter(product=p, is_active=True).exists())
        DelSV.reactivate_product(p)
        self.assertTrue(ProductBarcode.objects.filter(product=p, is_active=True).exists())
        self.assertTrue(ProductUnitId.objects.filter(product=p, is_active=True).exists())


class ProductHardDeleteTests(ProductDeletionTestMixin, TestCase):
    def test_hard_delete_without_history_or_stock_succeeds(self):
        p = self.create_product(name="HD-OK")
        self.add_identifiers(product=p, barcode="HD-BC-1", unit_id="HD-UID-1")
        DelSV.hard_delete_product(p)
        self.assertFalse(Product.objects.filter(id=p.id).exists())
        self.assertFalse(ProductBarcode.objects.filter(product_id=p.id).exists())
        self.assertFalse(ProductUnitId.objects.filter(product_id=p.id).exists())

    def test_hard_delete_blocked_when_stock_exists(self):
        p = self.create_product(name="HD-STOCK")
        self.add_stock(product=p, container=self.wh2, qty="1.000")
        with self.assertRaises(DelSV.ProductHardDeleteBlockedError):
            DelSV.hard_delete_product(p)

    def test_hard_delete_blocked_when_movement_exists(self):
        p = self.create_product(name="HD-MOV")
        ProductMovement.objects.create(
            product=p,
            qty_primary=Decimal("0.000"),
            unit_index=1,
            unit_cost=Decimal("0.0000"),
            total_cost=Decimal("0.000"),
            movement_type=ProductMovement.MovementType.ADJUSTMENT,
            source_app="tests",
            source_model="ProductHardDeleteTests",
            source_id="mv-1",
        )
        with self.assertRaises(DelSV.ProductHardDeleteBlockedError):
            DelSV.hard_delete_product(p)

    def test_hard_delete_blocked_when_billing_reference_exists(self):
        p = self.create_product(name="HD-BILL")
        self.make_bill_with_item(product=p)
        with self.assertRaises(DelSV.ProductHardDeleteBlockedError):
            DelSV.hard_delete_product(p)

    def test_hard_delete_blocked_when_pos_row_exists(self):
        p = self.create_product(name="HD-POS")
        bill = SalesBill.objects.create()
        SalesBillRow.objects.create(
            bill=bill,
            product_id=p.id,
            product_name=p.name,
            qty=Decimal("1.000"),
            uom_index=1,
            unit_price=Decimal("1.000"),
            sale_currency="SYP",
        )
        with self.assertRaises(DelSV.ProductHardDeleteBlockedError):
            DelSV.hard_delete_product(p)


class GlobalUniquenessTests(ProductDeletionTestMixin, TestCase):
    def test_duplicate_name_active_active_fails(self):
        self.create_product(name="UNQ-NAME-1", active=True)
        with self.assertRaises((ValidationError, IntegrityError)):
            self.create_product(name="UNQ-NAME-1", active=True)

    def test_duplicate_name_active_disabled_fails(self):
        self.create_product(name="UNQ-NAME-2", active=False)
        with self.assertRaises((ValidationError, IntegrityError)):
            self.create_product(name="UNQ-NAME-2", active=True)

    def test_duplicate_barcode_active_disabled_fails(self):
        p1 = self.create_product(name="UNQ-BC-1", active=False)
        self.add_identifiers(product=p1, barcode="UNQ-BC-X", unit_id="UNQ-UID-X1")
        p2 = self.create_product(name="UNQ-BC-2", active=True)
        with self.assertRaises(IntegrityError):
            ProductBarcode.objects.create(product=p2, unit_index=1, barcode="UNQ-BC-X", is_active=True)

    def test_duplicate_unit_id_active_disabled_fails(self):
        p1 = self.create_product(name="UNQ-UID-1", active=False)
        self.add_identifiers(product=p1, barcode="UNQ-BC-Y1", unit_id="UNQ-UID-Z")
        p2 = self.create_product(name="UNQ-UID-2", active=True)
        with self.assertRaises(IntegrityError):
            ProductUnitId.objects.create(product=p2, unit_index=1, value="UNQ-UID-Z", is_active=True)

    def test_case_insensitive_name_duplicate_fails(self):
        self.create_product(name="CaseName", active=True)
        with self.assertRaises((ValidationError, IntegrityError)):
            self.create_product(name="casename", active=True)


class OperationalGuardsTests(ProductDeletionTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.product = self.create_product(name="OPS-DISABLED", active=False)
        self.provider = self.make_provider(name="Prov-OPS")

        self.syp, _ = Currency.objects.get_or_create(
            code="SYP",
            defaults={"name": "SYP", "decimals": 0, "is_active": True},
        )
        self.usd, _ = Currency.objects.get_or_create(
            code="USD",
            defaults={"name": "USD", "decimals": 2, "is_active": True},
        )
        FinSV.set_current_fx(actor=self.user, rate_syp_per_usd=Decimal("10000"))

        self.cash = MoneyContainer.objects.create(
            name="Cash-OPS",
            container_type=MoneyContainer.ContainerType.DRAWER,
            is_active=True,
            created_by=self.user,
            balance_syp=Decimal("0"),
            balance_usd=Decimal("0"),
        )
        MoneyContainerCurrency.objects.get_or_create(
            container=self.cash,
            currency=self.syp,
            defaults={"is_enabled": True},
        )
        MoneyContainerCurrency.objects.get_or_create(
            container=self.cash,
            currency=self.usd,
            defaults={"is_enabled": True},
        )

    def test_disabled_product_cannot_create_bill_item(self):
        with self.assertRaises(ValidationError):
            BillingSV.create_bill(
                actor=self.user,
                provider_id=self.provider.id,
                status="unpaid",
                paid_amount=Decimal("0.000"),
                items=[
                    {
                        "product_id": self.product.id,
                        "unit_index": 1,
                        "qty_raw": "1",
                        "cost": "1.0000",
                        "price": "2.0000",
                        "currency": "SYP",
                    }
                ],
                container=self.store,
                money_container_id=self.cash.id,
                settlement_currency="SYP",
            )

    def test_disabled_product_cannot_create_provider_return(self):
        with self.assertRaises(ValidationError):
            BillingSV.create_return(
                actor=self.user,
                provider_id=self.provider.id,
                status="unpaid",
                paid_amount=Decimal("0.000"),
                items=[
                    {
                        "product_id": self.product.id,
                        "unit_index": 1,
                        "qty_raw": "1.000",
                        "cost": "1.0000",
                        "currency": "SYP",
                    }
                ],
                container=self.store,
                currency_code="SYP",
            )

    def test_disabled_product_cannot_finalize_pos_sale(self):
        bill = SalesBill.objects.create(
            finalized=True,
            parked=False,
            pay_status=SalesBill.PAY_NONE,
            total_amount=Decimal("1.000"),
            total_syp=Decimal("1.000"),
            settlement_mode=SalesBill.SETTLE_SPLIT,
            settlement_currency="SYP",
        )
        SalesBillRow.objects.create(
            bill=bill,
            product_id=self.product.id,
            product_name=self.product.name,
            qty=Decimal("1.000"),
            uom_index=1,
            unit_price=Decimal("1.000"),
            sale_currency="SYP",
        )
        with self.assertRaises(ValidationError):
            PosSV.finalize_pos_bill(bill=bill, actor=self.user)

    def test_disabled_product_cannot_create_stock_movement(self):
        with self.assertRaises(ValidationError):
            InvSV.record_movement(
                actor=self.user,
                product=self.product,
                unit_index=1,
                qty_primary=Decimal("1.000"),
                unit_cost=Decimal("1.0000"),
                cost_currency="SYP",
                movement_type=ProductMovement.MovementType.ADJUSTMENT,
                source_app="tests",
                source_model="OperationalGuardsTests",
                source_id="1",
                container=self.store,
            )

    def test_stock_batches_api_rejects_disabled_product_without_500(self):
        resp = self.client.get(
            reverse("stock:api_product_batches"),
            {"product_id": self.product.id, "container": "store"},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertNotEqual(resp.status_code, 500)

    def test_billing_api_bill_save_disabled_product_does_not_return_500(self):
        payload = {
            "provider": {"id": self.provider.id},
            "container": "store",
            "money_container_id": self.cash.id,
            "currency_code": "SYP",
            "items": [
                {
                    "product_id": self.product.id,
                    "unit_index": 1,
                    "qty_raw": "1",
                    "cost": "1.0000",
                    "price": "2.0000",
                    "currency": "SYP",
                }
            ],
            "pay": {"status": "unpaid", "paid_amount": "0"},
        }
        resp = self.client.post(
            reverse("billing_api_bill_save"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertNotEqual(resp.status_code, 500)


class ManagerApiFilteringTests(ProductDeletionTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.active_product = self.create_product(name="LIST-ACTIVE", active=True)
        self.disabled_product = self.create_product(name="LIST-DISABLED", active=False)

    def test_browser_products_default_excludes_disabled(self):
        resp = self.client.get(reverse("api_browser_products"), {"sid": self.set_obj.id, "page": 1})
        self.assertEqual(resp.status_code, 200)
        ids = [it["id"] for it in resp.json().get("items", [])]
        self.assertIn(self.active_product.id, ids)
        self.assertNotIn(self.disabled_product.id, ids)

    def test_browser_products_show_disabled_includes_disabled(self):
        resp = self.client.get(
            reverse("api_browser_products"),
            {"sid": self.set_obj.id, "page": 1, "show_disabled": 1},
        )
        self.assertEqual(resp.status_code, 200)
        items = resp.json().get("items", [])
        ids = [it["id"] for it in items]
        self.assertIn(self.active_product.id, ids)
        self.assertIn(self.disabled_product.id, ids)

    def test_operational_search_api_excludes_disabled_products(self):
        resp = self.client.get(
            reverse("api_product_search"),
            {"q": "LIST-DISABLED", "mode": "name"},
        )
        self.assertEqual(resp.status_code, 200)
        items = resp.json().get("items", [])
        ids = {it.get("id") for it in items if it.get("type") == "product"}
        self.assertNotIn(self.disabled_product.id, ids)


class StockAggregationRuleTests(ProductDeletionTestMixin, TestCase):
    def test_disable_blocked_when_any_of_three_nonzero(self):
        p = self.create_product(name="STK-RULE-1")
        self.add_stock(product=p, container=self.wh2, qty="0.001")
        with self.assertRaises(DelSV.ProductDisableBlockedError):
            DelSV.disable_product(p)

    def test_disable_allowed_only_when_store_wh1_wh2_exactly_zero(self):
        p = self.create_product(name="STK-RULE-2")
        self.add_stock(product=p, container=self.store, qty="0.000")
        self.add_stock(product=p, container=self.wh1, qty="0.000")
        self.add_stock(product=p, container=self.wh2, qty="0.000")
        DelSV.disable_product(p)
        p.refresh_from_db()
        self.assertFalse(p.is_active)


class ConcurrencySimulationTests(ProductDeletionTestMixin, TransactionTestCase):
    reset_sequences = True

    def test_disable_race_with_concurrent_stock_add_keeps_consistency(self):
        p = self.create_product(name="RACE-1")
        ready = Event()
        done_insert = Event()
        out: dict[str, object] = {}
        original = DelSV.stock_by_container

        def delayed_stock_by_container(*args, **kwargs):
            ready.set()
            done_insert.wait(timeout=3)
            return original(*args, **kwargs)

        def worker():
            try:
                DelSV.disable_product(p)
                out["ok"] = True
            except Exception as exc:  # pragma: no cover - asserted below
                out["exc"] = exc

        with patch("catalog.services.deletion_policy.stock_by_container", side_effect=delayed_stock_by_container):
            t = Thread(target=worker)
            t.start()
            self.assertTrue(ready.wait(timeout=3))
            self.add_stock(product=p, container=self.store, qty="1.000")
            done_insert.set()
            t.join(timeout=5)

        p.refresh_from_db()
        has_stock = StockFifoLayer.objects.filter(product=p, container=self.store, qty_remaining__gt=0).exists()
        self.assertFalse((not p.is_active) and has_stock)

        if out.get("ok"):
            self.assertFalse(has_stock)
        else:
            self.assertIsInstance(out.get("exc"), DelSV.ProductDisableBlockedError)


class MigrationSafetyTests(ProductDeletionTestMixin, TestCase):
    def test_global_uniqueness_constraints_exist_and_active_only_removed(self):
        with connection.cursor() as cursor:
            product_constraints = connection.introspection.get_constraints(cursor, "catalog_product")
            barcode_constraints = connection.introspection.get_constraints(cursor, "catalog_productbarcode")
            unit_id_constraints = connection.introspection.get_constraints(cursor, "catalog_productunitid")

        self.assertIn("uq_product_name_ci_global", product_constraints)
        self.assertNotIn("uq_product_name_ci_active", product_constraints)
        self.assertIn("uq_barcode_global", barcode_constraints)
        self.assertNotIn("uq_barcode_active", barcode_constraints)
        self.assertIn("uq_unit_id_value_global", unit_id_constraints)
        self.assertNotIn("uq_unit_id_value_active", unit_id_constraints)
