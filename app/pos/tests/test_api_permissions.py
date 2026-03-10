from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import AccountProfile
from pos.models import PosDay, PosShift, SalesBill


class PosApiPermissionsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.manager = User.objects.create_user(username="perm_manager", password="pw12345")
        cls.owner = User.objects.create_user(username="perm_owner", password="pw12345")
        cls.cashier_a = User.objects.create_user(username="perm_cashier_a", password="pw12345")
        cls.cashier_b = User.objects.create_user(username="perm_cashier_b", password="pw12345")
        cls.no_profile = User.objects.create_user(username="perm_plain", password="pw12345")

        AccountProfile.objects.create(user=cls.manager, role=AccountProfile.Role.MANAGER)
        AccountProfile.objects.create(user=cls.owner, role=AccountProfile.Role.OWNER)
        AccountProfile.objects.create(user=cls.cashier_a, role=AccountProfile.Role.CASHIER)
        AccountProfile.objects.create(user=cls.cashier_b, role=AccountProfile.Role.CASHIER)

    def _login(self, username: str) -> None:
        ok = self.client.login(username=username, password="pw12345")
        self.assertTrue(ok)

    def test_user_without_profile_cannot_access_pos_operator_api(self):
        self._login("perm_plain")
        resp = self.client.get(reverse("pos:api_search_name"), {"q": "abc"})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json().get("error"), "FORBIDDEN")

    def test_cashier_can_access_pos_operator_api(self):
        self._login("perm_cashier_a")
        resp = self.client.get(reverse("pos:api_search_name"), {"q": "abc"})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json().get("ok"))

    def test_cashier_cannot_access_manager_overview(self):
        self._login("perm_cashier_a")
        resp = self.client.get(reverse("pos:pos_manager_overview"))
        self.assertEqual(resp.status_code, 403)

    def test_manager_can_access_manager_overview(self):
        self._login("perm_manager")
        resp = self.client.get(reverse("pos:pos_manager_overview"))
        self.assertEqual(resp.status_code, 200)

    def test_owner_can_access_manager_overview(self):
        self._login("perm_owner")
        resp = self.client.get(reverse("pos:pos_manager_overview"))
        self.assertEqual(resp.status_code, 200)

    def test_manager_can_view_other_cashier_bill_detail_cashier_cannot(self):
        bill = SalesBill.objects.create(
            cashier=self.cashier_a,
            customer_name="P",
            parked=True,
            finalized=False,
            is_deleted=False,
        )

        self._login("perm_cashier_b")
        resp_cashier = self.client.get(reverse("pos:api_bill_detail", kwargs={"bill_id": bill.id}))
        self.assertEqual(resp_cashier.status_code, 403)
        self.client.logout()

        self._login("perm_manager")
        resp_manager = self.client.get(reverse("pos:api_bill_detail", kwargs={"bill_id": bill.id}))
        self.assertEqual(resp_manager.status_code, 200)
        self.assertTrue(resp_manager.json().get("ok"))

    def test_shift_end_is_owner_or_manager_scope_not_any_cashier(self):
        now = timezone.now()
        day = PosDay.objects.create(date=timezone.localdate(now))
        shift = PosShift.objects.create(
            user=self.cashier_a,
            day=day,
            started_at=now - timedelta(hours=1),
        )

        self._login("perm_cashier_b")
        deny = self.client.post(
            reverse("pos:api_shift_end"),
            data='{"id": %d}' % shift.id,
            content_type="application/json",
        )
        self.assertEqual(deny.status_code, 403)
        self.client.logout()

        self._login("perm_manager")
        allow = self.client.post(
            reverse("pos:api_shift_end"),
            data='{"id": %d}' % shift.id,
            content_type="application/json",
        )
        self.assertEqual(allow.status_code, 200)
        shift.refresh_from_db()
        self.assertIsNotNone(shift.ended_at)

    def test_no_profile_user_denied_across_core_pos_api_surface(self):
        self._login("perm_plain")

        get_cases = [
            (reverse("pos:api_search_name"), {"q": "abc"}),
            (reverse("pos:api_bills_today"), {}),
            (reverse("pos:api_customers_search"), {"q": "c"}),
        ]
        for url, params in get_cases:
            with self.subTest(url=url):
                resp = self.client.get(url, params)
                self.assertEqual(resp.status_code, 403)
                self.assertEqual(resp.json().get("error"), "FORBIDDEN")

        post_cases = [
            (reverse("pos:api_shift_start"), {}),
            (reverse("pos:api_login_end"), {"reason": "logout"}),
        ]
        for url, payload in post_cases:
            with self.subTest(url=url):
                resp = self.client.post(url, data=payload)
                self.assertEqual(resp.status_code, 403)
                self.assertEqual(resp.json().get("error"), "FORBIDDEN")

    def test_cashier_role_allowed_across_core_pos_api_surface(self):
        self._login("perm_cashier_a")

        self.assertEqual(self.client.get(reverse("pos:api_search_name"), {"q": "abc"}).status_code, 200)
        self.assertEqual(self.client.get(reverse("pos:api_bills_today")).status_code, 200)
        self.assertEqual(self.client.get(reverse("pos:api_customers_search"), {"q": "c"}).status_code, 200)
        self.assertEqual(self.client.post(reverse("pos:api_shift_start")).status_code, 200)
        self.assertEqual(self.client.post(reverse("pos:api_login_end"), data={"reason": "logout"}).status_code, 200)

    def test_owner_role_allowed_on_operator_apis(self):
        self._login("perm_owner")
        self.assertEqual(self.client.get(reverse("pos:api_search_name"), {"q": "abc"}).status_code, 200)
        self.assertEqual(self.client.get(reverse("pos:api_bills_today")).status_code, 200)
        self.assertEqual(self.client.post(reverse("pos:api_shift_start")).status_code, 200)

    def test_manager_only_return_wizard_enforced_by_role(self):
        wizard_url = reverse("pos:pos_manager_sale_return_wizard", kwargs={"bill_id": 999999})

        self._login("perm_cashier_a")
        denied_cashier = self.client.get(wizard_url)
        self.assertEqual(denied_cashier.status_code, 403)
        self.client.logout()

        self._login("perm_plain")
        denied_plain = self.client.get(wizard_url)
        self.assertEqual(denied_plain.status_code, 403)
        self.client.logout()

        self._login("perm_manager")
        manager_resp = self.client.get(wizard_url)
        self.assertEqual(manager_resp.status_code, 404)
        self.client.logout()

        self._login("perm_owner")
        owner_resp = self.client.get(wizard_url)
        self.assertEqual(owner_resp.status_code, 404)
