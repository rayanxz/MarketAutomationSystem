from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import AccountProfile
from pos.models import SalesBill, SalesReturn
from stock.models import ProductContainer


class PosReturnErrorHandlingPhase5Tests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.manager = User.objects.create_user(username="mgr_pos_ret_phase5", password="pw12345")
        profile, _ = AccountProfile.objects.get_or_create(user=cls.manager)
        profile.role = AccountProfile.Role.MANAGER
        profile.save(update_fields=["role"])

        cls.store, _ = ProductContainer.objects.get_or_create(
            code="store",
            defaults={"name": "Store", "is_store": True, "is_active": True},
        )
        if not cls.store.is_active:
            cls.store.is_active = True
            cls.store.save(update_fields=["is_active"])

    def setUp(self):
        ok = self.client.login(username="mgr_pos_ret_phase5", password="pw12345")
        self.assertTrue(ok)

    def _make_draft_return(self) -> SalesReturn:
        bill = SalesBill.objects.create(
            cashier=self.manager,
            parked=False,
            finalized=True,
            customer_name="Test Customer",
        )
        return SalesReturn.objects.create(
            sale_bill=bill,
            stock_container=self.store,
            status=SalesReturn.Status.DRAFT,
            created_by=self.manager,
        )

    def test_post_endpoint_sanitizes_unexpected_service_errors(self):
        ret = self._make_draft_return()
        url = reverse("pos:pos_manager_sale_return_post", kwargs={"return_id": ret.id})

        with patch("pos.returns_views.SV.post_sales_return", side_effect=RuntimeError("internal db trace")):
            resp = self.client.post(url, data={"settle_mode": "cash", "money_container_id": ""})

        self.assertEqual(resp.status_code, 400)
        body = resp.content.decode("utf-8")
        self.assertIn("Failed to post sales return.", body)
        self.assertNotIn("internal db trace", body)

    def test_post_endpoint_keeps_domain_validation_message(self):
        ret = self._make_draft_return()
        url = reverse("pos:pos_manager_sale_return_post", kwargs={"return_id": ret.id})

        with patch("pos.returns_views.SV.post_sales_return", side_effect=ValueError("Return is not in draft state.")):
            resp = self.client.post(url, data={"settle_mode": "cash", "money_container_id": ""})

        self.assertEqual(resp.status_code, 400)
        self.assertIn("Return is not in draft state.", resp.content.decode("utf-8"))
