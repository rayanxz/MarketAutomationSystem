from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from accounts.models import AccountProfile
from billing.models import Provider
from financials.models import MoneyContainer


class ProviderAccountSettlementTestPagePhase6CTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.manager = user_model.objects.create_user(
            username="phase6c_test_page_mgr",
            password="123456",
        )
        AccountProfile.objects.create(user=cls.manager, role=AccountProfile.Role.MANAGER)

    def setUp(self):
        self.provider = Provider.objects.create(name=f"Phase6C Provider {self._testMethodName}")
        self.container = MoneyContainer.objects.create(
            name=f"Phase6C Drawer {self._testMethodName}",
            container_type=MoneyContainer.ContainerType.DRAWER,
            is_active=True,
            created_by=self.manager,
        )
        self.container.allowed_users.add(self.manager)
        self.client.force_login(self.manager)

    def _path(self, provider_ref: str) -> str:
        return f"/manager/debts/provider/{provider_ref}/account-settlement-test/"

    @override_settings(ENABLE_PROVIDER_ACCOUNT_SETTLEMENT_EXECUTION=True)
    def test_before_after_summary_and_totals_cards_render(self):
        resp = self.client.get(self._path(str(self.provider.id)))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="beforeAfterSummaryCard"')
        self.assertContains(resp, 'id="allocationTotalsCard"')
        self.assertContains(resp, 'id="sumBeforePayable"')
        self.assertContains(resp, 'id="sumAfterNet"')
        self.assertContains(resp, 'id="totRequested"')
        self.assertContains(resp, 'id="totAllocatedCount"')

    @override_settings(ENABLE_PROVIDER_ACCOUNT_SETTLEMENT_EXECUTION=True)
    def test_execution_summary_card_rendered(self):
        resp = self.client.get(self._path(str(self.provider.id)))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="executionSummaryCard"')
        self.assertContains(resp, 'id="execActionPublicId"')
        self.assertContains(resp, 'id="execReceiptRef"')
        self.assertContains(resp, 'id="execReplayLabel"')

    @override_settings(ENABLE_PROVIDER_ACCOUNT_SETTLEMENT_EXECUTION=True)
    def test_raw_json_remains_collapsible_and_visible(self):
        resp = self.client.get(self._path(str(self.provider.id)))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="rawJsonDetails"')
        self.assertContains(resp, 'id="rawJsonBox"')
        self.assertContains(resp, "Raw JSON Response")

    @override_settings(ENABLE_PROVIDER_ACCOUNT_SETTLEMENT_EXECUTION=True)
    def test_execute_starts_disabled_until_fresh_preview(self):
        resp = self.client.get(self._path(str(self.provider.id)))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="executeBtn" class="btn danger" type="button" disabled')
        self.assertContains(resp, "Run preview first.")
        self.assertContains(resp, "previewFresh")

    @override_settings(ENABLE_PROVIDER_ACCOUNT_SETTLEMENT_EXECUTION=True)
    def test_stale_preview_behavior_markers_exist(self):
        resp = self.client.get(self._path(str(self.provider.id)))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "markPreviewStale")
        self.assertContains(resp, "setPreviewState('Preview stale', 'stale')")
        self.assertContains(resp, "previewSignature")
        self.assertContains(resp, "containerInput.addEventListener('change'")
        self.assertContains(resp, "amountInput.addEventListener('input'")

    @override_settings(ENABLE_PROVIDER_ACCOUNT_SETTLEMENT_EXECUTION=True)
    def test_unresolved_diagnostics_warning_and_execute_block_markers_exist(self):
        resp = self.client.get(self._path(str(self.provider.id)))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "unresolved_identities")
        self.assertContains(resp, "Execution blocked: unresolved identities present")
        self.assertContains(resp, "executeBtn.disabled = unresolvedBlocking")

    @override_settings(ENABLE_PROVIDER_ACCOUNT_SETTLEMENT_EXECUTION=True)
    def test_idempotent_replay_visibility_markers_exist(self):
        resp = self.client.get(self._path(str(self.provider.id)))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "IDEMPOTENT REPLAY RESULT")
        self.assertContains(resp, "execution.idempotent_replay")
        self.assertContains(resp, "replayTag.hidden = !execution.idempotent_replay")
