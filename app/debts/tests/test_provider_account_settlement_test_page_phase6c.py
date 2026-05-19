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
    def test_provider_selection_controls_render(self):
        resp = self.client.get(self._path(str(self.provider.id)))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "A) Provider Selection")
        self.assertContains(resp, 'id="providerSearchInput"')
        self.assertContains(resp, 'id="providerSearchBtn"')
        self.assertContains(resp, 'id="providerSearchResults"')
        self.assertContains(resp, 'id="selectedProviderMeta"')
        self.assertContains(resp, 'id="refreshProviderBtn"')
        self.assertContains(resp, 'data-provider-ac-url="/manager/billing/api/providers/ac/"')

    @override_settings(ENABLE_PROVIDER_ACCOUNT_SETTLEMENT_EXECUTION=True)
    def test_main_balance_card_rendered(self):
        resp = self.client.get(self._path(str(self.provider.id)))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "B) Total Provider Net Balance")
        self.assertContains(resp, 'id="netSypPayable"')
        self.assertContains(resp, 'id="netSypReceivable"')
        self.assertContains(resp, 'id="netSypNet"')
        self.assertContains(resp, 'id="netUsdPayable"')
        self.assertContains(resp, 'id="netUsdReceivable"')
        self.assertContains(resp, 'id="netUsdNet"')
        self.assertContains(resp, "Positive net: provider owes store.")

    @override_settings(ENABLE_PROVIDER_ACCOUNT_SETTLEMENT_EXECUTION=True)
    def test_obligation_derivation_table_and_summary_render(self):
        resp = self.client.get(self._path(str(self.provider.id)))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "C) How Net Balance Was Derived")
        self.assertContains(resp, 'id="sumOpenObligationCount"')
        self.assertContains(resp, 'id="sumOpenPayableCount"')
        self.assertContains(resp, 'id="sumOpenReceivableCount"')
        self.assertContains(resp, 'id="sumPayableSyp"')
        self.assertContains(resp, 'id="sumReceivableUsd"')
        self.assertContains(resp, 'id="diagnosticsAlerts"')
        self.assertContains(resp, 'id="obligationsTableBody"')

    @override_settings(ENABLE_PROVIDER_ACCOUNT_SETTLEMENT_EXECUTION=True)
    def test_preview_visualization_sections_render(self):
        resp = self.client.get(self._path(str(self.provider.id)))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "E) Preview Visualization")
        self.assertContains(resp, 'id="previewRequestedAmount"')
        self.assertContains(resp, 'id="previewAllocatableAmount"')
        self.assertContains(resp, 'id="previewTotalApplied"')
        self.assertContains(resp, 'id="previewUnallocatedAmount"')
        self.assertContains(resp, 'id="previewEligibleCount"')
        self.assertContains(resp, 'id="previewAllocatedCount"')
        self.assertContains(resp, 'id="previewFingerprint"')
        self.assertContains(resp, 'id="previewAllocTableBody"')
        self.assertContains(resp, 'id="previewStateBadge"')

    @override_settings(ENABLE_PROVIDER_ACCOUNT_SETTLEMENT_EXECUTION=True)
    def test_execution_summary_and_reload_markers_render(self):
        resp = self.client.get(self._path(str(self.provider.id)))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "F) Execution Results")
        self.assertContains(resp, 'id="execActionPublicId"')
        self.assertContains(resp, 'id="execReceiptSerial"')
        self.assertContains(resp, 'id="execTotalApplied"')
        self.assertContains(resp, 'id="execAllocationCount"')
        self.assertContains(resp, 'id="remainingRefreshNote"')
        self.assertContains(resp, "await loadProviderSnapshot('execution')")

    @override_settings(ENABLE_PROVIDER_ACCOUNT_SETTLEMENT_EXECUTION=True)
    def test_before_after_and_delta_summaries_render(self):
        resp = self.client.get(self._path(str(self.provider.id)))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="previewBeforePayable"')
        self.assertContains(resp, 'id="previewAfterNet"')
        self.assertContains(resp, 'id="previewDeltaNet"')
        self.assertContains(resp, 'id="execBeforePayable"')
        self.assertContains(resp, 'id="execAfterNet"')
        self.assertContains(resp, 'id="execDeltaNet"')

    @override_settings(ENABLE_PROVIDER_ACCOUNT_SETTLEMENT_EXECUTION=True)
    def test_raw_json_remains_collapsible_and_visible(self):
        resp = self.client.get(self._path(str(self.provider.id)))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="rawDebugDetails"')
        self.assertContains(resp, 'id="rawNetPayload"')
        self.assertContains(resp, 'id="rawPreviewPayload"')
        self.assertContains(resp, 'id="rawExecutePayload"')
        self.assertContains(resp, "Expand raw payloads")

    @override_settings(ENABLE_PROVIDER_ACCOUNT_SETTLEMENT_EXECUTION=True)
    def test_execute_starts_disabled_until_fresh_preview(self):
        resp = self.client.get(self._path(str(self.provider.id)))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="executeBtn" class="x-btn danger" type="button" disabled')
        self.assertContains(resp, "Run fresh preview first.")
        self.assertContains(resp, "state.previewFresh")
        self.assertContains(resp, "state.previewSignature")

    @override_settings(ENABLE_PROVIDER_ACCOUNT_SETTLEMENT_EXECUTION=True)
    def test_stale_preview_behavior_markers_exist(self):
        resp = self.client.get(self._path(str(self.provider.id)))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "markPreviewStale")
        self.assertContains(resp, "setPreviewBadge('Preview stale', 'stale')")
        self.assertContains(resp, "payloadSignature")
        self.assertContains(resp, "el.containerInput.addEventListener('change'")
        self.assertContains(resp, "el.amountInput.addEventListener('input'")
        self.assertContains(resp, "el.actionInput, el.currencyInput")

    @override_settings(ENABLE_PROVIDER_ACCOUNT_SETTLEMENT_EXECUTION=True)
    def test_unresolved_diagnostics_warning_and_execute_block_markers_exist(self):
        resp = self.client.get(self._path(str(self.provider.id)))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "unresolved_identities")
        self.assertContains(resp, "Execution blocked: unresolved identities")
        self.assertContains(resp, "state.unresolvedBlocking")
        self.assertContains(resp, "|| state.unresolvedBlocking")

    @override_settings(ENABLE_PROVIDER_ACCOUNT_SETTLEMENT_EXECUTION=True)
    def test_idempotent_replay_visibility_markers_exist(self):
        resp = self.client.get(self._path(str(self.provider.id)))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "IDEMPOTENT REPLAY RESULT")
        self.assertContains(resp, "execution.idempotent_replay")
        self.assertContains(resp, "setReplayBadge(Boolean(execution.idempotent_replay));")

    @override_settings(ENABLE_PROVIDER_ACCOUNT_SETTLEMENT_EXECUTION=True)
    def test_action_lab_controls_render(self):
        resp = self.client.get(self._path(str(self.provider.id)))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "D) Account Action Lab")
        self.assertContains(resp, 'id="actionInput"')
        self.assertContains(resp, 'id="currencyInput"')
        self.assertContains(resp, 'id="amountInput"')
        self.assertContains(resp, 'id="containerInput"')
        self.assertContains(resp, 'id="idempotencyKeyInput"')
        self.assertContains(resp, 'id="previewBtn"')
        self.assertContains(resp, 'id="executeBtn"')
        self.assertContains(resp, 'id="replayBadge"')

    @override_settings(ENABLE_PROVIDER_ACCOUNT_SETTLEMENT_EXECUTION=True)
    def test_provider_snapshot_auto_load_and_reload_hooks_exist(self):
        resp = self.client.get(self._path(str(self.provider.id)))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "loadProviderSnapshot('initial load')")
        self.assertContains(resp, "await loadProviderSnapshot('provider selection')")
        self.assertContains(resp, "await loadProviderSnapshot('manual reload')")
        self.assertContains(resp, "renderObligationsTable")
        self.assertContains(resp, "renderNetCard")

    @override_settings(ENABLE_PROVIDER_ACCOUNT_SETTLEMENT_EXECUTION=True)
    def test_execution_summary_card_rendered(self):
        resp = self.client.get(self._path(str(self.provider.id)))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="execActionId"')
        self.assertContains(resp, 'id="execStatus"')
        self.assertContains(resp, 'id="execReplayLabel"')
