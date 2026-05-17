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
    def test_feature_flag_off_hides_internal_access_path(self):
        self.client.force_login(self.manager)
        resp = self.client.get("/manager/billing/providers/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "const ENABLE_SETTLEMENT_TEST = false;")
        self.assertNotContains(resp, "اختبار التسوية")

    @override_settings(ENABLE_PROVIDER_ACCOUNT_SETTLEMENT_EXECUTION=True)
    def test_manager_and_owner_see_internal_access_path(self):
        self.client.force_login(self.manager)
        resp = self.client.get("/manager/billing/providers/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "const ENABLE_SETTLEMENT_TEST = true;")
        self.assertContains(resp, "اختبار التسوية")
        self.assertContains(resp, "INTERNAL TEST · ACCOUNT SETTLEMENT")

        self.client.force_login(self.owner)
        resp = self.client.get("/manager/billing/providers/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "const ENABLE_SETTLEMENT_TEST = true;")
        self.assertContains(resp, "اختبار التسوية")

    @override_settings(ENABLE_PROVIDER_ACCOUNT_SETTLEMENT_EXECUTION=True)
    def test_generated_url_template_is_correct_and_not_mass_exposed(self):
        self.client.force_login(self.manager)
        resp = self.client.get("/manager/billing/providers/")
        self.assertEqual(resp.status_code, 200)
        expected_base = "/manager/debts/provider/0/account-settlement-test/"
        self.assertContains(resp, f'const SETTLEMENT_TEST_URL_BASE = "{expected_base}";')
        self.assertContains(resp, 'SETTLEMENT_TEST_URL_BASE.replace("/0/", `/${p.id}/`)')
        self.assertContains(resp, 'internal-row-link')

        html = resp.content.decode("utf-8")
        self.assertEqual(html.count('id="providerSettlementInternalTool"'), 0)
        self.assertEqual(html.count("/account-settlement-test/"), 1)

    @override_settings(ENABLE_PROVIDER_ACCOUNT_SETTLEMENT_EXECUTION=True)
    def test_cashier_cannot_access_provider_page_or_internal_test_page(self):
        self.client.force_login(self.cashier)
        resp = self.client.get("/manager/billing/providers/")
        self.assertEqual(resp.status_code, 403)

        resp = self.client.get(f"/manager/debts/provider/{self.provider.id}/account-settlement-test/")
        self.assertEqual(resp.status_code, 403)

    @override_settings(ENABLE_PROVIDER_ACCOUNT_SETTLEMENT_EXECUTION=True)
    def test_manager_can_open_internal_test_page_via_generated_route(self):
        self.client.force_login(self.manager)
        resp = self.client.get("/manager/billing/providers/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'SETTLEMENT_TEST_URL_BASE.replace("/0/", `/${p.id}/`)')

        resp = self.client.get(f"/manager/debts/provider/{self.provider.id}/account-settlement-test/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "INTERNAL TEST TOOL")
