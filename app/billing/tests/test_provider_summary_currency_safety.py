from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import AccountProfile
from billing.models import Provider
from debts.models import DebtorDebt, PartyType


class ProviderSummaryCurrencySafetyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.manager = User.objects.create_user(username="prov_summary_mgr", password="pw12345")
        AccountProfile.objects.create(user=cls.manager, role=AccountProfile.Role.MANAGER)

    def setUp(self):
        ok = self.client.login(username="prov_summary_mgr", password="pw12345")
        self.assertTrue(ok)

    def test_provider_summary_returns_currency_separated_totals(self):
        provider = Provider.objects.create(name="Provider Mixed Debt")
        DebtorDebt.objects.create(
            provider=provider,
            source_app="billing",
            source_model="Bill",
            source_id="1001",
            total=Decimal("1200.00"),
            paid_amount=Decimal("200.00"),
            status=DebtorDebt.Status.OPEN,
            party_type=PartyType.PROVIDER,
            party_name=provider.name,
            currency_code="SYP",
        )
        DebtorDebt.objects.create(
            provider=provider,
            source_app="billing",
            source_model="Bill",
            source_id="1002",
            total=Decimal("9.00"),
            paid_amount=Decimal("1.00"),
            status=DebtorDebt.Status.OPEN,
            party_type=PartyType.PROVIDER,
            party_name=provider.name,
            currency_code="USD",
        )

        resp = self.client.get(reverse("billing_api_providers_list"), {"include_all": "1"})
        self.assertEqual(resp.status_code, 200, resp.content.decode("utf-8"))
        body = resp.json()
        self.assertTrue(body.get("ok"))

        item = next(p for p in body["items"] if p["id"] == provider.id)
        self.assertEqual(Decimal(item["debt_totals"]["SYP"]), Decimal("1000.00"))
        self.assertEqual(Decimal(item["debt_totals"]["USD"]), Decimal("8.00"))
        # Backward-compatibility field now mirrors only SYP and is no longer a mixed-currency aggregate.
        self.assertEqual(Decimal(item["total_debt"]), Decimal("1000.00"))
