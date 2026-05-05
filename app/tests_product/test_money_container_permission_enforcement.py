from __future__ import annotations

import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import AccountProfile
from billing import services as BillingSV
from billing.models import Provider
from catalog.models import Product, ProductCollection, ProductSet, UnitType
from debts import services as DebtSV
from debts.models import PartyType
from financials import services as FinSV
from financials.models import ContainerFeature, Currency, MoneyContainer, MoneyContainerCurrency
from pos.models import SalesBill
from stock.models import ProductContainer


class MoneyContainerPermissionEnforcementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()

        cls.owner = User.objects.create_user(username="owner_perm", password="pw")
        cls.allowed_mgr = User.objects.create_user(username="allowed_mgr_perm", password="pw")
        cls.forbidden_mgr = User.objects.create_user(username="forbidden_mgr_perm", password="pw")

        AccountProfile.objects.create(user=cls.owner, role=AccountProfile.Role.OWNER)
        AccountProfile.objects.create(user=cls.allowed_mgr, role=AccountProfile.Role.MANAGER)
        AccountProfile.objects.create(user=cls.forbidden_mgr, role=AccountProfile.Role.MANAGER)

        cls.syp, _ = Currency.objects.get_or_create(
            code="SYP",
            defaults={"name": "Syrian Pound", "decimals": 0, "is_active": True},
        )
        cls.usd, _ = Currency.objects.get_or_create(
            code="USD",
            defaults={"name": "US Dollar", "decimals": 2, "is_active": True},
        )

        cls.f_purchase, _ = ContainerFeature.objects.get_or_create(
            code="purchase_bills",
            defaults={"name": "Purchase Bills", "is_active": True},
        )
        cls.f_provider_returns, _ = ContainerFeature.objects.get_or_create(
            code="provider_returns",
            defaults={"name": "Provider Returns", "is_active": True},
        )
        cls.f_pos_sales, _ = ContainerFeature.objects.get_or_create(
            code="pos_sales",
            defaults={"name": "POS Sales", "is_active": True},
        )
        cls.f_pos_returns, _ = ContainerFeature.objects.get_or_create(
            code="pos_returns",
            defaults={"name": "POS Returns", "is_active": True},
        )

        cls.purchase_container = MoneyContainer.objects.create(
            name="Perm Purchase Drawer",
            container_type=MoneyContainer.ContainerType.DRAWER,
            is_active=True,
            created_by=cls.allowed_mgr,
        )
        cls.purchase_container.features.add(cls.f_purchase, cls.f_provider_returns)
        cls.purchase_container.allowed_users.add(cls.allowed_mgr)

        cls.pos_container = MoneyContainer.objects.create(
            name="Perm POS Drawer",
            container_type=MoneyContainer.ContainerType.DRAWER,
            is_active=True,
            created_by=cls.allowed_mgr,
        )
        cls.pos_container.features.add(cls.f_pos_sales, cls.f_pos_returns)
        cls.pos_container.allowed_users.add(cls.allowed_mgr)

        for c in (cls.purchase_container, cls.pos_container):
            MoneyContainerCurrency.objects.update_or_create(
                container=c,
                currency=cls.syp,
                defaults={"is_enabled": True},
            )
            MoneyContainerCurrency.objects.update_or_create(
                container=c,
                currency=cls.usd,
                defaults={"is_enabled": True},
            )

        cls.store = ProductContainer.objects.filter(code="store").first()
        if not cls.store:
            cls.store = ProductContainer.objects.create(
                name="Store",
                code="store",
                is_store=True,
                is_active=True,
            )

        col = ProductCollection.objects.create(name="Perm Collection")
        pset = ProductSet.objects.create(collection=col, name="Perm Set")
        cls.product = Product.objects.create(
            name="Perm Product",
            set=pset,
            unit_primary=UnitType.PIECE,
            allow_syp_purchasing=True,
            allow_usd_purchasing=True,
            allow_syp_sales=True,
            allow_usd_sales=True,
            default_cost_syp=Decimal("1000"),
            default_cost_usd=Decimal("1"),
            default_price_syp=Decimal("1200"),
            default_price_usd=Decimal("2"),
        )
        cls.provider = Provider.objects.create(name="Perm Provider")

        FinSV.set_current_fx(actor=cls.owner, rate_syp_per_usd=Decimal("15000"))

    def _billing_payload(self, container_id: int) -> dict:
        return {
            "provider": {"id": self.provider.id},
            "container": "store",
            "money_container_id": container_id,
            "currency_code": "SYP",
            "items": [
                {
                    "product_id": self.product.id,
                    "unit_index": 1,
                    "qty_raw": "1",
                    "cost": "1000",
                    "currency": "SYP",
                }
            ],
            "pay": {
                "status": "paid",
                "method": "syp_only",
                "amount_syp": "1000",
                "amount_usd": "0",
                "paid_amount": "0",
                "fx_rate": "15000",
            },
        }

    def _login(self, user) -> None:
        ok = self.client.login(username=user.username, password="pw")
        self.assertTrue(ok)

    def test_purchase_bill_dropdown_and_api_enforce_permissions(self):
        self._login(self.forbidden_mgr)
        resp = self.client.get(reverse("billing_add"))
        ids = {c.id for c in resp.context["money_containers"]}
        self.assertNotIn(self.purchase_container.id, ids)

        payload = self._billing_payload(self.purchase_container.id)
        bad = self.client.post(
            reverse("billing_api_bill_save"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(bad.status_code, 400)
        self.assertIn("not allowed", (bad.json().get("error") or "").lower())

        self.client.logout()
        self._login(self.allowed_mgr)
        ok = self.client.post(
            reverse("billing_api_bill_save"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(ok.status_code, 200, ok.content.decode("utf-8"))
        self.assertTrue(ok.json().get("ok"))

    def test_owner_is_always_allowed_even_without_allowed_users(self):
        self._login(self.owner)
        resp = self.client.get(reverse("billing_add"))
        ids = {c.id for c in resp.context["money_containers"]}
        self.assertIn(self.purchase_container.id, ids)

    def test_creator_self_removal_persists_and_usage_access_is_revoked(self):
        self._login(self.allowed_mgr)

        edit_url = reverse("financials:container_edit", args=[self.purchase_container.id])
        base_payload = {
            "ref_code": self.purchase_container.ref_code,
            "name": self.purchase_container.name,
            "container_type": self.purchase_container.container_type,
            "is_active": "on",
            "note": self.purchase_container.note or "",
            "currencies": [self.syp.id, self.usd.id],
            "features": [self.f_purchase.id, self.f_provider_returns.id],
        }

        remove_self = self.client.post(
            edit_url,
            data={**base_payload, "allowed_users": []},
            follow=False,
        )
        self.assertEqual(remove_self.status_code, 302)

        self.purchase_container.refresh_from_db()
        self.assertFalse(
            self.purchase_container.allowed_users.filter(pk=self.allowed_mgr.pk).exists()
        )

        edit_again = self.client.get(edit_url)
        self.assertEqual(edit_again.status_code, 200)

        billing_screen = self.client.get(reverse("billing_add"))
        ids = {c.id for c in billing_screen.context["money_containers"]}
        self.assertNotIn(self.purchase_container.id, ids)

        payload = self._billing_payload(self.purchase_container.id)
        blocked = self.client.post(
            reverse("billing_api_bill_save"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(blocked.status_code, 400)
        self.assertIn("not allowed", (blocked.json().get("error") or "").lower())

        reallow_self = self.client.post(
            edit_url,
            data={**base_payload, "allowed_users": [self.allowed_mgr.id]},
            follow=False,
        )
        self.assertEqual(reallow_self.status_code, 302)

        self.purchase_container.refresh_from_db()
        self.assertTrue(
            self.purchase_container.allowed_users.filter(pk=self.allowed_mgr.pk).exists()
        )

    def test_provider_return_flow_blocks_forbidden_container(self):
        bill = BillingSV.create_bill(
            actor=self.allowed_mgr,
            provider_id=self.provider.id,
            status="unpaid",
            paid_amount=Decimal("0"),
            items=[
                {
                    "product_id": self.product.id,
                    "unit_index": 1,
                    "qty_raw": "2",
                    "cost": "1000",
                    "currency": "SYP",
                }
            ],
            container=self.store,
            money_container_id=None,
            settlement_currency="SYP",
        )
        item = bill.items.first()
        self.assertIsNotNone(item)

        self._login(self.forbidden_mgr)
        wiz = self.client.get(
            reverse("billing_bill_return_wizard", kwargs={"bill_id": bill.public_id}),
            data={"items": str(item.id)},
        )
        wiz_ids = {c.id for c in wiz.context["money_containers"]}
        self.assertNotIn(self.purchase_container.id, wiz_ids)

        with self.assertRaises(ValueError):
            BillingSV.create_return(
                actor=self.forbidden_mgr,
                provider_id=self.provider.id,
                status="paid",
                paid_amount=Decimal("1000"),
                items=[
                    {
                        "bill_item_id": item.id,
                        "product_id": self.product.id,
                        "unit_index": 1,
                        "qty_primary": "1",
                        "cost": "1000",
                        "currency": "SYP",
                        "container_splits": [{"code": "store", "qty_primary": "1"}],
                    }
                ],
                container=None,
                source_bill_serial=bill.serial,
                money_container_id=self.purchase_container.id,
                currency_code="SYP",
                valuation_mode="HISTORICAL",
            )

    def test_pos_screen_and_api_enforce_permissions(self):
        self._login(self.forbidden_mgr)
        screen = self.client.get(reverse("pos:pos_screen"))
        ids = {int(row["id"]) for row in screen.context["pos_containers"]}
        self.assertNotIn(self.pos_container.id, ids)

        payload = {
            "id": None,
            "parked": True,
            "pay_status": "none",
            "paid_amount": "0",
            "total_amount": "0",
            "settlement_mode": "split",
            "money_container_id": self.pos_container.id,
            "rows": [
                {
                    "product_id": self.product.id,
                    "name": self.product.name,
                    "number": str(self.product.id),
                    "qty": "1",
                    "uom_index": 1,
                    "unit_price": "1200",
                    "currency": "SYP",
                    "disc_amount": "0",
                    "disc_pct": "0",
                }
            ],
        }
        bad = self.client.post(
            reverse("pos:api_bill_save"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(bad.status_code, 403)
        self.assertEqual(bad.json().get("error"), "CONTAINER_FORBIDDEN")

        self.client.logout()
        self._login(self.allowed_mgr)
        ok = self.client.post(
            reverse("pos:api_bill_save"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(ok.status_code, 200, ok.content.decode("utf-8"))
        self.assertTrue(ok.json().get("ok"))
        self.assertTrue(SalesBill.objects.filter(public_id=ok.json()["bill"]["id"]).exists())

    def test_manual_event_and_debt_payment_reject_forbidden_container(self):
        self._login(self.forbidden_mgr)
        bad_manual = self.client.post(
            reverse("financials:manual_event"),
            data={
                "action": "add",
                "to_container": str(self.purchase_container.id),
                "currency": "SYP",
                "amount": "10",
            },
        )
        self.assertEqual(bad_manual.status_code, 400)
        self.assertIn("not allowed", (bad_manual.json().get("error") or "").lower())

        entry = DebtSV.create_debtor_entry(
            provider=self.provider,
            total=Decimal("100"),
            paid_amount=Decimal("0"),
            source_app="debts",
            source_model="ManualDebt",
            source_id="perm-entry-1",
            currency_code="SYP",
            party_type=PartyType.PROVIDER,
            party_name=self.provider.name,
        )

        bad_pay = self.client.post(
            reverse(
                "debts_api_entry_pay_batch",
                kwargs={"direction": "debtor", "entry_id": entry.id},
            ),
            data={
                "amount": "10",
                "money_container_id": str(self.purchase_container.id),
                "currency_code": "SYP",
            },
        )
        self.assertEqual(bad_pay.status_code, 400)
        self.assertIn("access denied", (bad_pay.json().get("error") or "").lower())
