from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import AccountProfile
from financials.models import (
    Currency,
    MoneyContainer,
    MoneyContainerCurrency,
    Receipt,
    ReceiptKind,
    ReceiptStatus,
    PostingLine,
    PostingTargetType,
)
from financials import services as FSV

D = Decimal


class FinancialsContainerCreateTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.actor = User.objects.create_user(username="mgr", password="123")

        # Ensure profile exists + set role to MANAGER (so role_required passes)
        prof, _ = AccountProfile.objects.get_or_create(user=cls.actor)
        prof.role = AccountProfile.Role.MANAGER
        prof.save(update_fields=["role"])

        # Currencies: use get_or_create so multiple test modules don't collide
        cls.syp, _ = Currency.objects.get_or_create(
            code="SYP",
            defaults={"name": "Syrian Pound", "decimals": 0, "is_active": True},
        )
        cls.usd, _ = Currency.objects.get_or_create(
            code="USD",
            defaults={"name": "US Dollar", "decimals": 2, "is_active": True},
        )

        FSV.set_current_fx(actor=cls.actor, rate_syp_per_usd=Decimal("15000"))

    def setUp(self):
        ok = self.client.login(username="mgr", password="123")
        self.assertTrue(ok)

    def _unique_name(self, base: str) -> str:
        return f"{base}-{uuid4().hex[:8]}"

    def _post_create(
        self,
        *,
        name: str,
        container_type: str,
        currencies: list[int],
        opening: dict[str, str] | None = None,
    ):
        """
        Posts to container_create.

        IMPORTANT:
        - Your MoneyContainerForm currently requires ref_code (even though you generate it in backend).
          So we MUST send a placeholder ref_code to satisfy form validation.
        - Your opening form fields must match your build_opening_formset prefixes: cur_SYP / cur_USD
        """
        url = reverse("financials:container_create")
        opening = opening or {}

        data = {
            # ---- form fields ----
            "ref_code": "TEMP",  # placeholder: backend will override with alloc_ref_code
            "name": name,
            "container_type": container_type,
            "is_active": "on",
            "currencies": currencies,
            "note": "",
            "features": [],
            "allowed_users": [],

            # ---- opening formset (must match your template hidden fields) ----
            "cur_SYP-currency_code": "SYP",
            "cur_SYP-code": "SYP",
            "cur_SYP-amount": opening.get("SYP", "0"),

            "cur_USD-currency_code": "USD",
            "cur_USD-code": "USD",
            "cur_USD-amount": opening.get("USD", "0"),
        }

        return self.client.post(url, data=data, follow=False)

    def test_01_create_container_creates_enabled_currency_rows(self):
        name = self._unique_name("صندوق")
        resp = self._post_create(
            name=name,
            container_type=MoneyContainer.ContainerType.DRAWER,
            currencies=[self.syp.id, self.usd.id],
            opening={"SYP": "0", "USD": "0"},
        )
        self.assertEqual(resp.status_code, 302)

        c = MoneyContainer.objects.get(name=name)
        self.assertTrue(c.ref_code)  # must be allocated

        states = {
            s.currency.code: s.is_enabled
            for s in MoneyContainerCurrency.objects.filter(container=c)
        }
        self.assertEqual(states.get("SYP"), True)
        self.assertEqual(states.get("USD"), True)

        # No opening receipt if all amounts are zero
        self.assertFalse(
            Receipt.objects.filter(
                kind=ReceiptKind.OPENING_BALANCE,
                source_app="financials",
                source_model="MoneyContainer",
                source_id=str(c.id),
            ).exists()
        )

    def test_02_opening_balance_posts_single_receipt_and_lines(self):
        name = self._unique_name("صندوق")
        resp = self._post_create(
            name=name,
            container_type=MoneyContainer.ContainerType.DRAWER,
            currencies=[self.syp.id, self.usd.id],
            opening={"SYP": "10000", "USD": "2.5"},
        )
        self.assertEqual(resp.status_code, 302)

        c = MoneyContainer.objects.get(name=name)

        r = Receipt.objects.get(
            kind=ReceiptKind.OPENING_BALANCE,
            source_app="financials",
            source_model="MoneyContainer",
            source_id=str(c.id),
        )
        self.assertEqual(r.status, ReceiptStatus.POSTED)

        lines = list(PostingLine.objects.filter(receipt=r).order_by("id"))
        self.assertEqual(len(lines), 2)
        self.assertTrue(all(ln.target_type == PostingTargetType.CONTAINER for ln in lines))
        self.assertTrue(all(ln.container_id == c.id for ln in lines))

        syp_ln = next(ln for ln in lines if ln.currency.code == "SYP")
        usd_ln = next(ln for ln in lines if ln.currency.code == "USD")
        self.assertEqual(syp_ln.amount, D("10000"))
        self.assertEqual(usd_ln.amount, D("2.50"))

        b = FSV.container_balance(container_id=c.id)
        self.assertEqual(b.get("SYP"), D("10000"))
        self.assertEqual(b.get("USD"), D("2.50"))

    def test_03_disabled_currency_ignores_opening_amount(self):
        name = self._unique_name("صندوق")
        resp = self._post_create(
            name=name,
            container_type=MoneyContainer.ContainerType.DRAWER,
            currencies=[self.syp.id],  # USD unchecked
            opening={"SYP": "100", "USD": "9999.99"},
        )
        self.assertEqual(resp.status_code, 302)

        c = MoneyContainer.objects.get(name=name)

        states = {
            s.currency.code: s.is_enabled
            for s in MoneyContainerCurrency.objects.filter(container=c)
        }
        self.assertEqual(states.get("SYP"), True)
        self.assertEqual(states.get("USD"), False)

        r = Receipt.objects.get(
            kind=ReceiptKind.OPENING_BALANCE,
            source_app="financials",
            source_model="MoneyContainer",
            source_id=str(c.id),
        )
        lines = list(PostingLine.objects.filter(receipt=r))
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].currency.code, "SYP")
        self.assertEqual(lines[0].amount, D("100"))

        b = FSV.container_balance(container_id=c.id)
        self.assertEqual(b.get("SYP"), D("100"))
        self.assertTrue("USD" not in b or b["USD"] == D("0"))

    def test_04_duplicate_name_shows_form_error(self):
        # Create an existing container to collide with
        dup_name = self._unique_name("صندوق-dup")
        MoneyContainer.objects.create(name=dup_name, created_by=self.actor)

        resp = self._post_create(
            name=dup_name,
            container_type=MoneyContainer.ContainerType.DRAWER,
            currencies=[self.syp.id],
            opening={"SYP": "0", "USD": "0"},
        )

        # Invalid form => it re-renders => 200
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(MoneyContainer.objects.filter(name=dup_name).count(), 1)

        # Must contain name error
        form = resp.context["form"]
        self.assertTrue(form.errors.get("name"))
