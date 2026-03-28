# billing/tests/test_multicurrency_smoke.py
from __future__ import annotations

import json
from decimal import Decimal
from typing import Dict, Any

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import AccountProfile
from billing import services as BillingSV
from billing.models import Bill, BillItem, Provider
from catalog.models import Product, ProductCollection, ProductSet, UnitType
from debts.models import DebtorDebt, DebtorPayment
from financials.models import ContainerFeature, Currency, MoneyContainer, MoneyContainerCurrency
from financials import services as FinSV
from inventory.models import ProductMovement, DEC0, q3
from stock.models import ProductContainer, StockFifoLayer


class MultiCurrencyPurchaseBillSmokeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.actor = User.objects.create_user(username="mgr", password="123")

        prof, _ = AccountProfile.objects.get_or_create(user=cls.actor)
        prof.role = AccountProfile.Role.MANAGER
        prof.save(update_fields=["role"])

        cls.syp, _ = Currency.objects.get_or_create(
            code="SYP",
            defaults={"name": "Syrian Pound", "decimals": 0, "is_active": True},
        )
        cls.usd, _ = Currency.objects.get_or_create(
            code="USD",
            defaults={"name": "US Dollar", "decimals": 2, "is_active": True},
        )

        cls.store = ProductContainer.objects.filter(code="store").first()
        if not cls.store:
            cls.store = ProductContainer.objects.create(
                name="Main Store",
                code="store",
                is_store=True,
                is_active=True,
            )

        cls.cash = MoneyContainer.objects.create(
            name="Test Cash Drawer",
            container_type=MoneyContainer.ContainerType.DRAWER,
            is_active=True,
            created_by=cls.actor,
        )
        feature, _ = ContainerFeature.objects.get_or_create(
            code="purchase_bills",
            defaults={"name": "Purchase Bills", "is_active": True},
        )
        if not feature.is_active:
            feature.is_active = True
            feature.save(update_fields=["is_active"])
        cls.cash.features.add(feature)
        MoneyContainerCurrency.objects.get_or_create(
            container=cls.cash,
            currency=cls.syp,
            defaults={"is_enabled": True},
        )
        MoneyContainerCurrency.objects.get_or_create(
            container=cls.cash,
            currency=cls.usd,
            defaults={"is_enabled": True},
        )

        cls.provider = Provider.objects.create(name="Test Provider")

        col = ProductCollection.objects.create(name="Test Collection")
        cls.prod_set = ProductSet.objects.create(collection=col, name="Test Set")

        FinSV.set_current_fx(actor=cls.actor, rate_syp_per_usd=Decimal("15000"))

    def setUp(self):
        ok = self.client.login(username="mgr", password="123")
        self.assertTrue(ok)

    def _create_product(
        self,
        *,
        name: str,
        allow_syp: bool,
        allow_usd: bool,
        default_currency: str | None,
        cost: str,
        price: str,
    ) -> Product:
        return Product.objects.create(
            name=name,
            set=self.prod_set,
            unit_primary=UnitType.PIECE,
            allow_syp_purchasing=allow_syp,
            allow_syp_sales=allow_syp,
            allow_usd_purchasing=allow_usd,
            allow_usd_sales=allow_usd,
            default_purchase_currency=default_currency,
            default_sale_currency=default_currency,
            default_cost_syp=Decimal(cost) if allow_syp else Decimal("0"),
            default_cost_usd=Decimal(cost) if allow_usd else Decimal("0"),
            default_price_syp=Decimal(price) if allow_syp else Decimal("0"),
            default_price_usd=Decimal(price) if allow_usd else Decimal("0"),
        )

    def _post_bill(self, *, items: list[Dict[str, Any]]):
        url = reverse("billing_api_bill_save")
        payload = {
            "provider": {"id": self.provider.id},
            "container": "store",
            "money_container_id": self.cash.id,
            "currency_code": "SYP",
            "items": items,
            "pay": {"status": "unpaid", "paid_amount": "0"},
        }
        resp = self.client.post(
            url,
            data=json.dumps(payload),
            content_type="application/json",
        )
        return resp

    def test_multicurrency_purchase_bill_and_payments(self):
        prod_a = self._create_product(
            name="Prod SYP",
            allow_syp=True,
            allow_usd=False,
            default_currency=None,
            cost="1000",
            price="1500",
        )
        prod_b = self._create_product(
            name="Prod USD",
            allow_syp=False,
            allow_usd=True,
            default_currency=None,
            cost="2",
            price="3",
        )
        prod_c = self._create_product(
            name="Prod BOTH",
            allow_syp=True,
            allow_usd=True,
            default_currency=None,
            cost="2000",
            price="3000",
        )

        items = [
            {
                "product_id": prod_a.id,
                "unit_index": 1,
                "qty_raw": "2",
                "cost": "1000",
                "price": "1500",
                "price_syp": "1500",
                "price_usd": "",
                "currency": "SYP",
            },
            {
                "product_id": prod_b.id,
                "unit_index": 1,
                "qty_raw": "3",
                "cost": "5",
                "price": "6",
                "price_syp": "",
                "price_usd": "6",
                "currency": "USD",
            },
            {
                "product_id": prod_c.id,
                "unit_index": 1,
                "qty_raw": "1",
                "cost": "2000",
                "price": "3000",
                "price_syp": "3000",
                "price_usd": "",
                "currency": "",
            },
        ]

        resp = self._post_bill(items=items)
        self.assertEqual(resp.status_code, 200, resp.content.decode("utf-8"))
        data = resp.json()
        self.assertTrue(data.get("ok"), data)

        bill_id = data["bill"]["id"]
        bill = Bill.objects.get(pk=bill_id)

        self.assertGreater(bill.total_syp, DEC0)
        self.assertGreater(bill.total_usd, DEC0)

        expected_syp = q3(Decimal("2") * Decimal("1000") + Decimal("1") * Decimal("2000"))
        expected_usd = q3(Decimal("3") * Decimal("5"))
        self.assertEqual(q3(bill.total_syp), expected_syp)
        self.assertEqual(q3(bill.total_usd), expected_usd)

        items_qs = BillItem.objects.filter(bill=bill)
        cur_map = {it.product_id: it.currency for it in items_qs}
        self.assertEqual(cur_map.get(prod_a.id), "SYP")
        self.assertEqual(cur_map.get(prod_b.id), "USD")
        self.assertEqual(cur_map.get(prod_c.id), "SYP")

        item_ids = [str(it.id) for it in items_qs]

        mv_count = ProductMovement.objects.filter(
            source_app="billing",
            source_model="BillItem",
            source_id__in=item_ids,
        ).count()
        self.assertGreater(mv_count, 0)

        fifo = StockFifoLayer.objects.filter(
            source_app="billing",
            source_model="BillItem",
            source_id__in=item_ids,
        )
        self.assertGreater(fifo.count(), 0)
        fifo_map = {str(f.source_id): f.cost_currency for f in fifo}
        for it in items_qs:
            self.assertEqual(fifo_map.get(str(it.id)), it.currency)

        # Debts per currency
        syp_entry = DebtorDebt.objects.get(
            source_app="billing",
            source_model="Bill",
            source_id=str(bill.id),
            currency_code="SYP",
        )
        usd_entry = DebtorDebt.objects.get(
            source_app="billing",
            source_model="Bill",
            source_id=str(bill.id),
            currency_code="USD",
        )
        self.assertEqual(q3(syp_entry.total), q3(bill.total_syp))
        self.assertEqual(q3(usd_entry.total), q3(bill.total_usd))

        # Pay partial via existing endpoint
        url_syp = reverse("debts_api_entry_pay_batch", kwargs={"direction": "debtor", "entry_id": syp_entry.id})
        url_usd = reverse("debts_api_entry_pay_batch", kwargs={"direction": "debtor", "entry_id": usd_entry.id})

        r1 = self.client.post(url_syp, data={"amount": "1000", "money_container_id": self.cash.id, "currency_code": "SYP"})
        r2 = self.client.post(url_usd, data={"amount": "5", "money_container_id": self.cash.id, "currency_code": "USD"})
        self.assertEqual(r1.status_code, 200, r1.content.decode("utf-8"))
        self.assertEqual(r2.status_code, 200, r2.content.decode("utf-8"))
        self.assertTrue(r1.json().get("ok"))
        self.assertTrue(r2.json().get("ok"))

        syp_entry.refresh_from_db()
        usd_entry.refresh_from_db()
        self.assertEqual(q3(syp_entry.paid_amount), q3(Decimal("1000")))
        self.assertEqual(q3(usd_entry.paid_amount), q3(Decimal("5")))

        # Pay remaining (full) via same endpoint
        rem_syp = q3(syp_entry.remaining)
        rem_usd = q3(usd_entry.remaining)
        r3 = self.client.post(url_syp, data={"amount": str(rem_syp), "money_container_id": self.cash.id, "currency_code": "SYP"})
        r4 = self.client.post(url_usd, data={"amount": str(rem_usd), "money_container_id": self.cash.id, "currency_code": "USD"})
        self.assertEqual(r3.status_code, 200, r3.content.decode("utf-8"))
        self.assertEqual(r4.status_code, 200, r4.content.decode("utf-8"))
        self.assertTrue(r3.json().get("ok"))
        self.assertTrue(r4.json().get("ok"))

        syp_entry.refresh_from_db()
        usd_entry.refresh_from_db()
        self.assertEqual(q3(syp_entry.remaining), DEC0)
        self.assertEqual(q3(usd_entry.remaining), DEC0)

    def test_syp_partial_payment_uses_currency_precision(self):
        prod = self._create_product(
            name="SYP precision",
            allow_syp=True,
            allow_usd=False,
            default_currency=None,
            cost="2.4",
            price="3",
        )

        bill = BillingSV.create_bill(
            actor=self.actor,
            provider_id=self.provider.id,
            status="partial",
            paid_amount=Decimal("1.4"),
            items=[
                {
                    "product_id": prod.id,
                    "unit_index": 1,
                    "qty_raw": "1",
                    "cost": "2.4",
                    "currency": "SYP",
                }
            ],
            container=self.store,
            money_container_id=self.cash.id,
            settlement_currency="SYP",
        )

        self.assertEqual(bill.total_syp, Decimal("2"))

        entry = DebtorDebt.objects.get(
            source_app="billing",
            source_model="Bill",
            source_id=str(bill.id),
            currency_code="SYP",
        )
        self.assertEqual(entry.total, Decimal("2"))
        self.assertEqual(entry.paid_amount, Decimal("1"))
        self.assertEqual(entry.remaining, Decimal("1"))

        payment = DebtorPayment.objects.get(entry=entry)
        self.assertEqual(payment.amount, Decimal("1"))

        bal = FinSV.container_balance(container_id=self.cash.id)
        self.assertEqual(bal.get("SYP"), Decimal("-1"))

    def test_usd_partial_payment_uses_currency_precision(self):
        prod = self._create_product(
            name="USD precision",
            allow_syp=False,
            allow_usd=True,
            default_currency=None,
            cost="1.005",
            price="2",
        )

        bill = BillingSV.create_bill(
            actor=self.actor,
            provider_id=self.provider.id,
            status="partial",
            paid_amount=Decimal("0.335"),
            items=[
                {
                    "product_id": prod.id,
                    "unit_index": 1,
                    "qty_raw": "3",
                    "cost": "1.005",
                    "currency": "USD",
                }
            ],
            container=self.store,
            money_container_id=self.cash.id,
            settlement_currency="USD",
        )

        self.assertEqual(bill.total_usd, Decimal("3.02"))

        entry = DebtorDebt.objects.get(
            source_app="billing",
            source_model="Bill",
            source_id=str(bill.id),
            currency_code="USD",
        )
        self.assertEqual(entry.total, Decimal("3.02"))
        self.assertEqual(entry.paid_amount, Decimal("0.34"))
        self.assertEqual(entry.remaining, Decimal("2.68"))

        payment = DebtorPayment.objects.get(entry=entry)
        self.assertEqual(payment.amount, Decimal("0.34"))

        bal = FinSV.container_balance(container_id=self.cash.id)
        self.assertEqual(bal.get("USD"), Decimal("-0.34"))


