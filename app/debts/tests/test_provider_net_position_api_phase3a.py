from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
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
from debts.provider_position import get_provider_net_position
from financials.models import Receipt


class ProviderNetPositionApiPhase3ATests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.manager = user_model.objects.create_user(
            username="provider_net_pos_api_mgr",
            password="123456",
        )
        AccountProfile.objects.create(user=cls.manager, role=AccountProfile.Role.MANAGER)

        cls.cashier = user_model.objects.create_user(
            username="provider_net_pos_api_cashier",
            password="123456",
        )
        AccountProfile.objects.create(user=cls.cashier, role=AccountProfile.Role.CASHIER)

    def setUp(self):
        self.client.force_login(self.manager)
        self.provider = Provider.objects.create(name=f"Phase3A Provider {self._testMethodName}")

    def _path(self, provider_ref: str) -> str:
        return f"/manager/debts/api/provider/{provider_ref}/net-position/"

    def _serialize_decimals(self, value):
        if isinstance(value, Decimal):
            return format(value, "f")
        if isinstance(value, dict):
            return {k: self._serialize_decimals(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._serialize_decimals(v) for v in value]
        return value

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
            actor_username="phase3a_api",
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

    def test_api_returns_expected_single_provider_position(self):
        self._create_central_debt(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id="phase3a-payable-1",
            remaining_syp="20000.00",
        )
        self._create_central_debt(
            direction=DebtDirection.RECEIVABLE,
            cause_type=DebtCauseType.MANUAL,
            cause_id="phase3a-receivable-1",
            remaining_syp="10000.00",
        )

        resp = self.client.get(self._path(str(self.provider.id)))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        self.assertTrue(data["ok"])
        self.assertEqual(data["provider_id"], self.provider.id)
        self.assertEqual(data["currencies"]["SYP"]["receivable"], "10000.00")
        self.assertEqual(data["currencies"]["SYP"]["payable"], "20000.00")
        self.assertEqual(data["currencies"]["SYP"]["net"], "-10000.00")
        self.assertEqual(data["currencies"]["SYP"]["open_receivable_count"], 1)
        self.assertEqual(data["currencies"]["SYP"]["open_payable_count"], 1)

    def test_api_accepts_provider_public_id_ref(self):
        self._create_central_debt(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.MANUAL,
            cause_id="phase3a-public-ref-payable",
            remaining_syp="111.00",
        )

        resp = self.client.get(self._path(self.provider.public_id))
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["provider_id"], self.provider.id)
        self.assertEqual(data["currencies"]["SYP"]["payable"], "111.00")

    def test_api_keeps_numeric_provider_ref_compatibility(self):
        resp = self.client.get(self._path(str(self.provider.id)))
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["provider_id"], self.provider.id)

    def test_api_includes_diagnostics_passthrough(self):
        bill = Bill.objects.create(serial=901001, provider=self.provider)
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

        resp = self.client.get(self._path(str(self.provider.id)))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        self.assertIn("diagnostics", data)
        self.assertIn("dedup_warnings", data["diagnostics"])
        self.assertIn("unresolved_identities", data["diagnostics"])
        self.assertIsInstance(data["diagnostics"]["dedup_warnings"], list)
        self.assertIsInstance(data["diagnostics"]["unresolved_identities"], list)
        self.assertGreaterEqual(len(data["diagnostics"]["dedup_warnings"]), 1)
        self.assertGreaterEqual(len(data["diagnostics"]["unresolved_identities"]), 1)

    def test_api_missing_provider_returns_clean_error(self):
        missing_provider_id = self.provider.id + 999999
        resp = self.client.get(self._path(str(missing_provider_id)))
        self.assertEqual(resp.status_code, 404)
        data = resp.json()
        self.assertFalse(data["ok"])
        self.assertEqual(data["error"], "provider not found")

    def test_api_invalid_provider_id_returns_clean_error(self):
        resp = self.client.get(self._path("not-an-id"))
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertFalse(data["ok"])
        self.assertEqual(data["error"], "invalid provider id")

    def test_api_projection_is_read_only_and_performs_no_writes(self):
        central_payable = self._create_central_debt(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id="phase3a-ro-payable",
            remaining_syp="9000.00",
        )
        central_receivable = self._create_central_debt(
            direction=DebtDirection.RECEIVABLE,
            cause_type=DebtCauseType.MANUAL,
            cause_id="phase3a-ro-receivable",
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

        resp = self.client.get(self._path(str(self.provider.id)))
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

    def test_api_currency_isolation_no_forced_fx_conversion(self):
        self._create_central_debt(
            direction=DebtDirection.RECEIVABLE,
            cause_type=DebtCauseType.MANUAL,
            cause_id="phase3a-usd-recv",
            remaining_usd="100.00",
        )
        self._create_central_debt(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id="phase3a-syp-pay",
            remaining_syp="200000.00",
        )

        resp = self.client.get(self._path(str(self.provider.id)))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        self.assertEqual(data["currencies"]["USD"]["receivable"], "100.00")
        self.assertEqual(data["currencies"]["USD"]["payable"], "0.00")
        self.assertEqual(data["currencies"]["USD"]["net"], "100.00")

        self.assertEqual(data["currencies"]["SYP"]["receivable"], "0.00")
        self.assertEqual(data["currencies"]["SYP"]["payable"], "200000.00")
        self.assertEqual(data["currencies"]["SYP"]["net"], "-200000.00")

    def test_api_matches_service_output_exactly_after_decimal_serialization(self):
        bill = Bill.objects.create(serial=901002, provider=self.provider)
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
            direction=DebtDirection.RECEIVABLE,
            cause_type=DebtCauseType.MANUAL,
            cause_id="phase3a-match-recv",
            remaining_usd="10.00",
        )

        expected_service_output = get_provider_net_position(provider_id=self.provider.id)
        expected_payload = {
            "ok": True,
            **self._serialize_decimals(expected_service_output),
        }

        resp = self.client.get(self._path(str(self.provider.id)))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), expected_payload)

    def test_api_forbidden_for_non_manager(self):
        self.client.force_login(self.cashier)
        resp = self.client.get(self._path(str(self.provider.id)))
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json(), {"ok": False, "error": "FORBIDDEN"})
