from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import AccountProfile
from billing import services as BillingSV
from billing.models import Bill, Provider, ProviderReturn
from catalog.models import Product, ProductCollection, ProductSet, UnitType
from debts.models import CreditorDebt
from financials import services as FinSV
from financials.models import ContainerFeature, Currency, MoneyContainer, MoneyContainerCurrency
from stock.models import ProductContainer


class ReturnWizardPrecisionParityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.user = User.objects.create_user(username="mgr_return_wizard", password="pw12345")
        AccountProfile.objects.create(user=cls.user, role=AccountProfile.Role.MANAGER)

        cls.syp, _ = Currency.objects.get_or_create(
            code="SYP",
            defaults={"name": "Syrian Pound", "decimals": 0, "is_active": True},
        )
        cls.usd, _ = Currency.objects.get_or_create(
            code="USD",
            defaults={"name": "US Dollar", "decimals": 2, "is_active": True},
        )
        cls.syp.decimals = 0
        cls.syp.is_active = True
        cls.syp.save(update_fields=["decimals", "is_active"])
        cls.usd.decimals = 2
        cls.usd.is_active = True
        cls.usd.save(update_fields=["decimals", "is_active"])

        cls.store, _ = ProductContainer.objects.get_or_create(
            code="store",
            defaults={"name": "Store", "is_store": True, "is_active": True},
        )

        cls.cash = MoneyContainer.objects.create(
            name="Wizard Cash",
            container_type=MoneyContainer.ContainerType.DRAWER,
            is_active=True,
            created_by=cls.user,
        )
        cls.cash.allowed_users.add(cls.user)
        feature, _ = ContainerFeature.objects.get_or_create(
            code="provider_returns",
            defaults={"name": "Provider Returns", "is_active": True},
        )
        if not feature.is_active:
            feature.is_active = True
            feature.save(update_fields=["is_active"])
        cls.cash.features.add(feature)
        MoneyContainerCurrency.objects.update_or_create(
            container=cls.cash,
            currency=cls.syp,
            defaults={"is_enabled": True},
        )
        MoneyContainerCurrency.objects.update_or_create(
            container=cls.cash,
            currency=cls.usd,
            defaults={"is_enabled": True},
        )

        FinSV.set_current_fx(actor=cls.user, rate_syp_per_usd=Decimal("15000"))

        collection = ProductCollection.objects.create(name="Wizard Collection")
        prod_set = ProductSet.objects.create(collection=collection, name="Wizard Set")
        cls.product = Product.objects.create(
            name="Wizard USD Product",
            set=prod_set,
            unit_primary=UnitType.PIECE,
            allow_syp_purchasing=True,
            allow_usd_purchasing=True,
            allow_syp_sales=True,
            allow_usd_sales=True,
        )
        cls.provider = Provider.objects.create(name="Wizard Provider")

    def setUp(self):
        ok = self.client.login(username="mgr_return_wizard", password="pw12345")
        self.assertTrue(ok)

    def _create_usd_bill(self) -> Bill:
        return BillingSV.create_bill(
            actor=self.user,
            provider_id=self.provider.id,
            status="unpaid",
            paid_amount=Decimal("0"),
            items=[
                {
                    "product_id": self.product.id,
                    "unit_index": 1,
                    "qty_raw": "1",
                    "cost": "1.01",
                    "currency": "USD",
                }
            ],
            container=self.store,
            money_container_id=self.cash.id,
            settlement_currency="USD",
        )

    def _post_wizard(self, *, bill: Bill, paid_amount: str, ret_cost: str = "1.01"):
        item = bill.items.get()
        return self.client.post(
            reverse("billing_bill_return_wizard", kwargs={"bill_id": bill.public_id}),
            data={
                "items_ids": str(item.id),
                f"ret_store_{item.id}": "1",
                f"ret_wh1_{item.id}": "0",
                f"ret_wh2_{item.id}": "0",
                f"ret_cost_{item.id}": ret_cost,
                "return_status": "partial",
                "return_paid_amount": paid_amount,
                "valuation_mode": "HISTORICAL",
                "settlement_currency": "USD",
                "money_container_id": str(self.cash.id),
            },
        )

    def test_wizard_accepts_valid_2dp_paid_amount(self):
        bill = self._create_usd_bill()

        resp = self._post_wizard(bill=bill, paid_amount="1.00")
        self.assertEqual(resp.status_code, 302, resp.content.decode("utf-8"))
        self.assertIn(reverse("billing_returns_list"), resp["Location"])

        pret = ProviderReturn.objects.latest("id")
        self.assertEqual(pret.total_usd, Decimal("1.01"))

        entry = CreditorDebt.objects.get(
            source_app="billing",
            source_model="ProviderReturn",
            source_id=str(pret.id),
            currency_code="USD",
        )
        self.assertEqual(entry.total, Decimal("1.01"))
        self.assertEqual(entry.collected, Decimal("1.00"))
        self.assertEqual(entry.remaining, Decimal("0.01"))

    def test_wizard_rejects_paid_amount_more_than_2_decimals(self):
        bill = self._create_usd_bill()

        resp = self._post_wizard(bill=bill, paid_amount="1.015")
        self.assertEqual(resp.status_code, 200, resp.content.decode("utf-8"))
        self.assertContains(resp, "return_paid_amount supports at most 2 decimal digits")
        self.assertFalse(ProviderReturn.objects.exists())

    def test_wizard_rejects_return_cost_more_than_2_decimals(self):
        bill = self._create_usd_bill()

        resp = self._post_wizard(bill=bill, paid_amount="0.50", ret_cost="1.005")
        self.assertEqual(resp.status_code, 200, resp.content.decode("utf-8"))
        self.assertContains(resp, "return cost for")
        self.assertFalse(ProviderReturn.objects.exists())
