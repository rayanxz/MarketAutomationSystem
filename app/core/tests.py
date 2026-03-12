from decimal import Decimal

from django.test import TestCase

from core.templatetags.formatting import human_number


class HumanNumberFilterTests(TestCase):
    def test_trims_trailing_zeros_and_adds_grouping(self):
        self.assertEqual(human_number(Decimal("5000.0000")), "5,000")
        self.assertEqual(human_number(Decimal("25.500000")), "25.5")
        self.assertEqual(human_number(Decimal("12000.5000")), "12,000.5")

    def test_handles_negative_and_zero_values(self):
        self.assertEqual(human_number(Decimal("-1000.0000")), "-1,000")
        self.assertEqual(human_number(Decimal("-0.0000")), "0")
        self.assertEqual(human_number(Decimal("0.0000")), "0")

    def test_returns_non_numeric_values_unchanged(self):
        self.assertEqual(human_number("ABC-001"), "ABC-001")
