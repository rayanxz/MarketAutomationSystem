from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from django.test import SimpleTestCase

from billing.models import Bill, BillItem, ProviderReturn, ProviderReturnItem
from billing.services import q2 as billing_q2
from inventory.models import ProductMovement, SaleCostPart, q2 as inventory_q2
from pos.models import SalesBill, SalesBillRow, SalesReturn, SalesReturnRow


class TotalsPrecisionConsistencyTests(SimpleTestCase):
    def test_derived_monetary_fields_use_two_decimals(self):
        expectations = {
            (Bill, "total"): 2,
            (Bill, "subtotal_syp"): 2,
            (Bill, "subtotal_usd"): 2,
            (Bill, "total_syp"): 2,
            (Bill, "total_usd"): 2,
            (Bill, "grand_total_syp"): 2,
            (Bill, "grand_total_usd"): 2,
            (BillItem, "line_total"): 2,
            (ProviderReturn, "total"): 2,
            (ProviderReturn, "total_syp"): 2,
            (ProviderReturn, "total_usd"): 2,
            (ProviderReturnItem, "line_total"): 2,
            (SalesBill, "total_amount"): 2,
            (SalesBill, "total_syp"): 2,
            (SalesBill, "total_usd"): 2,
            (SalesBillRow, "disc_amount"): 2,
            (SalesReturn, "total_syp"): 2,
            (SalesReturn, "total_usd"): 2,
            (SalesReturnRow, "discount_amount_at_txn"): 2,
            (SalesReturnRow, "line_total"): 2,
            (ProductMovement, "total_cost"): 2,
            (ProductMovement, "discount_amount_at_txn"): 2,
            (SaleCostPart, "total_cost"): 2,
        }
        for (model, field_name), decimals in expectations.items():
            self.assertEqual(model._meta.get_field(field_name).decimal_places, decimals)

    def test_line_total_correctness_round_half_up(self):
        unit_price = Decimal("19.99")
        qty_primary = Decimal("3.333")
        discount = Decimal("0.555")

        base = billing_q2(unit_price * qty_primary)
        line_total = billing_q2(base - discount)

        expected = (unit_price * qty_primary).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        expected = (expected - discount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        self.assertEqual(line_total, expected)

    def test_subtotal_aggregation_correctness(self):
        line_totals = [Decimal("12.345"), Decimal("0.335"), Decimal("99.995")]
        subtotal = Decimal("0")
        for line in line_totals:
            subtotal = billing_q2(subtotal + billing_q2(line))
        self.assertEqual(subtotal, Decimal("112.69"))

    def test_grand_total_fx_rounding_correctness(self):
        total_syp = Decimal("100.00")
        total_usd = billing_q2(Decimal("1.235"))
        fx_syp_per_usd = Decimal("15000.56")

        grand_total_syp = billing_q2(total_syp + (total_usd * fx_syp_per_usd))
        expected = Decimal("18700.69")
        self.assertEqual(grand_total_syp, expected)

    def test_repeated_operations_no_drift(self):
        running = Decimal("0")
        step = Decimal("0.01")
        for _ in range(1000):
            running = inventory_q2(running + step)
        self.assertEqual(running, Decimal("10.00"))

    def test_full_payment_equality_has_no_ghost_remainder(self):
        total = billing_q2(Decimal("150.005"))
        paid = billing_q2(Decimal("150.006"))
        remaining = billing_q2(total - paid)

        self.assertEqual(total, Decimal("150.01"))
        self.assertEqual(paid, Decimal("150.01"))
        self.assertEqual(remaining, Decimal("0.00"))
        self.assertTrue(remaining == Decimal("0.00"))

    def test_ui_totals_formatters_force_two_decimals(self):
        app_root = Path(__file__).resolve().parents[1]
        pos_js = (app_root / "static" / "pos.js").read_text(encoding="utf-8")
        billing_js = (app_root / "static" / "js" / "billing_add_bill.js").read_text(encoding="utf-8")
        manager_overview = (app_root / "templates" / "pos" / "manager_overview.html").read_text(encoding="utf-8")
        return_wizard = (app_root / "templates" / "billing" / "bill_return_wizard.html").read_text(encoding="utf-8")

        self.assertIn("function fmtMoney(n)", pos_js)
        self.assertIn("fmtMoney(settlement.total)", pos_js)
        self.assertIn("fmtMoney(totals.syp)", pos_js)
        self.assertIn("fmtMoney(totals.usd)", pos_js)
        self.assertIn("const _moneyToCents", billing_js)
        self.assertIn("toFixed(2)", billing_js)
        self.assertIn("function round2(value)", manager_overview)
        self.assertIn("const SCALE_MONEY = 2", return_wizard)
        self.assertIn("function divRoundHalfUpSigned", return_wizard)
        self.assertIn("function parseScaledStrict", return_wizard)
