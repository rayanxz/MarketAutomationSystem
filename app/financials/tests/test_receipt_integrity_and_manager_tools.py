from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import AccountProfile
from financials import services as FinSV
from financials.models import Counterparty, CounterpartyType, Currency, Receipt


class FinancialsReceiptIntegrityAndManagerToolsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.manager = User.objects.create_user(username="fin_mgr_tools", password="123")
        prof, _ = AccountProfile.objects.get_or_create(user=cls.manager)
        prof.role = AccountProfile.Role.MANAGER
        prof.save(update_fields=["role"])

        Currency.objects.get_or_create(
            code="SYP",
            defaults={"name": "Syrian Pound", "decimals": 0, "is_active": True},
        )
        Currency.objects.get_or_create(
            code="USD",
            defaults={"name": "US Dollar", "decimals": 2, "is_active": True},
        )

        FinSV.set_current_fx(actor=cls.manager, rate_syp_per_usd=Decimal("15000"))

        cls.counterparty, _ = Counterparty.objects.get_or_create(
            type=CounterpartyType.SYSTEM,
            name="Manager Tools Counterparty",
            defaults={"is_active": True},
        )

        cls.action_key = "tests:Doc:1:create"
        cls.receipt = FinSV.post_counterparty_bill_action_with_fx(
            actor=cls.manager,
            counterparty_id=cls.counterparty.id,
            totals_by_code={"SYP": Decimal("100")},
            settled_counterparty_by_code={"SYP": Decimal("40")},
            container_paid_by_code={},
            container_id=None,
            fx_syp_per_usd=Decimal("15000"),
            action_key=cls.action_key,
            note="test canonical receipt",
            source_app="tests",
            source_model="Doc",
            source_id="1",
        )

    def setUp(self):
        ok = self.client.login(username="fin_mgr_tools", password="123")
        self.assertTrue(ok)

    def test_canonical_action_key_is_idempotent(self):
        receipt_2 = FinSV.post_counterparty_bill_action_with_fx(
            actor=self.manager,
            counterparty_id=self.counterparty.id,
            totals_by_code={"SYP": Decimal("100")},
            settled_counterparty_by_code={"SYP": Decimal("40")},
            container_paid_by_code={},
            container_id=None,
            fx_syp_per_usd=Decimal("15000"),
            action_key=self.action_key,
            note="duplicate call",
            source_app="tests",
            source_model="Doc",
            source_id="1",
        )
        self.assertEqual(receipt_2.id, self.receipt.id)
        self.assertEqual(Receipt.objects.filter(action_key=self.action_key).count(), 1)

    def test_receipt_explorer_renders_and_filters_by_action_key(self):
        url = reverse("financials:receipt_explorer")
        resp_all = self.client.get(url)
        self.assertEqual(resp_all.status_code, 200)
        self.assertContains(resp_all, self.receipt.serial)

        resp_filtered = self.client.get(url, {"action_key": self.action_key})
        self.assertEqual(resp_filtered.status_code, 200)
        self.assertContains(resp_filtered, self.receipt.serial)

    def test_document_trace_renders_for_source_document(self):
        url = reverse("financials:document_trace")
        resp = self.client.get(
            url,
            {
                "source_app": "tests",
                "source_model": "Doc",
                "source_id": "1",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, self.receipt.serial)

    def test_reconciliation_dashboard_renders(self):
        resp = self.client.get(reverse("financials:reconciliation_dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "لوحة المطابقة")
