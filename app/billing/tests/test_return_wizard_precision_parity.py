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
from financials.models import (
    ContainerFeature,
    Currency,
    MoneyContainer,
    MoneyContainerCurrency,
    PostingTargetType,
    Receipt,
    ReceiptStatus,
)
from inventory.models import ProductMovement
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
        cls.product_syp = Product.objects.create(
            name="Wizard SYP Product",
            set=prod_set,
            unit_primary=UnitType.PIECE,
            allow_syp_purchasing=True,
            allow_usd_purchasing=True,
            allow_syp_sales=True,
            allow_usd_sales=True,
        )
        cls.product_usd = Product.objects.create(
            name="Wizard Mixed USD Product",
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

    def _create_mixed_bill_for_full_paid(self) -> Bill:
        FinSV.set_current_fx(actor=self.user, rate_syp_per_usd=Decimal("100"))
        return BillingSV.create_bill(
            actor=self.user,
            provider_id=self.provider.id,
            status="unpaid",
            paid_amount=Decimal("0"),
            items=[
                {
                    "product_id": self.product_syp.id,
                    "unit_index": 1,
                    "qty_raw": "1",
                    "cost": "500.00",
                    "currency": "SYP",
                },
                {
                    "product_id": self.product_usd.id,
                    "unit_index": 1,
                    "qty_raw": "1",
                    "cost": "1.00",
                    "currency": "USD",
                },
            ],
            container=self.store,
            money_container_id=self.cash.id,
            settlement_currency="SYP",
        )

    def _create_syp_only_bill_for_full_paid_fx15000(self) -> Bill:
        FinSV.set_current_fx(actor=self.user, rate_syp_per_usd=Decimal("15000"))
        return BillingSV.create_bill(
            actor=self.user,
            provider_id=self.provider.id,
            status="unpaid",
            paid_amount=Decimal("0"),
            items=[
                {
                    "product_id": self.product_syp.id,
                    "unit_index": 1,
                    "qty_raw": "1",
                    "cost": "10000.00",
                    "currency": "SYP",
                },
            ],
            container=self.store,
            money_container_id=self.cash.id,
            settlement_currency="SYP",
        )

    def _post_wizard(
        self,
        *,
        bill: Bill,
        paid_amount: str,
        ret_cost: str = "1.01",
        ret_currency: str = "USD",
        payment_method: str | None = None,
        pay_syp: str | None = None,
        pay_usd: str | None = None,
    ):
        item = bill.items.get()
        payload = {
            "items_ids": str(item.id),
            f"ret_store_{item.id}": "1",
            f"ret_wh1_{item.id}": "0",
            f"ret_wh2_{item.id}": "0",
            f"ret_cost_{item.id}": ret_cost,
            f"ret_currency_{item.id}": ret_currency,
            "return_status": "partial",
            "return_paid_amount": paid_amount,
            "valuation_mode": "HISTORICAL",
            "settlement_currency": "USD",
            "money_container_id": str(self.cash.id),
        }
        if payment_method is not None:
            payload["return_payment_method"] = payment_method
        if pay_syp is not None:
            payload["return_pay_syp"] = pay_syp
        if pay_usd is not None:
            payload["return_pay_usd"] = pay_usd
        return self.client.post(
            reverse("billing_bill_return_wizard", kwargs={"bill_id": bill.public_id}),
            data=payload,
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

    def test_wizard_preview_totals_match_saved_return_debt_and_receipt(self):
        bill = self._create_usd_bill()

        resp = self._post_wizard(
            bill=bill,
            paid_amount="1.00",
            ret_cost="1.75",
            ret_currency="USD",
            payment_method="usd_only",
            pay_syp="0",
            pay_usd="1.00",
        )
        self.assertEqual(resp.status_code, 302, resp.content.decode("utf-8"))

        pret = ProviderReturn.objects.latest("id")
        self.assertEqual(pret.total_usd, Decimal("1.75"))
        self.assertEqual(pret.total_syp, Decimal("0.00"))

        entry = CreditorDebt.objects.get(
            source_app="billing",
            source_model="ProviderReturn",
            source_id=str(pret.id),
            currency_code="USD",
        )
        self.assertEqual(entry.total, Decimal("1.75"))
        self.assertEqual(entry.collected, Decimal("1.00"))
        self.assertEqual(entry.remaining, Decimal("0.75"))

        receipt = Receipt.objects.get(
            source_app="billing",
            source_model="ProviderReturn",
            source_id=str(pret.id),
            status=ReceiptStatus.POSTED,
        )
        container_usd_sum = Decimal("0.00")
        for ln in receipt.lines.select_related("currency").all():
            if ln.target_type != PostingTargetType.CONTAINER:
                continue
            if (ln.currency.code or "").upper() == "USD":
                container_usd_sum += Decimal(str(ln.amount or "0"))
        self.assertEqual(container_usd_sum, Decimal("1.00"))

    def test_wizard_partial_mixed_template_normalizes_one_side_payload_method(self):
        bill = self._create_usd_bill()
        item = bill.items.get()
        resp = self.client.get(
            reverse("billing_bill_return_wizard", kwargs={"bill_id": bill.public_id}),
            {"items": str(item.id)},
            follow=True,
        )
        self.assertEqual(resp.status_code, 200, resp.content.decode("utf-8"))
        html = resp.content.decode("utf-8")
        self.assertIn('if (selectedMethod === "mixed") {', html)
        self.assertIn('method = "syp_only";', html)
        self.assertIn('method = "usd_only";', html)
        self.assertIn('method = "mixed";', html)
        self.assertNotIn("في الدفع المختلط الجزئي يجب إدخال مبلغين أكبر من الصفر.", html)

    def test_wizard_uses_same_currency_settlement_cap_in_submit_validation(self):
        bill = self._create_usd_bill()
        item = bill.items.get()
        resp = self.client.get(
            reverse("billing_bill_return_wizard", kwargs={"bill_id": bill.public_id}),
            {"items": str(item.id)},
            follow=True,
        )
        self.assertEqual(resp.status_code, 200, resp.content.decode("utf-8"))
        html = resp.content.decode("utf-8")
        self.assertIn(
            "const paidSettlementComponent = paidComponentInSettlementCurrency(draft, totals.settlementCurrency);",
            html,
        )
        self.assertIn("if (settleAmount > paidSettlementComponent) {", html)
        self.assertNotIn("if (settleAmount > draft.paidSettlement) {", html)

    def test_wizard_has_duplicate_submit_lock_guard(self):
        bill = self._create_usd_bill()
        item = bill.items.get()
        resp = self.client.get(
            reverse("billing_bill_return_wizard", kwargs={"bill_id": bill.public_id}),
            {"items": str(item.id)},
            follow=True,
        )
        self.assertEqual(resp.status_code, 200, resp.content.decode("utf-8"))
        html = resp.content.decode("utf-8")
        self.assertIn("let submitInFlight = false;", html)
        self.assertIn("if (submitInFlight) return;", html)
        self.assertIn("if (submitInFlight) {", html)
        self.assertIn("setSubmitInFlight(true);", html)

    def test_wizard_mixed_full_payment_template_keeps_inputs_editable_and_bidirectional(self):
        bill = self._create_mixed_bill_for_full_paid()
        item_ids = ",".join(str(i.id) for i in bill.items.order_by("id"))
        resp = self.client.get(
            reverse("billing_bill_return_wizard", kwargs={"bill_id": bill.public_id}),
            {"items": item_ids},
            follow=True,
        )
        self.assertEqual(resp.status_code, 200, resp.content.decode("utf-8"))
        html = resp.content.decode("utf-8")

        self.assertIn('id="retPayMixedSyp"', html)
        self.assertIn('id="retPayMixedUsd"', html)
        self.assertNotRegex(html, r'id="retPayMixedSyp"[^>]*readonly')
        self.assertNotRegex(html, r'id="retPayMixedUsd"[^>]*readonly')

        self.assertIn('input.readOnly = (status === "paid" && method !== "mixed");', html)
        self.assertIn('if (payMixedSypInput) payMixedSypInput.readOnly = (status === "unpaid");', html)
        self.assertIn('if (payMixedUsdInput) payMixedUsdInput.readOnly = (status === "unpaid");', html)

        self.assertIn('syncMixedFullFrom(payState.mixedLastEdited === "usd" ? "usd" : "syp", totals);', html)
        self.assertNotIn('syncMixedFullFrom("syp", totals);', html)
        self.assertNotIn('syncMixedFullFrom("usd", totals);', html)
        self.assertIn("function scheduleSyncPaymentUI()", html)
        self.assertIn('input.addEventListener("input", scheduleSyncPaymentUI);', html)
        self.assertIn('payState.mixedLastEdited = "syp";', html)
        self.assertIn('payState.mixedLastEdited = "usd";', html)
        self.assertIn("scheduleSyncPaymentUI();", html)
        self.assertIn("function maxSourceForSettlement(", html)
        self.assertIn('const maxUsd = maxSourceForSettlement(', html)
        self.assertIn('const maxSyp = maxSourceForSettlement(', html)
        self.assertIn('if (status === "paid" && paidSettlement !== settlementTotal) {', html)
        self.assertNotIn("|| e.target === payMixedSypInput", html)
        self.assertNotIn("|| e.target === payMixedUsdInput", html)
        self.assertIn('payMethodSeparate.disabled = (status === "partial");', html)
        self.assertIn('if (status === "partial" && method === "separate") {', html)
        self.assertIn('debtToggle.addEventListener("change", syncPaymentUI);', html)
        self.assertIn('if (e.ctrlKey) {', html)
        self.assertIn('requestSubmitByShortcut();', html)

    def test_wizard_paid_mixed_post_preserves_payload_and_settles_exact_total(self):
        bill = self._create_mixed_bill_for_full_paid()
        items = list(bill.items.order_by("id"))
        self.assertEqual(len(items), 2)

        payload = {
            "items_ids": ",".join(str(i.id) for i in items),
            "return_status": "paid",
            "return_paid_amount": "600.00",
            "return_payment_method": "mixed",
            "return_pay_syp": "400.00",
            "return_pay_usd": "2.00",
            "settlement_currency": "SYP",
            "money_container_id": str(self.cash.id),
            "valuation_mode": "CURRENT_FX",
        }
        for item in items:
            payload[f"ret_store_{item.id}"] = "1"
            payload[f"ret_wh1_{item.id}"] = "0"
            payload[f"ret_wh2_{item.id}"] = "0"
            payload[f"ret_cost_{item.id}"] = ""
            payload[f"ret_currency_{item.id}"] = ""

        resp = self.client.post(
            reverse("billing_bill_return_wizard", kwargs={"bill_id": bill.public_id}),
            data=payload,
        )
        self.assertEqual(resp.status_code, 302, resp.content.decode("utf-8"))

        pret = ProviderReturn.objects.latest("id")
        self.assertEqual(pret.settlement_currency, "SYP")
        self.assertEqual(pret.total_syp, Decimal("500.00"))
        self.assertEqual(pret.total_usd, Decimal("1.00"))

        debt_syp = CreditorDebt.objects.get(
            source_app="billing",
            source_model="ProviderReturn",
            source_id=str(pret.id),
            currency_code="SYP",
        )
        debt_usd = CreditorDebt.objects.get(
            source_app="billing",
            source_model="ProviderReturn",
            source_id=str(pret.id),
            currency_code="USD",
        )
        self.assertEqual(debt_syp.remaining, Decimal("0.00"))
        self.assertEqual(debt_usd.remaining, Decimal("0.00"))

        receipt = Receipt.objects.get(
            source_app="billing",
            source_model="ProviderReturn",
            source_id=str(pret.id),
            status=ReceiptStatus.POSTED,
        )
        container_syp = Decimal("0.00")
        container_usd = Decimal("0.00")
        for ln in receipt.lines.select_related("currency").all():
            if ln.target_type != PostingTargetType.CONTAINER:
                continue
            cur = (ln.currency.code or "").upper()
            if cur == "SYP":
                container_syp += Decimal(str(ln.amount or "0"))
            if cur == "USD":
                container_usd += Decimal(str(ln.amount or "0"))
        self.assertEqual(container_syp, Decimal("400.00"))
        self.assertEqual(container_usd, Decimal("2.00"))

    def test_wizard_paid_mixed_post_fx15000_syp_authoritative_keeps_exact_settlement(self):
        bill = self._create_syp_only_bill_for_full_paid_fx15000()
        item = bill.items.get()
        payload = {
            "items_ids": str(item.id),
            "return_status": "paid",
            "return_paid_amount": "10000.00",
            "return_payment_method": "mixed",
            "return_pay_syp": "2500.00",
            "return_pay_usd": "0.50",
            "settlement_currency": "SYP",
            "money_container_id": str(self.cash.id),
            "valuation_mode": "CURRENT_FX",
            f"ret_store_{item.id}": "1",
            f"ret_wh1_{item.id}": "0",
            f"ret_wh2_{item.id}": "0",
            f"ret_cost_{item.id}": "",
            f"ret_currency_{item.id}": "",
        }
        resp = self.client.post(
            reverse("billing_bill_return_wizard", kwargs={"bill_id": bill.public_id}),
            data=payload,
        )
        self.assertEqual(resp.status_code, 302, resp.content.decode("utf-8"))

        pret = ProviderReturn.objects.latest("id")
        self.assertEqual(pret.total_syp, Decimal("10000.00"))
        self.assertEqual(pret.total_usd, Decimal("0.00"))

        debt = CreditorDebt.objects.get(
            source_app="billing",
            source_model="ProviderReturn",
            source_id=str(pret.id),
            currency_code="SYP",
        )
        self.assertEqual(debt.remaining, Decimal("0.00"))

        receipt = Receipt.objects.get(
            source_app="billing",
            source_model="ProviderReturn",
            source_id=str(pret.id),
            status=ReceiptStatus.POSTED,
        )
        container_syp = Decimal("0.00")
        container_usd = Decimal("0.00")
        for ln in receipt.lines.select_related("currency").all():
            if ln.target_type != PostingTargetType.CONTAINER:
                continue
            cur = (ln.currency.code or "").upper()
            if cur == "SYP":
                container_syp += Decimal(str(ln.amount or "0"))
            if cur == "USD":
                container_usd += Decimal(str(ln.amount or "0"))
        self.assertEqual(container_syp, Decimal("2500.00"))
        self.assertEqual(container_usd, Decimal("0.50"))

    def test_wizard_duplicate_post_is_idempotent_backend(self):
        bill = self._create_usd_bill()

        first = self._post_wizard(
            bill=bill,
            paid_amount="1.00",
            ret_cost="1.01",
            ret_currency="USD",
            payment_method="usd_only",
            pay_syp="0",
            pay_usd="1.00",
        )
        second = self._post_wizard(
            bill=bill,
            paid_amount="1.00",
            ret_cost="1.01",
            ret_currency="USD",
            payment_method="usd_only",
            pay_syp="0",
            pay_usd="1.00",
        )

        self.assertEqual(first.status_code, 302, first.content.decode("utf-8"))
        self.assertEqual(second.status_code, 302, second.content.decode("utf-8"))
        self.assertEqual(ProviderReturn.objects.count(), 1)

        pret = ProviderReturn.objects.get()
        self.assertEqual(
            ProductMovement.objects.filter(
                source_app="billing",
                source_model="ProviderReturn",
            ).values("source_id").distinct().count(),
            1,
        )
        self.assertGreaterEqual(
            ProductMovement.objects.filter(
                source_app="billing",
                source_model="ProviderReturn",
                source_id=str(pret.id),
            ).count(),
            1,
        )
        self.assertEqual(
            CreditorDebt.objects.filter(
                source_app="billing",
                source_model="ProviderReturn",
            ).values("source_id").distinct().count(),
            1,
        )
        self.assertEqual(
            Receipt.objects.filter(
                source_app="billing",
                source_model="ProviderReturn",
                source_id=str(pret.id),
                status=ReceiptStatus.POSTED,
            ).count(),
            1,
        )
