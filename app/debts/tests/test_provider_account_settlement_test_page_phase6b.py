from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from accounts.models import AccountProfile
from billing.models import Provider
from debts.models import (
    DebtCauseType,
    DebtDirection,
    DebtRecord,
    DebtSettlement,
    DebtStatus,
    OtherPartyType,
    ProviderSettlementAction,
    ProviderSettlementAllocation,
)
from financials.models import MoneyContainer, Receipt


class ProviderAccountSettlementTestPagePhase6BTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.manager = user_model.objects.create_user(
            username="phase6b_test_page_mgr",
            password="123456",
        )
        AccountProfile.objects.create(user=cls.manager, role=AccountProfile.Role.MANAGER)

        cls.cashier = user_model.objects.create_user(
            username="phase6b_test_page_cashier",
            password="123456",
        )
        AccountProfile.objects.create(user=cls.cashier, role=AccountProfile.Role.CASHIER)

    def setUp(self):
        self.provider = Provider.objects.create(name=f"Phase6B Provider {self._testMethodName}")
        self.container = MoneyContainer.objects.create(
            name=f"Phase6B Drawer {self._testMethodName}",
            container_type=MoneyContainer.ContainerType.DRAWER,
            is_active=True,
            created_by=self.manager,
        )
        self.container.allowed_users.add(self.manager)

    def _path(self, provider_ref: str) -> str:
        return f"/manager/debts/provider/{provider_ref}/account-settlement-test/"

    def test_page_is_disabled_by_default_without_override(self):
        self.client.force_login(self.manager)
        response = self.client.get(self._path(str(self.provider.id)))
        self.assertEqual(response.status_code, 403)
        self.assertContains(
            response,
            "provider account settlement execution is disabled",
            status_code=403,
        )

    @override_settings(ENABLE_PROVIDER_ACCOUNT_SETTLEMENT_EXECUTION=False)
    def test_page_disabled_when_feature_flag_off(self):
        self.client.force_login(self.manager)
        resp = self.client.get(self._path(str(self.provider.id)))
        self.assertEqual(resp.status_code, 403)
        self.assertContains(resp, "provider account settlement execution is disabled", status_code=403)

    @override_settings(ENABLE_PROVIDER_ACCOUNT_SETTLEMENT_EXECUTION=True)
    def test_manager_only_access(self):
        resp = self.client.get(self._path(str(self.provider.id)))
        self.assertEqual(resp.status_code, 302)

        self.client.force_login(self.cashier)
        resp = self.client.get(self._path(str(self.provider.id)))
        self.assertEqual(resp.status_code, 403)

        self.client.force_login(self.manager)
        resp = self.client.get(self._path(str(self.provider.id)))
        self.assertEqual(resp.status_code, 200)

    @override_settings(ENABLE_PROVIDER_ACCOUNT_SETTLEMENT_EXECUTION=True)
    def test_page_renders_when_enabled(self):
        self.client.force_login(self.manager)
        resp = self.client.get(self._path(str(self.provider.id)))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "INTERNAL TEST TOOL")
        self.assertContains(resp, "Provider Account Explorer")
        self.assertContains(resp, self.provider.name)
        self.assertContains(resp, "Preview allocation")
        self.assertContains(resp, "Execute settlement")

    @override_settings(ENABLE_PROVIDER_ACCOUNT_SETTLEMENT_EXECUTION=True)
    def test_page_includes_expected_api_urls_and_data_attributes(self):
        self.client.force_login(self.manager)
        resp = self.client.get(self._path(str(self.provider.id)))
        self.assertEqual(resp.status_code, 200)

        self.assertContains(resp, 'data-provider-ac-url="/manager/billing/api/providers/ac/"')
        self.assertContains(resp, 'data-open-obligations-url-template="/manager/debts/api/provider/0/open-obligations/"')
        self.assertContains(resp, 'data-net-position-url-template="/manager/debts/api/provider/0/net-position/"')
        self.assertContains(resp, 'data-allocation-preview-url-template="/manager/debts/api/provider/0/account-allocation-preview/"')
        self.assertContains(resp, 'data-settlement-execute-url-template="/manager/debts/api/provider/0/account-settlement-execute/"')
        self.assertContains(resp, f'data-initial-provider-id="{self.provider.id}"')
        self.assertContains(resp, f'data-initial-provider-name="{self.provider.name}"')

    @override_settings(ENABLE_PROVIDER_ACCOUNT_SETTLEMENT_EXECUTION=True)
    def test_does_not_expose_provider_list_execution_page(self):
        self.client.force_login(self.manager)
        resp = self.client.get("/manager/debts/providers/account-settlement-test/")
        self.assertEqual(resp.status_code, 404)

    @override_settings(ENABLE_PROVIDER_ACCOUNT_SETTLEMENT_EXECUTION=True)
    def test_no_execution_happens_on_get(self):
        self.client.force_login(self.manager)
        debt = DebtRecord.objects.create(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.MANUAL,
            cause_id="phase6b-no-get-exec",
            source_app="debts",
            other_party_type=OtherPartyType.PROVIDER,
            other_party_id=str(self.provider.id),
            provider=self.provider,
            actor_username="phase6b",
            total_syp=Decimal("1000.00"),
            total_usd=Decimal("0.00"),
            remaining_syp=Decimal("1000.00"),
            remaining_usd=Decimal("0.00"),
            status=DebtStatus.OPEN,
        )
        before_counts = (
            ProviderSettlementAction.objects.count(),
            ProviderSettlementAllocation.objects.count(),
            Receipt.objects.count(),
            DebtSettlement.objects.count(),
        )
        resp = self.client.get(self._path(str(self.provider.id)))
        self.assertEqual(resp.status_code, 200)
        debt.refresh_from_db()
        self.assertEqual(debt.remaining_syp, Decimal("1000.00"))
        self.assertEqual(debt.status, DebtStatus.OPEN)
        after_counts = (
            ProviderSettlementAction.objects.count(),
            ProviderSettlementAllocation.objects.count(),
            Receipt.objects.count(),
            DebtSettlement.objects.count(),
        )
        self.assertEqual(before_counts, after_counts)
