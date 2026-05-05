from __future__ import annotations

from decimal import Decimal

from django.test import SimpleTestCase

from core.formatters import round_money


class RoundMoneyPhase1TempTests(SimpleTestCase):
    def test_basic_rounding(self):
        self.assertEqual(round_money(Decimal("1.234")), Decimal("1.23"))
        self.assertEqual(round_money(Decimal("1.235")), Decimal("1.24"))
        self.assertEqual(round_money(Decimal("1.236")), Decimal("1.24"))

    def test_edge_values(self):
        self.assertEqual(round_money(Decimal("0.005")), Decimal("0.01"))
        self.assertEqual(round_money(Decimal("0.004")), Decimal("0.00"))
        self.assertEqual(round_money(Decimal("999999999.999")), Decimal("1000000000.00"))

    def test_negative_values(self):
        self.assertEqual(round_money(Decimal("-1.235")), Decimal("-1.24"))

    def test_string_and_float_input(self):
        self.assertEqual(round_money("1.235"), Decimal("1.24"))
        self.assertEqual(round_money(1.235), Decimal("1.24"))

    def test_invalid_input_normalizes_to_zero(self):
        self.assertEqual(round_money(None), Decimal("0.00"))
        self.assertEqual(round_money(""), Decimal("0.00"))
        self.assertEqual(round_money("abc"), Decimal("0.00"))
