from __future__ import annotations

import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.serializers.json import DjangoJSONEncoder
from django.test import TestCase

from accounts.models import AccountProfile
from billing.models import Bill, Provider
from debts.models import (
    CreditorDebt,
    CreditorReceipt,
    DebtCauseType,
    DebtDirection,
    DebtRecord,
    DebtSettlement,
    DebtStatus,
    DebtorDebt,
    DebtorPayment,
    OtherPartyType,
    PartyType,
)
from debts.provider_account_allocator import simulate_provider_account_allocation
from financials.models import Receipt


class ProviderAccountAllocatorApiPhase4BTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.manager = user_model.objects.create_user(
            username="provider_alloc_preview_mgr",
            password="123456",
        )
        AccountProfile.objects.create(user=cls.manager, role=AccountProfile.Role.MANAGER)

        cls.cashier = user_model.objects.create_user(
            username="provider_alloc_preview_cashier",
            password="123456",
        )
        AccountProfile.objects.create(user=cls.cashier, role=AccountProfile.Role.CASHIER)

    def setUp(self):
        self.client.force_login(self.manager)
        self.provider = Provider.objects.create(name=f"Phase4B Provider {self._testMethodName}")
        self._serial_seq = 990000

    def _next_serial(self) -> int:
        self._serial_seq += 1
        return self._serial_seq

    def _create_bill(self) -> Bill:
        return Bill.objects.create(serial=self._next_serial(), provider=self.provider)

    def _path(self, provider_ref: str) -> str:
        return f"/manager/debts/api/provider/{provider_ref}/account-allocation-preview/"

    def _post(self, provider_ref: str, payload: dict):
        return self.client.post(
            self._path(provider_ref),
            data=json.dumps(payload),
            content_type="application/json",
        )

    def _serialize_decimals(self, value):
        if isinstance(value, Decimal):
            return format(value, "f")
        if isinstance(value, dict):
            return {k: self._serialize_decimals(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._serialize_decimals(v) for v in value]
        return value

    def _json_safe(self, value):
        return json.loads(json.dumps(value, cls=DjangoJSONEncoder))

    def _create_central_debt(
        self,
        *,
        direction: str,
        cause_type: str,
        cause_id: str,
        remaining_syp: str = "0.00",
        remaining_usd: str = "0.00",
        status: str = DebtStatus.OPEN,
    ) -> DebtRecord:
        return DebtRecord.objects.create(
            direction=direction,
            cause_type=cause_type,
            cause_id=cause_id,
            source_app="billing",
            other_party_type=OtherPartyType.PROVIDER,
            other_party_id=str(self.provider.id),
            provider=self.provider,
            actor_username="phase4b_api",
            total_syp=Decimal(remaining_syp),
            total_usd=Decimal(remaining_usd),
            remaining_syp=Decimal(remaining_syp),
            remaining_usd=Decimal(remaining_usd),
            status=status,
        )

    def _create_legacy_debtor(
        self,
        *,
        source_app: str,
        source_model: str,
        source_id: str,
        currency_code: str,
        total: str,
        paid_amount: str = "0.00",
        legacy_source_id: str = "",
    ) -> DebtorDebt:
        return DebtorDebt.objects.create(
            provider=self.provider,
            source_app=source_app,
            source_model=source_model,
            source_id=source_id,
            legacy_source_id=legacy_source_id,
            total=Decimal(total),
            paid_amount=Decimal(paid_amount),
            status=DebtorDebt.Status.OPEN,
            party_type=PartyType.PROVIDER,
            party_name=self.provider.name,
            currency_code=currency_code,
        )

    def _create_legacy_creditor(
        self,
        *,
        source_app: str,
        source_model: str,
        source_id: str,
        currency_code: str,
        total: str,
        collected: str = "0.00",
        legacy_source_id: str = "",
    ) -> CreditorDebt:
        return CreditorDebt.objects.create(
            provider=self.provider,
            source_app=source_app,
            source_model=source_model,
            source_id=source_id,
            legacy_source_id=legacy_source_id,
            total=Decimal(total),
            collected=Decimal(collected),
            status=CreditorDebt.Status.OPEN,
            party_type=PartyType.PROVIDER,
            party_name=self.provider.name,
            currency_code=currency_code,
        )

    def test_valid_preview_response(self):
        self._create_central_debt(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id="phase4b-pay-1",
            remaining_syp="2000.00",
        )
        self._create_central_debt(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id="phase4b-pay-2",
            remaining_syp="3000.00",
        )
        resp = self._post(
            str(self.provider.id),
            {"action": "pay_provider", "currency": "SYP", "amount": "2500.00"},
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertTrue(data["ok"])
        for key in (
            "provider_id",
            "action",
            "currency",
            "requested_amount",
            "allocatable_amount",
            "eligible_total_remaining",
            "total_applied",
            "unallocated_amount",
            "eligible_debt_count",
            "allocated_debt_count",
            "allocations",
            "diagnostics",
        ):
            self.assertIn(key, data)
        self.assertEqual(data["provider_id"], self.provider.id)
        self.assertEqual(data["action"], "pay_provider")
        self.assertEqual(data["currency"], "SYP")

    def test_preview_api_accepts_provider_public_id_ref(self):
        self._create_central_debt(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id="phase4b-public-ref-pay",
            remaining_syp="500.00",
        )
        resp = self._post(
            self.provider.public_id,
            {"action": "pay_provider", "currency": "SYP", "amount": "100.00"},
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["provider_id"], self.provider.id)

    def test_allocation_list_matches_simulator(self):
        self._create_central_debt(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id="phase4b-match-1",
            remaining_syp="2000.00",
        )
        self._create_central_debt(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id="phase4b-match-2",
            remaining_syp="3000.00",
        )

        payload = {"action": "pay_provider", "currency": "SYP", "amount": "4000.00"}
        expected = simulate_provider_account_allocation(
            provider_id=self.provider.id,
            action=payload["action"],
            currency=payload["currency"],
            amount=payload["amount"],
        )
        expected_payload = {
            "ok": True,
            **self._json_safe(self._serialize_decimals(expected)),
        }

        resp = self._post(str(self.provider.id), payload)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["allocations"], expected_payload["allocations"])

    def test_diagnostics_passthrough(self):
        bill = self._create_bill()
        self._create_central_debt(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id=bill.public_id,
            remaining_syp="700.00",
        )
        self._create_legacy_debtor(
            source_app="billing",
            source_model="Bill",
            source_id=str(bill.id),
            currency_code="SYP",
            total="1000.00",
        )
        self._create_central_debt(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id="not-a-valid-bill-ref",
            remaining_syp="250.00",
        )
        resp = self._post(
            str(self.provider.id),
            {"action": "pay_provider", "currency": "SYP", "amount": "50.00"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("diagnostics", data)
        self.assertIn("dedup_warnings", data["diagnostics"])
        self.assertIn("unresolved_identities", data["diagnostics"])
        self.assertGreaterEqual(len(data["diagnostics"]["dedup_warnings"]), 1)
        self.assertGreaterEqual(len(data["diagnostics"]["unresolved_identities"]), 1)

    def test_invalid_payload_errors(self):
        base = str(self.provider.id)
        cases = [
            ({"action": "bad", "currency": "SYP", "amount": "10.00"}, "invalid action"),
            ({"action": "pay_provider", "currency": "EUR", "amount": "10.00"}, "invalid currency"),
            ({"action": "pay_provider", "currency": "SYP", "amount": "abc"}, "Invalid amount"),
            ({"action": "pay_provider", "currency": "SYP", "amount": "0"}, "amount must be positive"),
            (
                {"action": "pay_provider", "currency": "SYP", "amount": "10.001"},
                "amount supports at most 2 decimal digits",
            ),
        ]
        for payload, expected_error in cases:
            with self.subTest(payload=payload):
                resp = self._post(base, payload)
                self.assertEqual(resp.status_code, 400)
                data = resp.json()
                self.assertFalse(data["ok"])
                self.assertEqual(data["error"], expected_error)

        resp_bad_json = self.client.post(
            self._path(base),
            data="{bad json",
            content_type="application/json",
        )
        self.assertEqual(resp_bad_json.status_code, 400)
        self.assertEqual(resp_bad_json.json()["error"], "bad json")

    def test_missing_provider_error(self):
        missing_provider_id = self.provider.id + 999999
        resp = self._post(
            str(missing_provider_id),
            {"action": "pay_provider", "currency": "SYP", "amount": "10.00"},
        )
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json(), {"ok": False, "error": "provider not found"})

    def test_no_write_side_effects(self):
        central_payable = self._create_central_debt(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id="phase4b-ro-pay",
            remaining_syp="9000.00",
        )
        central_receivable = self._create_central_debt(
            direction=DebtDirection.RECEIVABLE,
            cause_type=DebtCauseType.MANUAL,
            cause_id="phase4b-ro-recv",
            remaining_syp="3000.00",
        )
        legacy_debtor = self._create_legacy_debtor(
            source_app="billing",
            source_model="Bill",
            source_id="legacy-ro-1",
            currency_code="SYP",
            total="2000.00",
            paid_amount="500.00",
        )
        legacy_creditor = self._create_legacy_creditor(
            source_app="billing",
            source_model="ProviderReturn",
            source_id="legacy-ro-2",
            currency_code="SYP",
            total="1000.00",
            collected="100.00",
        )

        before_central = list(
            DebtRecord.objects.filter(provider=self.provider)
            .order_by("id")
            .values("id", "status", "remaining_syp", "remaining_usd")
        )
        before_legacy_debtor = list(
            DebtorDebt.objects.filter(provider=self.provider)
            .order_by("id")
            .values("id", "status", "paid_amount")
        )
        before_legacy_creditor = list(
            CreditorDebt.objects.filter(provider=self.provider)
            .order_by("id")
            .values("id", "status", "collected")
        )
        before_settlement_count = DebtSettlement.objects.count()
        before_receipt_count = Receipt.objects.count()
        before_debtor_payment_count = DebtorPayment.objects.count()
        before_creditor_receipt_count = CreditorReceipt.objects.count()

        resp = self._post(
            str(self.provider.id),
            {"action": "pay_provider", "currency": "SYP", "amount": "1000.00"},
        )
        self.assertEqual(resp.status_code, 200)

        central_payable.refresh_from_db()
        central_receivable.refresh_from_db()
        legacy_debtor.refresh_from_db()
        legacy_creditor.refresh_from_db()

        after_central = list(
            DebtRecord.objects.filter(provider=self.provider)
            .order_by("id")
            .values("id", "status", "remaining_syp", "remaining_usd")
        )
        after_legacy_debtor = list(
            DebtorDebt.objects.filter(provider=self.provider)
            .order_by("id")
            .values("id", "status", "paid_amount")
        )
        after_legacy_creditor = list(
            CreditorDebt.objects.filter(provider=self.provider)
            .order_by("id")
            .values("id", "status", "collected")
        )
        after_settlement_count = DebtSettlement.objects.count()
        after_receipt_count = Receipt.objects.count()
        after_debtor_payment_count = DebtorPayment.objects.count()
        after_creditor_receipt_count = CreditorReceipt.objects.count()

        self.assertEqual(before_central, after_central)
        self.assertEqual(before_legacy_debtor, after_legacy_debtor)
        self.assertEqual(before_legacy_creditor, after_legacy_creditor)
        self.assertEqual(before_settlement_count, after_settlement_count)
        self.assertEqual(before_receipt_count, after_receipt_count)
        self.assertEqual(before_debtor_payment_count, after_debtor_payment_count)
        self.assertEqual(before_creditor_receipt_count, after_creditor_receipt_count)

    def test_no_fx_cross_currency_allocation(self):
        self._create_central_debt(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id="phase4b-usd-only",
            remaining_usd="10.00",
        )
        resp = self._post(
            str(self.provider.id),
            {"action": "pay_provider", "currency": "SYP", "amount": "1.00"},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "action/currency has zero remaining balance")

    def test_api_output_matches_simulator_after_decimal_serialization(self):
        bill = self._create_bill()
        self._create_central_debt(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id=bill.public_id,
            remaining_syp="1200.00",
        )
        self._create_legacy_debtor(
            source_app="billing",
            source_model="Bill",
            source_id=bill.public_id,
            currency_code="SYP",
            total="1200.00",
        )
        self._create_central_debt(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.MANUAL,
            cause_id="phase4b-extra-usd",
            remaining_usd="10.00",
        )

        payload = {"action": "pay_provider", "currency": "SYP", "amount": "700.00"}
        expected = simulate_provider_account_allocation(
            provider_id=self.provider.id,
            action=payload["action"],
            currency=payload["currency"],
            amount=payload["amount"],
        )
        expected_payload = {
            "ok": True,
            **self._json_safe(self._serialize_decimals(expected)),
        }

        resp = self._post(str(self.provider.id), payload)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), expected_payload)

    def test_forbidden_for_non_manager(self):
        self.client.force_login(self.cashier)
        resp = self._post(
            str(self.provider.id),
            {"action": "pay_provider", "currency": "SYP", "amount": "10.00"},
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json(), {"ok": False, "error": "FORBIDDEN"})
