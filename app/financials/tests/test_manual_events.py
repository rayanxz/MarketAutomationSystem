from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import AccountProfile
from financials import services as FSV
from financials import manual_events as ManualSV
from financials.models import Currency, MoneyContainer, MoneyContainerCurrency, ReceiptKind


class ManualEventsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.actor = User.objects.create_user(username="mgr", password="pw")
        AccountProfile.objects.create(user=cls.actor, role=AccountProfile.Role.MANAGER)

        cls.syp, _ = Currency.objects.get_or_create(
            code="SYP",
            defaults={"name": "Syrian Pound", "decimals": 0, "is_active": True},
        )
        cls.usd, _ = Currency.objects.get_or_create(
            code="USD",
            defaults={"name": "US Dollar", "decimals": 2, "is_active": True},
        )
        FSV.set_current_fx(actor=cls.actor, rate_syp_per_usd=Decimal("15000"))

        cls.a = MoneyContainer.objects.create(
            name="A",
            container_type=MoneyContainer.ContainerType.DRAWER,
            is_active=True,
            created_by=cls.actor,
        )
        cls.b = MoneyContainer.objects.create(
            name="B",
            container_type=MoneyContainer.ContainerType.DRAWER,
            is_active=True,
            created_by=cls.actor,
        )
        cls.a.allowed_users.add(cls.actor)
        cls.b.allowed_users.add(cls.actor)

        MoneyContainerCurrency.objects.update_or_create(
            container=cls.a, currency=cls.syp, defaults={"is_enabled": True}
        )
        MoneyContainerCurrency.objects.update_or_create(
            container=cls.a, currency=cls.usd, defaults={"is_enabled": True}
        )
        MoneyContainerCurrency.objects.update_or_create(
            container=cls.b, currency=cls.syp, defaults={"is_enabled": True}
        )
        MoneyContainerCurrency.objects.update_or_create(
            container=cls.b, currency=cls.usd, defaults={"is_enabled": True}
        )

    def test_add_updates_balance(self):
        ManualSV.post_manual_add(
            actor=self.actor,
            container_id=self.a.id,
            currency_code="SYP",
            amount=Decimal("10000"),
        )
        self.a.refresh_from_db()
        self.assertEqual(self.a.balance_syp, Decimal("10000"))

    def test_withdraw_blocks_insufficient(self):
        with self.assertRaises(ValueError):
            ManualSV.post_manual_withdraw(
                actor=self.actor,
                container_id=self.a.id,
                currency_code="SYP",
                amount=Decimal("2000"),
            )

    def test_transfer_moves_balance(self):
        ManualSV.post_manual_add(
            actor=self.actor,
            container_id=self.a.id,
            currency_code="SYP",
            amount=Decimal("5000"),
        )
        receipt = ManualSV.post_manual_transfer(
            actor=self.actor,
            from_container_id=self.a.id,
            to_container_id=self.b.id,
            currency_code="SYP",
            amount=Decimal("1500"),
        )
        self.a.refresh_from_db()
        self.b.refresh_from_db()
        self.assertEqual(self.a.balance_syp, Decimal("3500"))
        self.assertEqual(self.b.balance_syp, Decimal("1500"))
        self.assertEqual(receipt.lines.count(), 2)

    def test_exchange_same_container_usd_to_syp(self):
        ManualSV.post_manual_add(
            actor=self.actor,
            container_id=self.a.id,
            currency_code="USD",
            amount=Decimal("100"),
        )
        receipt = ManualSV.post_manual_exchange(
            actor=self.actor,
            from_container_id=self.a.id,
            to_container_id=None,
            currency_from="USD",
            currency_to="SYP",
            amount_from=Decimal("10"),
            fx_syp_per_usd=Decimal("15000"),
        )
        self.a.refresh_from_db()
        self.assertEqual(self.a.balance_usd, Decimal("90"))
        self.assertEqual(self.a.balance_syp, Decimal("150000"))
        self.assertEqual(receipt.kind, ReceiptKind.EXCHANGE)
        self.assertEqual(receipt.lines.count(), 2)

    def test_exchange_between_containers_syp_to_usd(self):
        ManualSV.post_manual_add(
            actor=self.actor,
            container_id=self.a.id,
            currency_code="SYP",
            amount=Decimal("300000"),
        )
        receipt = ManualSV.post_manual_exchange(
            actor=self.actor,
            from_container_id=self.a.id,
            to_container_id=self.b.id,
            currency_from="SYP",
            currency_to="USD",
            amount_from=Decimal("150000"),
            fx_syp_per_usd=Decimal("15000"),
        )
        self.a.refresh_from_db()
        self.b.refresh_from_db()
        self.assertEqual(self.a.balance_syp, Decimal("150000"))
        self.assertEqual(self.b.balance_usd, Decimal("10"))
        self.assertEqual(receipt.kind, ReceiptKind.EXCHANGE)

    def test_currency_disabled_blocks(self):
        MoneyContainerCurrency.objects.update_or_create(
            container=self.a, currency=self.usd, defaults={"is_enabled": False}
        )
        with self.assertRaises(ValueError):
            ManualSV.post_manual_withdraw(
                actor=self.actor,
                container_id=self.a.id,
                currency_code="USD",
                amount=Decimal("1"),
            )

    def test_exchange_rejects_inactive_target_container(self):
        ManualSV.post_manual_add(
            actor=self.actor,
            container_id=self.a.id,
            currency_code="USD",
            amount=Decimal("2"),
        )
        self.b.is_active = False
        self.b.save(update_fields=["is_active"])

        with self.assertRaisesMessage(ValueError, "Container is inactive / disabled"):
            ManualSV.post_manual_exchange(
                actor=self.actor,
                from_container_id=self.a.id,
                to_container_id=self.b.id,
                currency_from="USD",
                currency_to="SYP",
                amount_from=Decimal("1"),
                fx_syp_per_usd=Decimal("15000"),
            )

    def test_exchange_rejects_more_than_2_decimal_amount(self):
        ManualSV.post_manual_add(
            actor=self.actor,
            container_id=self.a.id,
            currency_code="USD",
            amount=Decimal("2"),
        )
        with self.assertRaisesMessage(ValueError, "supports at most 2 decimal digits"):
            ManualSV.post_manual_exchange(
                actor=self.actor,
                from_container_id=self.a.id,
                to_container_id=self.a.id,
                currency_from="USD",
                currency_to="SYP",
                amount_from=Decimal("1.237"),
                fx_syp_per_usd=Decimal("15000"),
            )

    def test_withdraw_rejects_more_than_2_decimal_amount(self):
        ManualSV.post_manual_add(
            actor=self.actor,
            container_id=self.a.id,
            currency_code="SYP",
            amount=Decimal("100"),
        )
        with self.assertRaisesMessage(ValueError, "supports at most 2 decimal digits"):
            ManualSV.post_manual_withdraw(
                actor=self.actor,
                container_id=self.a.id,
                currency_code="SYP",
                amount=Decimal("1.237"),
            )

    def test_transfer_rejects_more_than_2_decimal_amount(self):
        ManualSV.post_manual_add(
            actor=self.actor,
            container_id=self.a.id,
            currency_code="SYP",
            amount=Decimal("100"),
        )
        with self.assertRaisesMessage(ValueError, "supports at most 2 decimal digits"):
            ManualSV.post_manual_transfer(
                actor=self.actor,
                from_container_id=self.a.id,
                to_container_id=self.b.id,
                currency_code="SYP",
                amount=Decimal("1.237"),
            )
