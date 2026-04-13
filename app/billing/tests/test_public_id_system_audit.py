from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import json
import time

from django.contrib.auth import get_user_model
from django.db import OperationalError, close_old_connections
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from accounts.models import AccountProfile
from billing.models import (
    BILL_PUBLIC_ID_SEQUENCE_KEY,
    PROVIDER_RETURN_PUBLIC_ID_SEQUENCE_KEY,
    Bill,
    Provider,
    ProviderReturn,
)
from core.models import PublicIdSequence
from debts.models import (
    DEBT_PUBLIC_ID_SEQUENCE_KEY,
    DebtCauseType,
    DebtDirection,
    DebtPublicIdSequence,
    DebtRecord,
    DebtStatus,
    OtherPartyType,
)
from financials import services as FinSV
from financials.models import Counterparty, CounterpartyType, Currency
from pos.models import SALES_BILL_PUBLIC_ID_SEQUENCE_KEY, SalesBill


def _new_debt(*, cause_id: str, public_id: str | None = None) -> DebtRecord:
    payload = {
        "direction": DebtDirection.PAYABLE,
        "cause_type": DebtCauseType.MANUAL,
        "cause_id": str(cause_id),
        "source_app": "debts",
        "other_party_type": OtherPartyType.OTHER,
        "other_party_id": f"party-{cause_id}",
        "total_syp": Decimal("100.000"),
        "total_usd": Decimal("0.000"),
        "remaining_syp": Decimal("100.000"),
        "remaining_usd": Decimal("0.000"),
        "status": DebtStatus.OPEN,
    }
    if public_id is not None:
        payload["public_id"] = public_id
    return DebtRecord.objects.create(**payload)


class PublicIdGenerationAuditTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        User = get_user_model()
        self.actor = User.objects.create_user(username="pubid_gen_mgr", password="pw12345")
        profile, _ = AccountProfile.objects.get_or_create(user=self.actor)
        profile.role = AccountProfile.Role.MANAGER
        profile.save(update_fields=["role"])
        self.provider = Provider.objects.create(name="Audit Provider")

    def _with_retry(self, fn):
        for attempt in range(20):
            try:
                close_old_connections()
                return fn()
            except OperationalError as exc:
                if "locked" not in str(exc).lower():
                    raise
                time.sleep(0.03 * (attempt + 1))
            finally:
                close_old_connections()
        raise AssertionError("db remained locked during concurrency audit")

    def test_prefix_and_sequence_start_for_all_targeted_types(self):
        b1 = Bill.objects.create(provider=self.provider)
        b2 = Bill.objects.create(provider=self.provider)
        self.assertEqual(b1.public_id, "PB-001")
        self.assertEqual(b2.public_id, "PB-002")

        r1 = ProviderReturn.objects.create(provider=self.provider)
        r2 = ProviderReturn.objects.create(provider=self.provider)
        self.assertEqual(r1.public_id, "PR-001")
        self.assertEqual(r2.public_id, "PR-002")

        s1 = SalesBill.objects.create(cashier=self.actor, customer_name="A")
        s2 = SalesBill.objects.create(cashier=self.actor, customer_name="B")
        self.assertEqual(s1.public_id, "PS-001")
        self.assertEqual(s2.public_id, "PS-002")

        d1 = _new_debt(cause_id="1")
        d2 = _new_debt(cause_id="2")
        self.assertEqual(d1.public_id, "D-001")
        self.assertEqual(d2.public_id, "D-002")

    def test_zero_padding_floor_and_large_number_growth(self):
        PublicIdSequence.objects.update_or_create(
            key=BILL_PUBLIC_ID_SEQUENCE_KEY,
            defaults={"next_value": 1000},
        )
        PublicIdSequence.objects.update_or_create(
            key=PROVIDER_RETURN_PUBLIC_ID_SEQUENCE_KEY,
            defaults={"next_value": 1234},
        )
        PublicIdSequence.objects.update_or_create(
            key=SALES_BILL_PUBLIC_ID_SEQUENCE_KEY,
            defaults={"next_value": 2005},
        )
        DebtPublicIdSequence.objects.update_or_create(
            key=DEBT_PUBLIC_ID_SEQUENCE_KEY,
            defaults={"next_value": 7777},
        )

        b = Bill.objects.create(provider=self.provider)
        r = ProviderReturn.objects.create(provider=self.provider)
        s = SalesBill.objects.create(cashier=self.actor, customer_name="C")
        d = _new_debt(cause_id="big")

        self.assertEqual(b.public_id, "PB-1000")
        self.assertEqual(r.public_id, "PR-1234")
        self.assertEqual(s.public_id, "PS-2005")
        self.assertEqual(d.public_id, "D-7777")

    def test_concurrent_pb_generation_has_no_duplicates(self):
        def create(idx: int) -> str:
            return self._with_retry(lambda: Bill.objects.create(provider=self.provider).public_id)

        with ThreadPoolExecutor(max_workers=6) as pool:
            ids = list(pool.map(create, range(12)))

        self.assertEqual(len(ids), 12)
        self.assertEqual(len(set(ids)), 12)
        self.assertTrue(all(pid.startswith("PB-") for pid in ids))

    def test_concurrent_pr_generation_has_no_duplicates(self):
        def create(idx: int) -> str:
            return self._with_retry(lambda: ProviderReturn.objects.create(provider=self.provider).public_id)

        with ThreadPoolExecutor(max_workers=6) as pool:
            ids = list(pool.map(create, range(12)))

        self.assertEqual(len(ids), 12)
        self.assertEqual(len(set(ids)), 12)
        self.assertTrue(all(pid.startswith("PR-") for pid in ids))

    def test_concurrent_ps_generation_has_no_duplicates(self):
        def create(idx: int) -> str:
            return self._with_retry(
                lambda: SalesBill.objects.create(cashier=self.actor, customer_name=f"C-{idx}").public_id
            )

        with ThreadPoolExecutor(max_workers=6) as pool:
            ids = list(pool.map(create, range(12)))

        self.assertEqual(len(ids), 12)
        self.assertEqual(len(set(ids)), 12)
        self.assertTrue(all(pid.startswith("PS-") for pid in ids))


class PublicIdSearchAndRoutingAuditTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.manager = User.objects.create_user(username="pubid_audit_mgr", password="pw12345")
        profile, _ = AccountProfile.objects.get_or_create(user=cls.manager)
        profile.role = AccountProfile.Role.MANAGER
        profile.save(update_fields=["role"])

        cls.provider = Provider.objects.create(name="Lookup Provider")
        cls.bill = Bill.objects.create(provider=cls.provider, created_by=cls.manager)
        cls.return_doc = ProviderReturn.objects.create(provider=cls.provider, created_by=cls.manager)
        cls.pos_bill = SalesBill.objects.create(cashier=cls.manager, customer_name="Lookup Customer")
        cls.debt = DebtRecord.objects.create(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id=cls.bill.public_id,
            source_app="billing",
            other_party_type=OtherPartyType.PROVIDER,
            other_party_id=str(cls.provider.id),
            provider=cls.provider,
            total_syp=Decimal("250.000"),
            total_usd=Decimal("0.000"),
            remaining_syp=Decimal("250.000"),
            remaining_usd=Decimal("0.000"),
            status=DebtStatus.OPEN,
        )

    def setUp(self):
        ok = self.client.login(username="pubid_audit_mgr", password="pw12345")
        self.assertTrue(ok)

    def test_bills_returns_debts_filters_accept_public_and_reject_numeric_or_malformed(self):
        bill_url = reverse("billing_api_bills_list")
        ret_url = reverse("billing_api_returns_list")
        debt_url = reverse("debts_api_central_list")

        ok_bill = self.client.get(bill_url, {"bill_id": self.bill.public_id})
        self.assertEqual(ok_bill.status_code, 200)
        self.assertEqual(len(ok_bill.json().get("items", [])), 1)

        ok_ret = self.client.get(ret_url, {"return_id": self.return_doc.public_id})
        self.assertEqual(ok_ret.status_code, 200)
        self.assertEqual(len(ok_ret.json().get("items", [])), 1)

        ok_debt = self.client.get(debt_url, {"debt_id": self.debt.public_id})
        self.assertEqual(ok_debt.status_code, 200)
        self.assertEqual(len(ok_debt.json().get("items", [])), 1)

        for token in [str(self.bill.id), "PB-1", "PB001", "001"]:
            with self.subTest(token=token):
                resp = self.client.get(bill_url, {"bill_id": token})
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(len(resp.json().get("items", [])), 0)

        for token in [str(self.return_doc.id), "PR-1", "PR001", "001"]:
            with self.subTest(token=token):
                resp = self.client.get(ret_url, {"return_id": token})
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(len(resp.json().get("items", [])), 0)

        for token in [str(self.debt.id), "D-1", "D001", "001"]:
            with self.subTest(token=token):
                resp = self.client.get(debt_url, {"debt_id": token})
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(len(resp.json().get("items", [])), 0)

    def test_policy_numeric_internal_refs_must_not_open_detail_pages(self):
        # Policy audit: these should be rejected because they are DB IDs.
        bill_numeric = self.client.get(
            reverse("billing_bill_view", kwargs={"bill_id": str(self.bill.id)})
        )
        self.assertEqual(bill_numeric.status_code, 404)

        return_numeric = self.client.get(
            reverse("billing_return_view", kwargs={"ret_id": str(self.return_doc.id)})
        )
        self.assertEqual(return_numeric.status_code, 404)

        pos_numeric = self.client.get(
            reverse("pos:pos_manager_bill_detail", kwargs={"bill_id": str(self.pos_bill.id)})
        )
        self.assertEqual(pos_numeric.status_code, 404)

    def test_policy_numeric_internal_refs_must_not_open_pos_apis(self):
        detail_numeric = self.client.get(
            reverse("pos:api_bill_detail", kwargs={"bill_id": str(self.pos_bill.id)})
        )
        self.assertEqual(detail_numeric.status_code, 404)

        delete_numeric = self.client.post(
            reverse("pos:api_bill_delete", kwargs={"bill_id": str(self.pos_bill.id)})
        )
        self.assertEqual(delete_numeric.status_code, 404)


class PublicIdApiLeakAuditTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.manager = User.objects.create_user(username="pubid_leak_mgr", password="pw12345")
        profile, _ = AccountProfile.objects.get_or_create(user=cls.manager)
        profile.role = AccountProfile.Role.MANAGER
        profile.save(update_fields=["role"])

        cls.provider = Provider.objects.create(name="Leak Provider")
        cls.bill = Bill.objects.create(provider=cls.provider, created_by=cls.manager)
        cls.debt = DebtRecord.objects.create(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id=cls.bill.public_id,
            source_app="billing",
            other_party_type=OtherPartyType.PROVIDER,
            other_party_id=str(cls.provider.id),
            provider=cls.provider,
            total_syp=Decimal("120.000"),
            total_usd=Decimal("0.000"),
            remaining_syp=Decimal("120.000"),
            remaining_usd=Decimal("0.000"),
            status=DebtStatus.OPEN,
        )

    def setUp(self):
        ok = self.client.login(username="pubid_leak_mgr", password="pw12345")
        self.assertTrue(ok)

    def test_debts_api_row_must_not_expose_internal_numeric_id_field(self):
        resp = self.client.get(reverse("debts_api_central_list"), {"debt_id": self.debt.public_id})
        self.assertEqual(resp.status_code, 200)
        row = resp.json()["items"][0]
        self.assertNotIn("id", row)
        self.assertEqual(row.get("debt_id"), self.debt.public_id)

    def test_pos_api_bill_save_must_not_return_internal_id_in_payload(self):
        resp = self.client.post(
            reverse("pos:api_bill_save"),
            data=json.dumps(
                {
                    "parked": True,
                    "pay_status": "none",
                    "rows": [],
                    "settlement_mode": "split",
                    "total_amount": "0",
                    "paid_amount": "0",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, resp.content.decode("utf-8"))
        payload = resp.json()
        self.assertTrue(payload.get("ok"), payload)
        self.assertNotIn("internal_id", payload.get("bill", {}))


class PublicIdFinancialTraceAuditTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.manager = User.objects.create_user(username="pubid_trace_mgr", password="pw12345")
        profile, _ = AccountProfile.objects.get_or_create(user=cls.manager)
        profile.role = AccountProfile.Role.MANAGER
        profile.save(update_fields=["role"])

        Currency.objects.get_or_create(
            code="SYP",
            defaults={"name": "Syrian Pound", "decimals": 0, "is_active": True},
        )
        Currency.objects.get_or_create(
            code="USD",
            defaults={"name": "US Dollar", "decimals": 2, "is_active": True},
        )

        cls.provider = Provider.objects.create(name="Trace Provider")
        cls.bill = Bill.objects.create(provider=cls.provider, created_by=cls.manager)

        cls.counterparty, _ = Counterparty.objects.get_or_create(
            type=CounterpartyType.SYSTEM,
            name="Trace Counterparty",
            defaults={"is_active": True},
        )
        FinSV.set_current_fx(actor=cls.manager, rate_syp_per_usd=Decimal("15000"))
        cls.receipt = FinSV.post_counterparty_bill_action_with_fx(
            actor=cls.manager,
            counterparty_id=cls.counterparty.id,
            totals_by_code={"SYP": Decimal("100")},
            settled_counterparty_by_code={"SYP": Decimal("40")},
            container_paid_by_code={},
            container_id=None,
            fx_syp_per_usd=Decimal("15000"),
            action_key=f"audit:Bill:{cls.bill.id}:trace",
            note="trace audit receipt",
            source_app="billing",
            source_model="Bill",
            source_id=str(cls.bill.id),  # legacy internal source persisted in old receipts
        )

    def setUp(self):
        ok = self.client.login(username="pubid_trace_mgr", password="pw12345")
        self.assertTrue(ok)

    def test_receipt_explorer_allows_public_id_and_rejects_numeric_lookup(self):
        url = reverse("financials:receipt_explorer")
        by_public = self.client.get(
            url,
            {"source_app": "billing", "source_model": "Bill", "source_id": self.bill.public_id},
        )
        self.assertEqual(by_public.status_code, 200)
        self.assertContains(by_public, self.receipt.serial)

        by_numeric = self.client.get(
            url,
            {"source_app": "billing", "source_model": "Bill", "source_id": str(self.bill.id)},
        )
        self.assertEqual(by_numeric.status_code, 200)
        self.assertNotContains(by_numeric, self.receipt.serial)

    def test_document_trace_allows_public_id_and_rejects_numeric_lookup(self):
        url = reverse("financials:document_trace")
        by_public = self.client.get(
            url,
            {"source_app": "billing", "source_model": "Bill", "source_id": self.bill.public_id},
        )
        self.assertEqual(by_public.status_code, 200)
        self.assertContains(by_public, self.receipt.serial)
        self.assertContains(by_public, self.bill.public_id)

        by_numeric = self.client.get(
            url,
            {"source_app": "billing", "source_model": "Bill", "source_id": str(self.bill.id)},
        )
        self.assertEqual(by_numeric.status_code, 200)
        self.assertNotContains(by_numeric, self.receipt.serial)

    def test_legacy_debt_public_id_stays_queryable(self):
        legacy = _new_debt(cause_id="legacy-compat", public_id="D-LEGACYABC123")
        resp = self.client.get(reverse("debts_api_central_list"), {"debt_id": legacy.public_id})
        self.assertEqual(resp.status_code, 200)
        items = resp.json().get("items", [])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["debt_id"], legacy.public_id)
