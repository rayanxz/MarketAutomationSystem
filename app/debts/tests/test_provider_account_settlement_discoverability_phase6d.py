from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from accounts.models import AccountProfile
from billing.models import Provider


class ProviderAccountSettlementDiscoverabilityPhase6DTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.manager = user_model.objects.create_user(
            username="phase6d_discovery_mgr",
            password="123456",
        )
        AccountProfile.objects.create(user=cls.manager, role=AccountProfile.Role.MANAGER)

        cls.owner = user_model.objects.create_user(
            username="phase6d_discovery_owner",
            password="123456",
        )
        AccountProfile.objects.create(user=cls.owner, role=AccountProfile.Role.OWNER)

        cls.cashier = user_model.objects.create_user(
            username="phase6d_discovery_cashier",
            password="123456",
        )
        AccountProfile.objects.create(user=cls.cashier, role=AccountProfile.Role.CASHIER)

    def setUp(self):
        self.provider = Provider.objects.create(name=f"Phase6D Provider {self._testMethodName}")

    @override_settings(ENABLE_PROVIDER_ACCOUNT_SETTLEMENT_EXECUTION=False)
    def test_feature_flag_off_hides_launcher_and_blocks_direct_page(self):
        self.client.force_login(self.manager)
        resp = self.client.get("/manager/billing/providers/")
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "/account-settlement-test/")

        resp = self.client.get(f"/manager/debts/provider/{self.provider.public_id}/account-settlement-test/")
        self.assertEqual(resp.status_code, 403)

    @override_settings(ENABLE_PROVIDER_ACCOUNT_SETTLEMENT_EXECUTION=True)
    def test_feature_flag_on_still_does_not_show_launcher_in_provider_list(self):
        self.client.force_login(self.manager)
        resp = self.client.get("/manager/billing/providers/")
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "ENABLE_SETTLEMENT_TEST")
        self.assertNotContains(resp, "SETTLEMENT_TEST_URL_BASE")
        self.assertNotContains(resp, "/account-settlement-test/")

    @override_settings(ENABLE_PROVIDER_ACCOUNT_SETTLEMENT_EXECUTION=True)
    def test_manager_can_open_internal_test_page_directly(self):
        self.client.force_login(self.manager)
        resp = self.client.get(f"/manager/debts/provider/{self.provider.public_id}/account-settlement-test/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "INTERNAL TEST TOOL")

    @override_settings(ENABLE_PROVIDER_ACCOUNT_SETTLEMENT_EXECUTION=True)
    def test_cashier_cannot_access_provider_page_or_internal_test_page(self):
        self.client.force_login(self.cashier)
        resp = self.client.get("/manager/billing/providers/")
        self.assertEqual(resp.status_code, 403)

        resp = self.client.get(f"/manager/debts/provider/{self.provider.public_id}/account-settlement-test/")
        self.assertEqual(resp.status_code, 403)

    @override_settings(ENABLE_PROVIDER_ACCOUNT_SETTLEMENT_EXECUTION=True)
    def test_owner_access_behavior_matches_role_policy(self):
        self.client.force_login(self.owner)
        resp = self.client.get("/manager/billing/providers/")
        self.assertEqual(resp.status_code, 200)

        resp = self.client.get(f"/manager/debts/provider/{self.provider.public_id}/account-settlement-test/")
        self.assertEqual(resp.status_code, 200)
