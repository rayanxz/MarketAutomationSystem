from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import AccountProfile
from billing import services as BillingSV
from billing.models import Provider
from catalog.models import Product, ProductCollection, ProductSet, UnitType
from financials import services as FinSV
from financials.models import MoneyContainer, Currency, MoneyContainerCurrency, ContainerFeature
from inventory import services as InvSV
from inventory.models import ProductMovement, SaleCostPart
from pos import services as POSSV
from pos import services_returns as ReturnSV
from pos.models import SalesBill, SalesBillRow
from stock import services as StockSV
from stock.models import ProductContainer, StockEntry, StockFifoLayer
from stock.services import MissingCostBasisError, MissingFifoCostBasisError

from .utils import (
    create_collection_set,
    create_money_container,
    create_product,
    create_provider,
    create_stock_container,
    create_user_with_role,
    ensure_currency,
    ensure_fx,
)


class CurrencyStrictnessInventoryStockTests(TestCase):
    def setUp(self):
        self.user = create_user_with_role("strict_mgr_stock", AccountProfile.Role.MANAGER)
        ensure_currency("SYP", "SYP")
        ensure_currency("USD", "USD")
        ensure_fx(Decimal("10000"))
        self.container = create_stock_container("store", "Store", is_store=True)
        self.wh1 = create_stock_container("wh1", "WH1", is_store=False)
        self.provider = create_provider("Strict Prov")
        self.money = create_money_container(name="Strict Drawer", user=self.user, enable_syp=True, enable_usd=True)

    def _mk_product(self, name: str) -> Product:
        _, pset = create_collection_set(f"C-{name}", f"S-{name}")
        return create_product(name=name, set_obj=pset, unit_primary=UnitType.PIECE, unit_secondary="", conversion_factor=None)

    def test_record_movement_requires_explicit_cost_currency(self):
        prod = self._mk_product("MvNoCur")
        with self.assertRaisesRegex(MissingCostBasisError, "Missing or invalid movement cost currency"):
            InvSV.record_movement(
                actor=self.user,
                product=prod,
                unit_index=1,
                qty_primary=Decimal("1"),
                unit_cost=Decimal("1"),
                cost_currency=None,
                movement_type=ProductMovement.MovementType.ADJUSTMENT,
                source_app="tests",
                source_model="Strict",
                source_id="mv-no-cur",
                container=self.container,
            )

    def test_record_purchase_item_requires_explicit_or_deterministic_currency(self):
        prod = self._mk_product("PurchNoDet")
        prod.allow_syp_purchasing = True
        prod.allow_usd_purchasing = True
        prod.default_purchase_currency = None
        prod.save(update_fields=["allow_syp_purchasing", "allow_usd_purchasing", "default_purchase_currency"])

        mv = InvSV.record_purchase_item(
            actor=self.user,
            product=prod,
            unit_index=1,
            qty_primary=Decimal("1"),
            unit_cost=Decimal("2"),
            cost_currency=None,
            source_app="tests",
            source_model="Strict",
            source_id="purch-no-cur",
            container=self.container,
        )
        self.assertEqual(mv.cost_currency_at_txn, "SYP")

    def test_record_purchase_item_deterministic_inference_works(self):
        prod = self._mk_product("PurchDet")
        prod.allow_syp_purchasing = False
        prod.allow_usd_purchasing = True
        prod.default_purchase_currency = None
        prod.save(update_fields=["allow_syp_purchasing", "allow_usd_purchasing", "default_purchase_currency"])

        mv = InvSV.record_purchase_item(
            actor=self.user,
            product=prod,
            unit_index=1,
            qty_primary=Decimal("2"),
            unit_cost=Decimal("3"),
            cost_currency=None,
            source_app="tests",
            source_model="Strict",
            source_id="purch-det-cur",
            container=self.container,
        )
        self.assertEqual(mv.cost_currency_at_txn, "USD")
        layer = StockFifoLayer.objects.filter(product=prod, container=self.container).first()
        self.assertIsNotNone(layer)
        self.assertEqual(layer.cost_currency, "USD")

    def test_record_sale_item_without_fifo_raises(self):
        prod = self._mk_product("SaleNoFifo")
        with self.assertRaisesRegex(MissingFifoCostBasisError, f"product {prod.id}"):
            InvSV.record_sale_item(
                actor=self.user,
                product=prod,
                unit_index=1,
                qty_primary=Decimal("1"),
                unit_cost=Decimal("0"),
                source_app="tests",
                source_model="Strict",
                source_id="sale-no-fifo",
                container=self.container,
            )

    def test_record_sale_item_insufficient_fifo_raises_explicit(self):
        prod = self._mk_product("SaleShort")
        StockSV.fifo_add_incoming(
            product=prod,
            container=self.container,
            qty_primary=Decimal("1"),
            unit_cost=Decimal("1"),
            cost_currency="SYP",
            source_app="tests",
            source_model="Strict",
            source_id="seed-short",
        )
        with self.assertRaisesRegex(MissingFifoCostBasisError, "requested"):
            InvSV.record_sale_item(
                actor=self.user,
                product=prod,
                unit_index=1,
                qty_primary=Decimal("2"),
                unit_cost=Decimal("0"),
                source_app="tests",
                source_model="Strict",
                source_id="sale-short",
                container=self.container,
            )

    def test_record_sale_item_mixed_fifo_currencies_raises(self):
        prod = self._mk_product("SaleMixed")
        StockSV.fifo_add_incoming(
            product=prod,
            container=self.container,
            qty_primary=Decimal("1"),
            unit_cost=Decimal("1"),
            cost_currency="SYP",
            source_app="tests",
            source_model="Strict",
            source_id="seed-mixed-1",
        )
        StockSV.fifo_add_incoming(
            product=prod,
            container=self.container,
            qty_primary=Decimal("1"),
            unit_cost=Decimal("2"),
            cost_currency="USD",
            source_app="tests",
            source_model="Strict",
            source_id="seed-mixed-2",
        )
        with self.assertRaisesRegex(MissingCostBasisError, "Mixed FIFO cost currencies"):
            InvSV.record_sale_item(
                actor=self.user,
                product=prod,
                unit_index=1,
                qty_primary=Decimal("2"),
                unit_cost=Decimal("0"),
                source_app="tests",
                source_model="Strict",
                source_id="sale-mixed",
                container=self.container,
            )

    def test_fifo_transfer_preserves_cost_currency(self):
        prod = self._mk_product("TransferUSD")
        prod.allow_usd_purchasing = True
        prod.default_purchase_currency = "USD"
        prod.default_cost_usd = Decimal("2")
        prod.save(update_fields=["allow_usd_purchasing", "default_purchase_currency", "default_cost_usd"])
        BillingSV.create_bill(
            actor=self.user,
            provider_id=self.provider.id,
            status="paid",
            paid_amount=Decimal("4"),
            items=[
                {
                    "product_id": prod.id,
                    "unit_index": 1,
                    "qty_raw": "2",
                    "cost": "2",
                    "price": "3",
                    "currency": "USD",
                }
            ],
            update_product_defaults=False,
            container=self.container,
            money_container_id=self.money.id,
            settlement_currency="USD",
        )
        src_layer = StockFifoLayer.objects.filter(product=prod, container=self.container).first()
        self.assertIsNotNone(src_layer)
        self.assertEqual(src_layer.cost_currency, "USD")

        out_mv, in_mv = StockSV.transfer_from_batch(
            actor=self.user,
            batch=src_layer,
            to_container=self.wh1,
            qty_primary=Decimal("1"),
            ref="STRICT-TX",
            line_no=1,
        )
        moved_layer = StockFifoLayer.objects.filter(product=prod, container=self.wh1).first()
        self.assertIsNotNone(moved_layer)
        self.assertEqual(moved_layer.cost_currency, "USD")
        self.assertEqual(out_mv.cost_currency_at_txn, "USD")
        self.assertEqual(in_mv.cost_currency_at_txn, "USD")

    def test_fifo_rebuild_with_missing_movement_currency_fails(self):
        prod = self._mk_product("RebuildBadMv")
        ProductMovement.objects.create(
            product=prod,
            container=self.container,
            qty_primary=Decimal("1"),
            unit_index=1,
            unit_cost=Decimal("5"),
            total_cost=Decimal("5"),
            movement_type=ProductMovement.MovementType.PURCHASE,
            source_app="tests",
            source_model="ManualBad",
            source_id="rbad-1",
            cost_currency_at_txn=None,
        )
        with self.assertRaisesRegex(MissingCostBasisError, "Missing cost currency snapshot for movement"):
            StockSV.rebuild_fifo_from_inventory()


class CurrencyStrictnessPosTests(TestCase):
    def setUp(self):
        self.user = create_user_with_role("strict_mgr_pos", AccountProfile.Role.MANAGER)
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        ensure_currency("SYP", "SYP")
        ensure_currency("USD", "USD")
        ensure_fx(Decimal("10000"))

        self.container = create_stock_container("store", "Store", is_store=True)
        self.money = create_money_container(
            name="Strict POS Drawer",
            user=self.user,
            enable_syp=True,
            enable_usd=True,
            with_pos_feature=True,
        )
        self.client.force_login(self.user)

    def _mk_product(self, name: str) -> Product:
        _, pset = create_collection_set(f"CP-{name}", f"SP-{name}")
        return create_product(name=name, set_obj=pset, unit_primary=UnitType.PIECE, unit_secondary="", conversion_factor=None)

    def test_pos_finalize_fails_without_fifo_basis(self):
        prod = self._mk_product("PosNoFifo")
        StockEntry.objects.update_or_create(
            product=prod,
            container=self.container,
            defaults={"qty_primary": Decimal("5"), "avg_unit_cost": Decimal("0")},
        )
        bill = SalesBill.objects.create(
            parked=False,
            finalized=True,
            pay_status=SalesBill.PAY_NONE,
            settlement_mode=SalesBill.SETTLE_SPLIT,
            money_container=self.money,
            cashier=self.user,
        )
        SalesBillRow.objects.create(
            bill=bill,
            product_id=prod.id,
            product_name=prod.name,
            product_name_at_txn=prod.name,
            product_number=str(prod.id),
            qty=Decimal("1"),
            uom_index=1,
            unit_price=Decimal("10"),
            sale_currency="SYP",
        )
        with self.assertRaisesRegex(MissingFifoCostBasisError, f"product {prod.id}"):
            POSSV.finalize_pos_bill(bill=bill, actor=self.user)

    def test_pos_return_fails_without_sale_cost_parts(self):
        prod = self._mk_product("PosRetNoParts")
        bill = SalesBill.objects.create(
            parked=False,
            finalized=True,
            pay_status=SalesBill.PAY_FULL,
            settlement_mode=SalesBill.SETTLE_SPLIT,
            money_container=self.money,
            cashier=self.user,
            total_syp=Decimal("10"),
        )
        row = SalesBillRow.objects.create(
            bill=bill,
            product_id=prod.id,
            product_name=prod.name,
            product_name_at_txn=prod.name,
            product_number=str(prod.id),
            qty=Decimal("1"),
            uom_index=1,
            unit_price=Decimal("10"),
            sale_currency="SYP",
        )
        ret = ReturnSV.create_sales_return_draft(
            actor=self.user,
            sale_bill_id=bill.id,
            stock_container_id=self.container.id,
            items=[{"sale_row_id": row.id, "qty": Decimal("1"), "reason": "strict"}],
        )
        with self.assertRaisesRegex(ValueError, "Missing sale cost parts"):
            ReturnSV.post_sales_return(
                actor=self.user,
                return_id=ret.id,
                settle_mode="credit",
            )

    def test_pos_return_fails_on_mixed_cost_currencies(self):
        prod = self._mk_product("PosRetMixed")
        bill = SalesBill.objects.create(
            parked=False,
            finalized=True,
            pay_status=SalesBill.PAY_FULL,
            settlement_mode=SalesBill.SETTLE_SPLIT,
            money_container=self.money,
            cashier=self.user,
            total_syp=Decimal("10"),
        )
        row = SalesBillRow.objects.create(
            bill=bill,
            product_id=prod.id,
            product_name=prod.name,
            product_name_at_txn=prod.name,
            product_number=str(prod.id),
            qty=Decimal("1"),
            uom_index=1,
            unit_price=Decimal("10"),
            sale_currency="SYP",
        )
        mv = ProductMovement.objects.create(
            product=prod,
            container=self.container,
            qty_primary=Decimal("-1"),
            unit_index=1,
            unit_cost=Decimal("1"),
            total_cost=Decimal("1"),
            movement_type=ProductMovement.MovementType.SALE,
            source_app="pos",
            source_model="SalesBill",
            source_id=str(bill.id),
            cost_currency_at_txn="SYP",
        )
        f1 = StockFifoLayer.objects.create(
            product=prod,
            container=self.container,
            qty_remaining=Decimal("0"),
            unit_cost=Decimal("1"),
            cost_currency="SYP",
            source_app="tests",
            source_model="Mixed",
            source_id="1",
        )
        f2 = StockFifoLayer.objects.create(
            product=prod,
            container=self.container,
            qty_remaining=Decimal("0"),
            unit_cost=Decimal("1"),
            cost_currency="USD",
            source_app="tests",
            source_model="Mixed",
            source_id="2",
        )
        SaleCostPart.objects.create(movement=mv, fifo_layer=f1, qty_primary=Decimal("0.500"), unit_cost=Decimal("1"), total_cost=Decimal("0.500"))
        SaleCostPart.objects.create(movement=mv, fifo_layer=f2, qty_primary=Decimal("0.500"), unit_cost=Decimal("1"), total_cost=Decimal("0.500"))

        ret = ReturnSV.create_sales_return_draft(
            actor=self.user,
            sale_bill_id=bill.id,
            stock_container_id=self.container.id,
            items=[{"sale_row_id": row.id, "qty": Decimal("1"), "reason": "mixed"}],
        )
        with self.assertRaisesRegex(ValueError, "Missing or mixed sale cost currencies"):
            ReturnSV.post_sales_return(
                actor=self.user,
                return_id=ret.id,
                settle_mode="credit",
            )

    def test_pos_row_seed_uses_usd_default_not_legacy_raw_cost(self):
        prod = self._mk_product("PosSeedUSD")
        prod.default_cost_syp = Decimal("999.0000")
        prod.default_cost_usd = Decimal("2.2500")
        prod.default_sale_currency = "USD"
        prod.allow_usd_sales = True
        prod.allow_usd_purchasing = True
        prod.save(update_fields=["default_cost_syp", "default_cost_usd", "default_sale_currency", "allow_usd_sales", "allow_usd_purchasing"])

        payload = {
            "id": None,
            "parked": True,
            "pay_status": SalesBill.PAY_FULL,
            "total_amount": "0",
            "paid_amount": "0",
            "settlement_mode": SalesBill.SETTLE_SPLIT,
            "money_container_id": self.money.id,
            "customer_name": "",
            "create_new_customer": False,
            "rows": [
                {
                    "product_id": prod.id,
                    "name": prod.name,
                    "number": str(prod.id),
                    "qty": "1",
                    "uom_index": 1,
                    "unit_price": "3.000",
                    "currency": "USD",
                    "disc_amount": "0",
                    "disc_pct": "0",
                    "notes": "",
                }
            ],
        }
        resp = self.client.post(reverse("pos:api_bill_save"), data=payload, content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        bill_id = resp.json()["bill"]["id"]
        row = SalesBillRow.objects.get(bill_id=bill_id, product_id=prod.id)
        self.assertEqual(row.unit_cost_at_txn, Decimal("2.2500"))
        self.assertEqual(row.cost_currency_at_txn, "USD")


class CurrencyStrictnessBillingAndCatalogTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="strict_mgr_cat", password="pass")
        AccountProfile.objects.create(user=self.user, role=AccountProfile.Role.MANAGER)
        self.client.force_login(self.user)
        FinSV.set_current_fx(actor=self.user, rate_syp_per_usd=Decimal("20000"))

        self.store = ProductContainer.objects.filter(code="store").first()
        if not self.store:
            self.store = ProductContainer.objects.create(name="Store", code="store", is_store=True, is_active=True)

        self.provider = Provider.objects.create(name="Strict Provider")

        syp, _ = Currency.objects.get_or_create(code="SYP", defaults={"name": "SYP", "is_active": True})
        usd, _ = Currency.objects.get_or_create(code="USD", defaults={"name": "USD", "is_active": True})
        self.cash = MoneyContainer.objects.create(
            name="Strict Cash",
            container_type=MoneyContainer.ContainerType.DRAWER,
            is_active=True,
            created_by=self.user,
        )
        feat, _ = ContainerFeature.objects.get_or_create(code="pos_sales", defaults={"name": "POS Sales", "is_active": True})
        self.cash.features.add(feat)
        MoneyContainerCurrency.objects.get_or_create(container=self.cash, currency=syp, defaults={"is_enabled": True})
        MoneyContainerCurrency.objects.get_or_create(container=self.cash, currency=usd, defaults={"is_enabled": True})

    def _mk_product(self, name: str) -> Product:
        col = ProductCollection.objects.create(name=f"{name}-C")
        st = ProductSet.objects.create(collection=col, name=f"{name}-S")
        return Product.objects.create(
            name=name,
            set=st,
            unit_primary=UnitType.PIECE,
            unit_secondary="",
            conversion_factor=None,
            allow_syp_purchasing=True,
            allow_usd_purchasing=True,
            allow_syp_sales=True,
            allow_usd_sales=True,
        )

    def test_purchase_bill_explicit_currency_creates_fifo_with_same_currency(self):
        p_syp = self._mk_product("BillCurSYP")
        p_usd = self._mk_product("BillCurUSD")
        bill = BillingSV.create_bill(
            actor=self.user,
            provider_id=self.provider.id,
            status="paid",
            paid_amount=Decimal("0"),
            items=[
                {"product_id": p_syp.id, "unit_index": 1, "qty_raw": "1", "cost": "1000", "currency": "SYP"},
                {"product_id": p_usd.id, "unit_index": 1, "qty_raw": "1", "cost": "2", "currency": "USD"},
            ],
            container=self.store,
            money_container_id=self.cash.id,
            settlement_currency="SYP",
            fx_usd_syp=Decimal("15000"),
            update_product_defaults=False,
        )
        item_by_product = {it.product_id: it for it in bill.items.all()}
        fifo_syp = StockFifoLayer.objects.filter(source_model="BillItem", source_id=str(item_by_product[p_syp.id].id)).first()
        fifo_usd = StockFifoLayer.objects.filter(source_model="BillItem", source_id=str(item_by_product[p_usd.id].id)).first()
        self.assertIsNotNone(fifo_syp)
        self.assertIsNotNone(fifo_usd)
        self.assertEqual(fifo_syp.cost_currency, "SYP")
        self.assertEqual(fifo_usd.cost_currency, "USD")

    def test_provider_return_preserves_currency_identity(self):
        prod = self._mk_product("RetUSD")
        bill = BillingSV.create_bill(
            actor=self.user,
            provider_id=self.provider.id,
            status="unpaid",
            paid_amount=Decimal("0"),
            items=[{"product_id": prod.id, "unit_index": 1, "qty_raw": "2", "cost": "2", "currency": "USD"}],
            container=self.store,
            money_container_id=self.cash.id,
            settlement_currency="USD",
            fx_usd_syp=Decimal("15000"),
            update_product_defaults=False,
        )
        item = bill.items.first()
        BillingSV.create_return(
            actor=self.user,
            provider_id=self.provider.id,
            status="unpaid",
            paid_amount=Decimal("0"),
            items=[
                {
                    "bill_item_id": item.id,
                    "product_id": prod.id,
                    "unit_index": 1,
                    "qty_primary": "1",
                    "container_splits": [{"code": "store", "qty_primary": "1"}],
                }
            ],
            container=None,
            source_bill_serial=bill.serial,
            currency_code="USD",
            valuation_mode="HISTORICAL",
        )
        mv = ProductMovement.objects.filter(source_app="billing", source_model="ProviderReturn", product=prod).first()
        self.assertIsNotNone(mv)
        self.assertEqual(mv.cost_currency_at_txn, "USD")

    def test_provider_return_current_fx_is_deterministic(self):
        prod = self._mk_product("RetFx")
        FinSV.set_current_fx(actor=self.user, rate_syp_per_usd=Decimal("20000"))
        bill = BillingSV.create_bill(
            actor=self.user,
            provider_id=self.provider.id,
            status="unpaid",
            paid_amount=Decimal("0"),
            items=[{"product_id": prod.id, "unit_index": 1, "qty_raw": "1", "cost": "2", "currency": "USD"}],
            container=self.store,
            money_container_id=self.cash.id,
            settlement_currency="SYP",
            fx_usd_syp=Decimal("15000"),
            update_product_defaults=False,
        )
        item = bill.items.first()
        pret = BillingSV.create_return(
            actor=self.user,
            provider_id=self.provider.id,
            status="unpaid",
            paid_amount=Decimal("0"),
            items=[
                {
                    "bill_item_id": item.id,
                    "product_id": prod.id,
                    "unit_index": 1,
                    "qty_primary": "1",
                    "container_splits": [{"code": "store", "qty_primary": "1"}],
                }
            ],
            container=None,
            source_bill_serial=bill.serial,
            currency_code="SYP",
            valuation_mode="CURRENT_FX",
        )
        self.assertEqual(pret.total, Decimal("40000.000"))

    def test_product_edit_updates_currency_defaults(self):
        col = ProductCollection.objects.create(name="EditCostCol")
        st = ProductSet.objects.create(collection=col, name="EditCostSet")
        p = Product.objects.create(
            name="EditCostProd",
            set=st,
            unit_primary=UnitType.PIECE,
            unit_secondary="",
            conversion_factor=None,
            allow_syp_sales=True,
            allow_syp_purchasing=True,
            allow_usd_sales=True,
            allow_usd_purchasing=True,
            default_purchase_currency="SYP",
            default_sale_currency="USD",
            default_cost_syp=Decimal("10.0000"),
            default_cost_usd=Decimal("1.0000"),
            default_price_syp=Decimal("20.0000"),
            default_price_usd=Decimal("2.0000"),
        )
        resp = self.client.post(
            reverse("manager_product_edit", kwargs={"pk": p.id}),
            data={
                "collection_name": col.name,
                "set_name": st.name,
                "create_parent": "",
                "name": p.name,
                "unit_primary": UnitType.PIECE,
                "unit_secondary": "",
                "allow_syp_sales": "on",
                "allow_syp_purchasing": "on",
                "allow_usd_sales": "on",
                "allow_usd_purchasing": "on",
                "default_purchase_currency": "USD",
                "default_sale_currency": "SYP",
                "default_cost_syp": "11.0000",
                "default_cost_usd": "3.0000",
                "default_price_syp": "21.0000",
                "default_price_usd": "4.0000",
                "notes": "edited",
            },
        )
        self.assertEqual(resp.status_code, 302)
        p.refresh_from_db()
        self.assertEqual(p.default_cost_syp, Decimal("11.0000"))
        self.assertEqual(p.default_cost_usd, Decimal("3.0000"))
        self.assertEqual(p.default_price_syp, Decimal("21.0000"))
        self.assertEqual(p.default_price_usd, Decimal("4.0000"))


