from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase

from accounts.models import AccountProfile
from billing.models import Provider
from debts import services as DebtSV
from debts.models import DebtorPayment, PartyType
from financials import services as FinSV
from financials.models import (
    Counterparty,
    CounterpartyType,
    Currency,
    MoneyContainer,
    MoneyContainerCurrency,
    PostingLine,
    PostingTargetType,
    Receipt,
    ReceiptStatus,
)
from pos.models import CustomerProfile
from pos import services as POSSV


class CounterpartyIdentityRuntimeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.actor = User.objects.create_user(username="cp_runtime_mgr", password="pw123456", is_staff=True)

        profile, _ = AccountProfile.objects.get_or_create(user=cls.actor)
        profile.role = AccountProfile.Role.MANAGER
        profile.save(update_fields=["role"])

        cls.syp, _ = Currency.objects.get_or_create(
            code="SYP",
            defaults={"name": "Syrian Pound", "decimals": 0, "is_active": True},
        )
        cls.usd, _ = Currency.objects.get_or_create(
            code="USD",
            defaults={"name": "US Dollar", "decimals": 2, "is_active": True},
        )
        FinSV.set_current_fx(actor=cls.actor, rate_syp_per_usd=Decimal("15000"))

        cls.container = MoneyContainer.objects.create(
            name="Counterparty Identity Drawer",
            container_type=MoneyContainer.ContainerType.DRAWER,
            is_active=True,
            created_by=cls.actor,
        )
        cls.container.allowed_users.add(cls.actor)
        MoneyContainerCurrency.objects.get_or_create(
            container=cls.container,
            currency=cls.syp,
            defaults={"is_enabled": True},
        )
        MoneyContainerCurrency.objects.get_or_create(
            container=cls.container,
            currency=cls.usd,
            defaults={"is_enabled": True},
        )

    def test_duplicate_name_providers_have_distinct_counterparties_and_settlement_lines(self):
        # Provider names can repeat historically (inactive + active) and must stay identity-safe.
        old_provider = Provider.objects.create(name="Same Provider Name")
        old_provider.is_active = False
        old_provider.save(update_fields=["is_active"])
        new_provider = Provider.objects.create(name="Same Provider Name")

        old_entry = DebtSV.create_manual_debt(
            actor=self.actor,
            direction="debtor",
            party_type=PartyType.PROVIDER,
            provider_id=old_provider.id,
            party_name=old_provider.name,
            amount=Decimal("20"),
            currency_code="SYP",
            initial_payment=Decimal("5"),
            money_container_id=self.container.id,
        )
        new_entry = DebtSV.create_manual_debt(
            actor=self.actor,
            direction="debtor",
            party_type=PartyType.PROVIDER,
            provider_id=new_provider.id,
            party_name=new_provider.name,
            amount=Decimal("30"),
            currency_code="SYP",
            initial_payment=Decimal("7"),
            money_container_id=self.container.id,
        )

        cp_old = Counterparty.objects.get(type=CounterpartyType.PROVIDER, provider_id=old_provider.id)
        cp_new = Counterparty.objects.get(type=CounterpartyType.PROVIDER, provider_id=new_provider.id)
        self.assertNotEqual(cp_old.id, cp_new.id)

        pay_old = DebtorPayment.objects.get(entry_id=old_entry.id)
        pay_new = DebtorPayment.objects.get(entry_id=new_entry.id)
        self.assertNotEqual(pay_old.receipt_id, pay_new.receipt_id)

        lines_old = PostingLine.objects.filter(
            receipt_id=pay_old.receipt_id,
            target_type=PostingTargetType.COUNTERPARTY,
        )
        lines_new = PostingLine.objects.filter(
            receipt_id=pay_new.receipt_id,
            target_type=PostingTargetType.COUNTERPARTY,
        )
        self.assertEqual(lines_old.count(), 1)
        self.assertEqual(lines_new.count(), 1)
        self.assertEqual(lines_old.first().counterparty_id, cp_old.id)
        self.assertEqual(lines_new.first().counterparty_id, cp_new.id)

        FinSV.reverse_receipt(actor=self.actor, receipt_id=pay_old.receipt_id, reason_note="identity regression")
        reversal = (
            Receipt.objects.filter(
                reverses_id=pay_old.receipt_id,
                status=ReceiptStatus.POSTED,
            )
            .order_by("-id")
            .first()
        )
        self.assertIsNotNone(reversal)
        reversal_cp_line = PostingLine.objects.filter(
            receipt_id=reversal.id,
            target_type=PostingTargetType.COUNTERPARTY,
        ).first()
        self.assertIsNotNone(reversal_cp_line)
        self.assertEqual(reversal_cp_line.counterparty_id, cp_old.id)

    def test_duplicate_name_customers_have_distinct_counterparties(self):
        customer_a = CustomerProfile.objects.create(name="Walk-in Customer")
        customer_b = CustomerProfile.objects.create(name="Walk-in Customer")

        cp_a = POSSV._ensure_customer_counterparty(customer=customer_a)
        cp_b = POSSV._ensure_customer_counterparty(customer=customer_b)

        self.assertNotEqual(cp_a.id, cp_b.id)
        self.assertEqual(cp_a.customer_id, customer_a.id)
        self.assertEqual(cp_b.customer_id, customer_b.id)


class CounterpartyIdentityMigrationTests(TransactionTestCase):
    reset_sequences = True

    migrate_from = ("financials", "0014_alter_receipt_kind")
    migrate_to = ("financials", "0015_counterparty_identity_constraints")

    def setUp(self):
        self.executor = MigrationExecutor(connection)
        self.executor.migrate([self.migrate_from])
        self.old_apps = self.executor.loader.project_state([self.migrate_from]).apps

    def tearDown(self):
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.executor.loader.graph.leaf_nodes())

    def test_migration_deduplicates_provider_counterparties_and_repoints_posting_lines(self):
        User = self.old_apps.get_model("auth", "User")
        Provider = self.old_apps.get_model("billing", "Provider")
        Currency = self.old_apps.get_model("financials", "Currency")
        Counterparty = self.old_apps.get_model("financials", "Counterparty")
        Receipt = self.old_apps.get_model("financials", "Receipt")
        PostingLine = self.old_apps.get_model("financials", "PostingLine")

        actor = User.objects.create_user(username="cp_mig_user")
        provider = Provider.objects.create(name="Migration Provider")
        syp, _ = Currency.objects.get_or_create(
            code="SYP",
            defaults={"name": "SYP", "decimals": 0, "is_active": True},
        )

        keep = Counterparty.objects.create(
            type="provider",
            name="Provider Name v1",
            provider_id=provider.id,
            is_active=True,
        )
        duplicate = Counterparty.objects.create(
            type="provider",
            name="Provider Name v2",
            provider_id=provider.id,
            is_active=True,
        )

        receipt = Receipt.objects.create(
            kind="counterparty_inc",
            status="posted",
            actor_id=actor.id,
            source_app="tests",
            source_model="migration",
            source_id="1",
        )
        line = PostingLine.objects.create(
            receipt_id=receipt.id,
            target_type="counterparty",
            counterparty_id=duplicate.id,
            currency_id=syp.id,
            amount=Decimal("5"),
        )

        self.executor = MigrationExecutor(connection)
        self.executor.migrate([self.migrate_to])
        new_apps = self.executor.loader.project_state([self.migrate_to]).apps

        NewCounterparty = new_apps.get_model("financials", "Counterparty")
        NewPostingLine = new_apps.get_model("financials", "PostingLine")

        rows = list(NewCounterparty.objects.filter(type="provider", provider_id=provider.id).order_by("id"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].id, keep.id)

        moved_line = NewPostingLine.objects.get(pk=line.id)
        self.assertEqual(moved_line.counterparty_id, keep.id)
