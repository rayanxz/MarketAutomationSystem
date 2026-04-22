from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from django.test import SimpleTestCase

from billing.models import Bill, BillItem, ProviderReturn, ProviderReturnItem
from billing.services import q4 as billing_q4
from catalog.forms import ProductCreateForm
from catalog.models import Product
from financials.forms import FxSettingsForm
from financials.models import FxSettings, PostingLine, Receipt
from financials import services as FinSV
from inventory.models import ProductMovement, SaleCostPart, q4 as inventory_q4
from pos.models import SalesBill, SalesBillRow, SalesReturnRow
from stock.models import StockEntry, StockFifoLayer


class MoneyPrecisionSafeScopeTests(SimpleTestCase):
    def test_cost_price_fields_are_two_decimals(self):
        expectations = {
            (Product, "cost_syp"): 2,
            (Product, "cost_usd"): 2,
            (Product, "price_syp"): 2,
            (Product, "price_usd"): 2,
            (Product, "default_cost_syp"): 2,
            (Product, "default_cost_usd"): 2,
            (Product, "default_price_syp"): 2,
            (Product, "default_price_usd"): 2,
            (Product, "latest_cost_syp"): 2,
            (Product, "latest_cost_usd"): 2,
            (Product, "latest_price_syp"): 2,
            (Product, "latest_price_usd"): 2,
            (BillItem, "cost"): 2,
            (BillItem, "price"): 2,
            (ProviderReturnItem, "cost"): 2,
            (ProductMovement, "unit_cost"): 2,
            (ProductMovement, "unit_cost_at_txn"): 2,
            (ProductMovement, "sale_unit_price_at_txn"): 2,
            (SaleCostPart, "unit_cost"): 2,
            (StockEntry, "avg_unit_cost"): 2,
            (StockFifoLayer, "unit_cost"): 2,
            (SalesBillRow, "unit_price"): 2,
            (SalesBillRow, "unit_cost_at_txn"): 2,
            (SalesReturnRow, "unit_price_at_sale"): 2,
            (SalesReturnRow, "unit_cost_at_txn"): 2,
        }
        for (model, field_name), decimals in expectations.items():
            self.assertEqual(model._meta.get_field(field_name).decimal_places, decimals)

    def test_fx_fields_are_two_decimals_in_scope(self):
        expectations = {
            (FxSettings, "rate_syp_per_usd"): 2,
            (Bill, "fx_rate_usd_to_syp_used"): 2,
            (BillItem, "fx_rate_at_txn"): 2,
            (ProviderReturn, "fx_rate_used"): 2,
            (ProviderReturnItem, "fx_rate_at_txn"): 2,
            (ProductMovement, "fx_rate_at_txn"): 2,
            (SalesBill, "fx_rate_used"): 2,
            (SalesBillRow, "fx_rate_at_txn"): 2,
            (SalesReturnRow, "fx_rate_at_txn"): 2,
        }
        for (model, field_name), decimals in expectations.items():
            self.assertEqual(model._meta.get_field(field_name).decimal_places, decimals)

    def test_rounding_helpers_use_two_decimals(self):
        self.assertEqual(inventory_q4(Decimal("12.344")), Decimal("12.34"))
        self.assertEqual(inventory_q4(Decimal("12.346")), Decimal("12.35"))
        self.assertEqual(billing_q4(Decimal("7.344")), Decimal("7.34"))
        self.assertEqual(billing_q4(Decimal("7.346")), Decimal("7.35"))
        self.assertEqual(FinSV.q_fx(Decimal("10000.234")), Decimal("10000.23"))
        self.assertEqual(FinSV.q_fx(Decimal("10000.235")), Decimal("10000.24"))

    def test_form_and_widget_precision_for_cost_price_fx(self):
        form = ProductCreateForm()
        money_fields = (
            "cost_syp",
            "cost_usd",
            "price_syp",
            "price_usd",
            "default_cost_syp",
            "default_cost_usd",
            "default_price_syp",
            "default_price_usd",
        )
        for name in money_fields:
            self.assertEqual(form.fields[name].decimal_places, 2)
            self.assertEqual(form.fields[name].widget.attrs.get("step"), "0.01")
            self.assertEqual(form.fields[name].widget.attrs.get("data-math-max-decimals"), "2")

        fx_form = FxSettingsForm()
        self.assertEqual(fx_form.fields["rate_syp_per_usd"].widget.attrs.get("step"), "0.01")
        self.assertEqual(fx_form.fields["rate_syp_per_usd"].widget.attrs.get("data-math-max-decimals"), "2")

    def test_ui_money_fx_scope_and_transactional_precision_unchanged(self):
        app_root = Path(__file__).resolve().parents[1]
        billing_js = (app_root / "static" / "js" / "billing_add_bill.js").read_text(encoding="utf-8")
        pos_js = (app_root / "static" / "pos.js").read_text(encoding="utf-8")
        product_tpl = (app_root / "templates" / "manager" / "product_new.html").read_text(encoding="utf-8")

        self.assertIn('name="cost[]"', billing_js)
        self.assertIn('step="0.01"', billing_js)
        self.assertIn('priceSypInput.value = fmt2', billing_js)
        self.assertIn('priceUsdInput.value = fmt2', billing_js)
        self.assertIn("function fmtPrice(n)", pos_js)
        self.assertIn("inqPriceEl.textContent    = `${fmtPrice(priceNum)}", pos_js)
        self.assertIn("tdPrice.textContent = fmtPrice(r.price);", pos_js)
        self.assertIn("function roundTo2(v)", product_tpl)

        # Guard rail: transactional/accounting precision remains untouched.
        self.assertEqual(Bill._meta.get_field("total").decimal_places, 3)
        self.assertEqual(Bill._meta.get_field("creation_paid_syp").decimal_places, 3)
        self.assertEqual(SalesBill._meta.get_field("total_amount").decimal_places, 3)
        self.assertEqual(PostingLine._meta.get_field("amount").decimal_places, 6)
        self.assertEqual(Receipt._meta.get_field("fx_syp_per_usd").decimal_places, 6)
