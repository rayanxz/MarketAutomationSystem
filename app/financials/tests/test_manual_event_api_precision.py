from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import AccountProfile
from financials import manual_events as ManualSV
from financials import services as FinSV
from financials.models import Currency, MoneyContainer, MoneyContainerCurrency, Receipt


class ManualEventApiPrecisionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.actor = User.objects.create_user(username="mgr_fx", password="pw")
        prof, _ = AccountProfile.objects.get_or_create(user=cls.actor)
        prof.role = AccountProfile.Role.MANAGER
        prof.save(update_fields=["role"])

        cls.syp, _ = Currency.objects.get_or_create(
            code="SYP",
            defaults={"name": "Syrian Pound", "decimals": 0, "is_active": True},
        )
        cls.usd, _ = Currency.objects.get_or_create(
            code="USD",
            defaults={"name": "US Dollar", "decimals": 2, "is_active": True},
        )
        FinSV.set_current_fx(actor=cls.actor, rate_syp_per_usd=Decimal("15000"))

        cls.a = MoneyContainer.objects.create(
            name="API FX A",
            container_type=MoneyContainer.ContainerType.DRAWER,
            is_active=True,
            created_by=cls.actor,
        )
        cls.a.allowed_users.add(cls.actor)
        MoneyContainerCurrency.objects.update_or_create(
            container=cls.a,
            currency=cls.syp,
            defaults={"is_enabled": True},
        )
        MoneyContainerCurrency.objects.update_or_create(
            container=cls.a,
            currency=cls.usd,
            defaults={"is_enabled": True},
        )

    def setUp(self):
        ok = self.client.login(username="mgr_fx", password="pw")
        self.assertTrue(ok)

    def test_manual_exchange_uses_explicit_fx_rate(self):
        ManualSV.post_manual_add(
            actor=self.actor,
            container_id=self.a.id,
            currency_code="USD",
            amount=Decimal("10"),
        )

        resp = self.client.post(
            reverse("financials:manual_event"),
            data={
                "action": "exchange",
                "from_container": str(self.a.id),
                "to_container": str(self.a.id),
                "currency_from": "USD",
                "currency_to": "SYP",
                "amount": "1",
                "fx_rate": "20000",
            },
        )
        self.assertEqual(resp.status_code, 200, resp.content.decode("utf-8"))
        body = resp.json()
        self.assertTrue(body.get("ok"), body)

        self.a.refresh_from_db()
        self.assertEqual(self.a.balance_usd, Decimal("9"))
        self.assertEqual(self.a.balance_syp, Decimal("20000"))

        receipt = Receipt.objects.get(pk=body["receipt_id"])
        self.assertEqual(receipt.fx_syp_per_usd, Decimal("20000.000000"))

    def test_manual_exchange_rejects_invalid_explicit_fx(self):
        ManualSV.post_manual_add(
            actor=self.actor,
            container_id=self.a.id,
            currency_code="USD",
            amount=Decimal("2"),
        )
        self.a.refresh_from_db()
        before_usd = self.a.balance_usd
        before_syp = self.a.balance_syp

        resp = self.client.post(
            reverse("financials:manual_event"),
            data={
                "action": "exchange",
                "from_container": str(self.a.id),
                "to_container": str(self.a.id),
                "currency_from": "USD",
                "currency_to": "SYP",
                "amount": "1",
                "fx_rate": "bad",
            },
        )
        self.assertEqual(resp.status_code, 400, resp.content.decode("utf-8"))
        self.assertEqual(resp.json().get("error"), "INVALID_FX_RATE")

        self.a.refresh_from_db()
        self.assertEqual(self.a.balance_usd, before_usd)
        self.assertEqual(self.a.balance_syp, before_syp)

    def test_manual_exchange_rejects_inactive_container(self):
        ManualSV.post_manual_add(
            actor=self.actor,
            container_id=self.a.id,
            currency_code="USD",
            amount=Decimal("2"),
        )
        self.a.is_active = False
        self.a.save(update_fields=["is_active"])

        resp = self.client.post(
            reverse("financials:manual_event"),
            data={
                "action": "exchange",
                "from_container": str(self.a.id),
                "to_container": str(self.a.id),
                "currency_from": "USD",
                "currency_to": "SYP",
                "amount": "1",
                "fx_rate": "15000",
            },
        )
        self.assertEqual(resp.status_code, 400, resp.content.decode("utf-8"))
        self.assertIn("inactive", (resp.json().get("error") or "").lower())
