from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import AccountProfile
from billing.models import Provider


class ProvidersListUiCleanupPhase1Tests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.manager = user_model.objects.create_user(
            username="providers_ui_cleanup_mgr",
            password="123456",
        )
        AccountProfile.objects.create(user=cls.manager, role=AccountProfile.Role.MANAGER)

    def setUp(self):
        self.client.force_login(self.manager)

    def test_page_uses_arabic_labels_and_columns(self):
        resp = self.client.get(reverse("billing_providers"))
        self.assertEqual(resp.status_code, 200)

        self.assertContains(resp, "قائمة الموردين")
        self.assertContains(resp, "إنشاء ملف مورد جديد")
        self.assertContains(resp, "رقم المورد")
        self.assertContains(resp, "اسم المورد")
        self.assertContains(resp, "رقم الهاتف")
        self.assertContains(resp, "الإجراءات")

    def test_no_english_ui_labels_in_page(self):
        resp = self.client.get(reverse("billing_providers"))
        self.assertEqual(resp.status_code, 200)

        self.assertNotContains(resp, "Providers List")
        self.assertNotContains(resp, "Provider Name")
        self.assertNotContains(resp, "Show Provider Info")
        self.assertNotContains(resp, "Show Bills")
        self.assertNotContains(resp, "Show Debts")
        self.assertNotContains(resp, "Load More")

    def test_actions_are_present_and_grouped(self):
        resp = self.client.get(reverse("billing_providers"))
        self.assertEqual(resp.status_code, 200)

        self.assertContains(resp, 'data-action="provider-info"')
        self.assertContains(resp, 'data-action="provider-bills"')
        self.assertContains(resp, 'data-action="provider-debts"')
        self.assertContains(resp, 'data-action="provider-hide"')
        self.assertContains(resp, 'data-actions-row="1"')

        self.assertContains(resp, "عرض معلومات المورد")
        self.assertContains(resp, "عرض الفواتير")
        self.assertContains(resp, "عرض الديون")
        self.assertContains(resp, "إخفاء")

        self.assertContains(resp, 'data-action="provider-info" disabled')
        self.assertContains(resp, 'data-action="provider-debts" disabled')
        self.assertContains(resp, 'data-action="provider-hide" disabled')

    def test_show_bills_action_still_uses_billing_list_route(self):
        resp = self.client.get(reverse("billing_providers"))
        self.assertEqual(resp.status_code, 200)
        expected = 'const billsUrl = "{}" + "?q=" + encodeURIComponent(p.name);'.format(reverse("billing_list"))
        self.assertContains(resp, expected)

    def test_table_structure_classes_exist(self):
        resp = self.client.get(reverse("billing_providers"))
        self.assertEqual(resp.status_code, 200)

        self.assertContains(resp, "providers-panel")
        self.assertContains(resp, "providers-create")
        self.assertContains(resp, "providers-list")
        self.assertContains(resp, 'id="q"')
        self.assertContains(resp, 'id="btnSearch"')
        self.assertContains(resp, "providers-table")
        self.assertContains(resp, 'data-col="provider-id"')
        self.assertContains(resp, 'data-col="provider-name"')
        self.assertContains(resp, 'data-col="provider-phone"')
        self.assertContains(resp, 'data-col="provider-actions"')

    def test_actions_column_has_visual_separator_rule(self):
        resp = self.client.get(reverse("billing_providers"))
        self.assertEqual(resp.status_code, 200)

        self.assertContains(resp, '.providers-table th[data-col="provider-actions"]')
        self.assertContains(resp, ".providers-table td.actions-cell")
        self.assertContains(resp, "border-inline-start:1px solid #e5e7eb;")

    def test_removed_columns_and_dropdown_and_delete_controls(self):
        resp = self.client.get(reverse("billing_providers"))
        self.assertEqual(resp.status_code, 200)

        self.assertNotContains(resp, "p.bills_count")
        self.assertNotContains(resp, "p.unpaid_bills_count")
        self.assertNotContains(resp, "debt_totals")
        self.assertNotContains(resp, 'id="fState"')
        self.assertNotContains(resp, "data-del")
        self.assertNotContains(resp, "pvdDelModal")
        self.assertNotContains(resp, "api_provider_delete")

    def test_providers_page_requests_lightweight_basic_api_mode(self):
        resp = self.client.get(reverse("billing_providers"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'basic: "1"')

    def test_provider_api_defaults_to_include_all(self):
        p1 = Provider.objects.create(name="UI Cleanup Provider 1")
        p2 = Provider.objects.create(name="UI Cleanup Provider 2")

        resp = self.client.get(reverse("billing_api_providers_list"))
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body.get("ok"), body)

        ids = {int(item["id"]) for item in body.get("items") or []}
        self.assertIn(p1.id, ids)
        self.assertIn(p2.id, ids)

    def test_basic_provider_api_path_does_not_invoke_payable_collector(self):
        Provider.objects.create(name="UI Cleanup Collector Bypass 1")
        Provider.objects.create(name="UI Cleanup Collector Bypass 2")

        with patch("billing.selectors.collect_provider_open_obligations_batch") as collector_mock:
            resp = self.client.get(reverse("billing_api_providers_list"), {"basic": "1", "page_size": "30"})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body.get("ok"), body)
        self.assertGreaterEqual(len(body.get("items") or []), 2)
        collector_mock.assert_not_called()

    def test_providers_page_no_longer_exposes_settlement_launcher(self):
        resp = self.client.get(reverse("billing_providers"))
        self.assertEqual(resp.status_code, 200)

        self.assertNotContains(resp, "ENABLE_SETTLEMENT_TEST")
        self.assertNotContains(resp, "SETTLEMENT_TEST_URL_BASE")
        self.assertNotContains(resp, "/account-settlement-test/")
