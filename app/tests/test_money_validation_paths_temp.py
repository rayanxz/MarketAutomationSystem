from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from billing.services import _parse_money_value as parse_billing_money
from billing.views import _parse_money_input as parse_billing_view_money
from debts.views import _parse_money_input as parse_debts_view_money
from pos.api_bills import _parse_money_decimal as parse_pos_money
from accounts.models import AccountProfile


class MoneyValidationPathsTempTests(SimpleTestCase):
    def test_billing_service_rejects_more_than_2_decimals(self):
        for value in ("1.234", "1.239", "0.001"):
            with self.assertRaises(ValidationError):
                parse_billing_money(raw=value, field_name="amount")

    def test_billing_view_rejects_more_than_2_decimals(self):
        for value in ("1.234", "1.239", "0.001"):
            with self.assertRaises(ValueError):
                parse_billing_view_money(value, field_name="amount")

    def test_debts_view_rejects_more_than_2_decimals(self):
        for value in ("1.234", "1.239", "0.001"):
            with self.assertRaises(ValueError):
                parse_debts_view_money(value, field_name="amount")

    def test_pos_api_rejects_more_than_2_decimals(self):
        for value in ("1.234", "1.239", "0.001"):
            with self.assertRaises(ValueError):
                parse_pos_money(value, field_name="amount")

    def test_paths_accept_valid_2dp_money(self):
        self.assertEqual(parse_billing_money(raw="1.23", field_name="amount"), Decimal("1.23"))
        self.assertEqual(parse_billing_view_money("1.23", field_name="amount"), Decimal("1.23"))
        self.assertEqual(parse_debts_view_money("1.23", field_name="amount"), Decimal("1.23"))
        self.assertEqual(parse_pos_money("1.23", field_name="amount"), Decimal("1.23"))


class FinancialsManualEventValidationTempTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.user = User.objects.create_user(username="mgr_temp_validation", password="pw")
        profile, _ = AccountProfile.objects.get_or_create(user=cls.user)
        profile.role = AccountProfile.Role.MANAGER
        profile.save(update_fields=["role"])

    def setUp(self):
        self.assertTrue(self.client.login(username="mgr_temp_validation", password="pw"))

    def test_manual_event_rejects_more_than_2_decimals(self):
        for value in ("1.234", "1.239", "0.001"):
            resp = self.client.post(
                reverse("financials:manual_event"),
                data={"action": "add", "amount": value},
            )
            self.assertEqual(resp.status_code, 400)
            self.assertEqual(resp.json().get("error"), "AMOUNT_MAX_2_DECIMALS")
