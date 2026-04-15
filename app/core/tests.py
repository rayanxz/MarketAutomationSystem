from decimal import Decimal
from datetime import date

from django.test import TestCase

from core.date_filters import parse_filter_date
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


class DateFilterParsingTests(TestCase):
    def test_parses_dd_mm_yyyy_as_day_first(self):
        self.assertEqual(parse_filter_date("05/04/2026"), date(2026, 4, 5))
        self.assertEqual(parse_filter_date("5/4/2026"), date(2026, 4, 5))

    def test_accepts_iso_for_backward_compatibility(self):
        self.assertEqual(parse_filter_date("2026-04-05"), date(2026, 4, 5))
        self.assertEqual(parse_filter_date("2026/04/05"), date(2026, 4, 5))

    def test_invalid_dates_return_none(self):
        self.assertIsNone(parse_filter_date("31/02/2026"))
        self.assertIsNone(parse_filter_date("not-a-date"))
