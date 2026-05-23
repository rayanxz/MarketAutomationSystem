from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from accounts.models import AccountProfile
from billing.models import Provider


class ProviderPublicIdLayerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.manager = user_model.objects.create_user(
            username="provider_public_id_mgr",
            password="123456",
        )
        AccountProfile.objects.create(user=cls.manager, role=AccountProfile.Role.MANAGER)

    def setUp(self):
        self.client.force_login(self.manager)

    def test_new_providers_get_sequential_public_ids(self):
        p1 = Provider.objects.create(name="Public ID Provider A")
        p2 = Provider.objects.create(name="Public ID Provider B")

        self.assertRegex(p1.public_id, r"^P-\d+$")
        self.assertRegex(p2.public_id, r"^P-\d+$")
        self.assertGreater(int(p2.public_id.split("-")[1]), int(p1.public_id.split("-")[1]))

    def test_providers_list_api_includes_public_id_and_keeps_numeric_id(self):
        provider = Provider.objects.create(name="Public ID API Provider")

        resp = self.client.get(
            reverse("billing_api_providers_list"),
            {"basic": "1", "include_all": "1", "page_size": "30"},
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertTrue(body.get("ok"), body)

        row = next(item for item in body["items"] if item["name"] == provider.name)
        self.assertEqual(row["id"], provider.id)
        self.assertEqual(row["public_id"], provider.public_id)

    def test_provider_list_search_accepts_public_id(self):
        provider = Provider.objects.create(name="Search By Public ID", phone="0999000111")

        resp = self.client.get(
            reverse("billing_api_providers_list"),
            {"basic": "1", "q": provider.public_id, "include_all": "1", "page_size": "30"},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body.get("ok"), body)
        ids = {item["id"] for item in body["items"]}
        self.assertIn(provider.id, ids)

    def test_provider_autocomplete_search_accepts_public_id(self):
        provider = Provider.objects.create(name="Autocomplete Public ID Provider", phone="011223344")

        resp = self.client.get(reverse("billing_api_providers_ac"), {"q": provider.public_id})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body.get("ok"), body)
        row = next(item for item in body["items"] if int(item["id"]) == provider.id)
        self.assertEqual(row["public_id"], provider.public_id)
        self.assertEqual(row["name"], provider.name)

    def test_providers_list_template_uses_public_id_field_for_display(self):
        resp = self.client.get(reverse("billing_providers"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "const providerRef = p.public_id || p.id;")

    def test_provider_delete_route_accepts_public_id_ref(self):
        provider = Provider.objects.create(name="Delete By Public ID Provider")
        resp = self.client.post(
            reverse("billing_api_provider_delete", kwargs={"provider_ref": provider.public_id})
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json(), {"ok": True})
        provider.refresh_from_db()
        self.assertFalse(provider.is_active)

    def test_provider_delete_route_keeps_numeric_ref_compatibility(self):
        provider = Provider.objects.create(name="Delete By Numeric ID Provider")
        resp = self.client.post(
            reverse("billing_api_provider_delete", kwargs={"provider_ref": str(provider.id)})
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json(), {"ok": True})
        provider.refresh_from_db()
        self.assertFalse(provider.is_active)


class ProviderPublicIdMigrationBackfillTests(TransactionTestCase):
    reset_sequences = True

    migrate_from = ("billing", "0025_rename_billing_pr_reqfp_idx_billing_pro_provide_273707_idx")
    migrate_to = ("billing", "0026_provider_public_id_layer")

    def setUp(self):
        self.executor = MigrationExecutor(connection)
        self.executor.migrate([self.migrate_from])
        self.old_apps = self.executor.loader.project_state([self.migrate_from]).apps

    def tearDown(self):
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.executor.loader.graph.leaf_nodes())

    def test_existing_providers_get_backfilled_public_ids(self):
        OldProvider = self.old_apps.get_model("billing", "Provider")

        p1 = OldProvider.objects.create(name="Migration Provider One")
        p2 = OldProvider.objects.create(name="Migration Provider Two")

        self.executor = MigrationExecutor(connection)
        self.executor.migrate([self.migrate_to])
        new_apps = self.executor.loader.project_state([self.migrate_to]).apps
        NewProvider = new_apps.get_model("billing", "Provider")

        np1 = NewProvider.objects.get(pk=p1.id)
        np2 = NewProvider.objects.get(pk=p2.id)
        self.assertRegex(np1.public_id, r"^P-\d+$")
        self.assertRegex(np2.public_id, r"^P-\d+$")
        self.assertEqual(np1.public_id, "P-001")
        self.assertEqual(np2.public_id, "P-002")
