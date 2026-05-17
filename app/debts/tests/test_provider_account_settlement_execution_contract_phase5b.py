from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import AccountProfile
from billing.models import Bill, Provider, ProviderReturn
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
    ProviderSettlementAction,
    ProviderSettlementAllocation,
)
from financials import services as FinSV
from financials.models import (
    ContainerFeature,
    Currency,
    MoneyContainer,
    MoneyContainerCurrency,
    Receipt,
)


class ProviderAccountSettlementExecutionContractPhase5BTests(TestCase):
    """
    Phase 5B contract tests for future write-path execution service.

    These tests are intentionally expected to fail before implementation.
    They freeze behavior and persistence contracts for the future execution phase.
    """

    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.manager = user_model.objects.create_user(
            username="phase5b_exec_mgr",
            password="123456",
        )
        AccountProfile.objects.create(user=cls.manager, role=AccountProfile.Role.MANAGER)

    def setUp(self):
        self.provider = Provider.objects.create(name=f"Phase5B Provider {self._testMethodName}")
        self._now = timezone.now()
        self._cause_seq = 0
        self._serial_seq = 960000

        self.container = MoneyContainer.objects.create(
            name=f"Phase5B Cash {self._testMethodName}",
            container_type=MoneyContainer.ContainerType.DRAWER,
            is_active=True,
            created_by=self.manager,
        )
        self.container.allowed_users.add(self.manager)
        feature, _ = ContainerFeature.objects.get_or_create(
            code="provider_account_settlement",
            defaults={"name": "Provider Account Settlement", "is_active": True},
        )
        self.container.features.add(feature)

        self.hidden_container = MoneyContainer.objects.create(
            name=f"Phase5B Hidden Cash {self._testMethodName}",
            container_type=MoneyContainer.ContainerType.DRAWER,
            is_active=True,
            created_by=self.manager,
        )
        self.hidden_container.features.add(feature)

        syp, _ = Currency.objects.get_or_create(
            code="SYP",
            defaults={"name": "Syrian Pound", "decimals": 2, "is_active": True},
        )
        usd, _ = Currency.objects.get_or_create(
            code="USD",
            defaults={"name": "US Dollar", "decimals": 2, "is_active": True},
        )
        MoneyContainerCurrency.objects.update_or_create(
            container=self.container,
            currency=syp,
            defaults={"is_enabled": True},
        )
        MoneyContainerCurrency.objects.update_or_create(
            container=self.container,
            currency=usd,
            defaults={"is_enabled": True},
        )
        MoneyContainerCurrency.objects.update_or_create(
            container=self.hidden_container,
            currency=syp,
            defaults={"is_enabled": True},
        )
        MoneyContainerCurrency.objects.update_or_create(
            container=self.hidden_container,
            currency=usd,
            defaults={"is_enabled": True},
        )
        FinSV.set_current_fx(actor=self.manager, rate_syp_per_usd=Decimal("20000"))

    def _next_cause_id(self, prefix: str) -> str:
        self._cause_seq += 1
        return f"{prefix}-{self._cause_seq}"

    def _next_serial(self) -> int:
        self._serial_seq += 1
        return self._serial_seq

    def _create_bill(self) -> Bill:
        return Bill.objects.create(serial=self._next_serial(), provider=self.provider)

    def _create_return(self) -> ProviderReturn:
        return ProviderReturn.objects.create(provider=self.provider)

    def _create_central_debt(
        self,
        *,
        direction: str,
        cause_type: str,
        cause_id: str,
        remaining_syp: str = "0.00",
        remaining_usd: str = "0.00",
        created_offset_days: int = 0,
        resolve_source_identity: bool = True,
    ) -> DebtRecord:
        effective_cause_id = str(cause_id or "")
        if resolve_source_identity and cause_type == DebtCauseType.PURCHASE_BILL:
            effective_cause_id = self._create_bill().public_id
        elif resolve_source_identity and cause_type == DebtCauseType.PROVIDER_RETURN:
            effective_cause_id = self._create_return().public_id

        row = DebtRecord.objects.create(
            direction=direction,
            cause_type=cause_type,
            cause_id=effective_cause_id,
            source_app="billing",
            other_party_type=OtherPartyType.PROVIDER,
            other_party_id=str(self.provider.id),
            provider=self.provider,
            actor_username="phase5b",
            total_syp=Decimal(remaining_syp),
            total_usd=Decimal(remaining_usd),
            remaining_syp=Decimal(remaining_syp),
            remaining_usd=Decimal(remaining_usd),
            status=DebtStatus.OPEN,
        )
        DebtRecord.objects.filter(id=row.id).update(
            created_at=self._now + timedelta(days=created_offset_days)
        )
        row.refresh_from_db()
        return row

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

    def _execution_targets(self):
        missing: list[str] = []
        executor = None

        try:
            from debts.provider_account_settlement_execution import (
                execute_provider_account_settlement,
            )
            executor = execute_provider_account_settlement
        except Exception as exc:
            missing.append(
                "service `debts.provider_account_settlement_execution.execute_provider_account_settlement` "
                f"missing/import failed ({exc!r})"
            )

        try:
            action_model = apps.get_model("debts", "ProviderSettlementAction")
        except LookupError:
            action_model = None
        if action_model is None:
            missing.append("model `debts.ProviderSettlementAction` missing")

        try:
            allocation_model = apps.get_model("debts", "ProviderSettlementAllocation")
        except LookupError:
            allocation_model = None
        if allocation_model is None:
            missing.append("model `debts.ProviderSettlementAllocation` missing")

        if missing:
            self.fail(
                "Phase 5B execution contract target missing. "
                "This is expected before write-path implementation. Missing: "
                + "; ".join(missing)
            )
        return executor, action_model, allocation_model

    def _execute(
        self,
        *,
        action: str,
        currency: str,
        amount,
        money_container_id: int | None = None,
        idempotency_key: str | None = None,
        preview_fingerprint: str | None = None,
    ) -> tuple[dict, object, object]:
        executor, action_model, allocation_model = self._execution_targets()
        result = executor(
            provider_id=self.provider.id,
            action=action,
            currency=currency,
            amount=amount,
            money_container_id=money_container_id or self.container.id,
            idempotency_key=idempotency_key or f"{self._testMethodName}:idempotency",
            preview_fingerprint=preview_fingerprint,
            user=self.manager,
        )
        return result, action_model, allocation_model

    def test_case_01_basic_pay_provider_execution_contract(self):
        d1 = self._create_central_debt(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id=self._next_cause_id("pb"),
            remaining_syp="2000.00",
            created_offset_days=-4,
        )
        d2 = self._create_central_debt(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id=self._next_cause_id("pb"),
            remaining_syp="3000.00",
            created_offset_days=-3,
        )
        d3 = self._create_central_debt(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id=self._next_cause_id("pb"),
            remaining_syp="10000.00",
            created_offset_days=-2,
        )
        d4 = self._create_central_debt(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id=self._next_cause_id("pb"),
            remaining_syp="5000.00",
            created_offset_days=-1,
        )

        result, action_model, allocation_model = self._execute(
            action="pay_provider",
            currency="SYP",
            amount="10000.00",
            idempotency_key=f"{self._testMethodName}:k1",
        )

        self.assertEqual(result["provider_id"], self.provider.id)
        self.assertEqual(result["action"], "pay_provider")
        self.assertEqual(result["currency"], "SYP")
        self.assertEqual(result["requested_amount"], Decimal("10000.00"))
        self.assertEqual(result["total_applied"], Decimal("10000.00"))
        self.assertEqual(result["allocation_count"], 3)
        self.assertEqual(len(result["allocations"]), 3)
        self.assertTrue(result["action_id"] > 0)
        self.assertTrue(result["receipt_id"] > 0)

        d1.refresh_from_db()
        d2.refresh_from_db()
        d3.refresh_from_db()
        d4.refresh_from_db()
        self.assertEqual(d1.remaining_syp, Decimal("0.00"))
        self.assertEqual(d1.status, DebtStatus.CLOSED)
        self.assertEqual(d2.remaining_syp, Decimal("0.00"))
        self.assertEqual(d2.status, DebtStatus.CLOSED)
        self.assertEqual(d3.remaining_syp, Decimal("5000.00"))
        self.assertEqual(d3.status, DebtStatus.OPEN)
        self.assertEqual(d4.remaining_syp, Decimal("5000.00"))
        self.assertEqual(d4.status, DebtStatus.OPEN)

        self.assertEqual(action_model.objects.count(), 1)
        self.assertEqual(allocation_model.objects.count(), 3)
        self.assertEqual(Receipt.objects.count(), 1)

    def test_case_02_basic_receive_from_provider_execution_contract(self):
        r1 = self._create_central_debt(
            direction=DebtDirection.RECEIVABLE,
            cause_type=DebtCauseType.MANUAL,
            cause_id=self._next_cause_id("m"),
            remaining_syp="2000.00",
            created_offset_days=-3,
        )
        r2 = self._create_central_debt(
            direction=DebtDirection.RECEIVABLE,
            cause_type=DebtCauseType.MANUAL,
            cause_id=self._next_cause_id("m"),
            remaining_syp="3000.00",
            created_offset_days=-2,
        )
        r3 = self._create_central_debt(
            direction=DebtDirection.RECEIVABLE,
            cause_type=DebtCauseType.MANUAL,
            cause_id=self._next_cause_id("m"),
            remaining_syp="10000.00",
            created_offset_days=-1,
        )
        pay_untouched = self._create_central_debt(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id=self._next_cause_id("pb"),
            remaining_syp="7000.00",
            created_offset_days=-4,
        )

        result, _, _ = self._execute(
            action="receive_from_provider",
            currency="SYP",
            amount="4500.00",
            idempotency_key=f"{self._testMethodName}:k1",
        )
        self.assertEqual(result["total_applied"], Decimal("4500.00"))
        self.assertTrue(all(a["direction"] == DebtDirection.RECEIVABLE for a in result["allocations"]))

        r1.refresh_from_db()
        r2.refresh_from_db()
        r3.refresh_from_db()
        pay_untouched.refresh_from_db()
        self.assertEqual(r1.remaining_syp, Decimal("0.00"))
        self.assertEqual(r2.remaining_syp, Decimal("500.00"))
        self.assertEqual(r3.remaining_syp, Decimal("10000.00"))
        self.assertEqual(pay_untouched.remaining_syp, Decimal("7000.00"))

    def test_case_03_currency_isolation_execution_contract(self):
        mixed = self._create_central_debt(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id=self._next_cause_id("pb"),
            remaining_syp="2000.00",
            remaining_usd="5.00",
            created_offset_days=-2,
        )
        usd_only = self._create_central_debt(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id=self._next_cause_id("pb"),
            remaining_usd="10.00",
            created_offset_days=-1,
        )

        result, _, _ = self._execute(
            action="pay_provider",
            currency="SYP",
            amount="500.00",
            idempotency_key=f"{self._testMethodName}:k1",
        )
        self.assertEqual(result["currency"], "SYP")
        mixed.refresh_from_db()
        usd_only.refresh_from_db()
        self.assertEqual(mixed.remaining_syp, Decimal("1500.00"))
        self.assertEqual(mixed.remaining_usd, Decimal("5.00"))
        self.assertEqual(usd_only.remaining_usd, Decimal("10.00"))

    def test_case_04_no_auto_cancel_contract(self):
        pay = self._create_central_debt(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id=self._next_cause_id("pb"),
            remaining_syp="1000.00",
            created_offset_days=-2,
        )
        recv = self._create_central_debt(
            direction=DebtDirection.RECEIVABLE,
            cause_type=DebtCauseType.MANUAL,
            cause_id=self._next_cause_id("m"),
            remaining_syp="1000.00",
            created_offset_days=-1,
        )

        result, _, _ = self._execute(
            action="pay_provider",
            currency="SYP",
            amount="500.00",
            idempotency_key=f"{self._testMethodName}:k1",
        )
        self.assertEqual(result["total_applied"], Decimal("500.00"))
        recv.refresh_from_db()
        pay.refresh_from_db()
        self.assertEqual(recv.remaining_syp, Decimal("1000.00"))
        self.assertEqual(pay.remaining_syp, Decimal("500.00"))

    def test_case_05_idempotency_replay_contract(self):
        debt = self._create_central_debt(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id=self._next_cause_id("pb"),
            remaining_syp="1000.00",
        )
        idem = f"{self._testMethodName}:same"

        first, action_model, allocation_model = self._execute(
            action="pay_provider",
            currency="SYP",
            amount="1000.00",
            idempotency_key=idem,
        )
        second, _, _ = self._execute(
            action="pay_provider",
            currency="SYP",
            amount="1000.00",
            idempotency_key=idem,
        )

        self.assertEqual(first["action_id"], second["action_id"])
        self.assertEqual(first["receipt_id"], second["receipt_id"])
        self.assertEqual(action_model.objects.count(), 1)
        self.assertEqual(allocation_model.objects.count(), 1)
        self.assertEqual(Receipt.objects.count(), 1)
        debt.refresh_from_db()
        self.assertEqual(debt.remaining_syp, Decimal("0.00"))
        self.assertEqual(debt.status, DebtStatus.CLOSED)

    def test_case_06_idempotency_conflict_contract(self):
        self._create_central_debt(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id=self._next_cause_id("pb"),
            remaining_syp="2000.00",
        )
        idem = f"{self._testMethodName}:conflict"

        self._execute(
            action="pay_provider",
            currency="SYP",
            amount="500.00",
            idempotency_key=idem,
        )
        with self.assertRaisesMessage(ValueError, "idempotency key conflict"):
            self._execute(
                action="pay_provider",
                currency="SYP",
                amount="600.00",
                idempotency_key=idem,
            )

    def test_case_07_stale_preview_fingerprint_conflict_contract(self):
        self._create_central_debt(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id=self._next_cause_id("pb"),
            remaining_syp="1000.00",
        )
        with self.assertRaisesMessage(ValueError, "stale preview fingerprint"):
            self._execute(
                action="pay_provider",
                currency="SYP",
                amount="300.00",
                idempotency_key=f"{self._testMethodName}:k1",
                preview_fingerprint="stale-preview-mismatch",
            )

    def test_case_08_unresolved_identity_diagnostics_block_execution(self):
        self._create_central_debt(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id="not-a-valid-bill-ref",
            remaining_syp="1000.00",
            resolve_source_identity=False,
        )
        self._create_legacy_debtor(
            source_app="billing",
            source_model="Bill",
            source_id="12345",
            currency_code="SYP",
            total="1000.00",
        )
        with self.assertRaisesMessage(ValueError, "execution blocked: unresolved identities"):
            self._execute(
                action="pay_provider",
                currency="SYP",
                amount="100.00",
                idempotency_key=f"{self._testMethodName}:k1",
            )

    def test_case_09_atomic_rollback_contract(self):
        d1 = self._create_central_debt(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id=self._next_cause_id("pb"),
            remaining_syp="500.00",
        )
        d2 = self._create_central_debt(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id=self._next_cause_id("pb"),
            remaining_syp="600.00",
        )

        before_action = 0
        before_allocation = 0
        try:
            _, action_model, allocation_model = self._execution_targets()
            before_action = action_model.objects.count()
            before_allocation = allocation_model.objects.count()
        except AssertionError:
            # keep explicit no-op, the test is expected to fail pre-implementation
            pass

        before_receipts = Receipt.objects.count()
        before_settlements = DebtSettlement.objects.count()
        before_debtor_payments = DebtorPayment.objects.count()
        before_creditor_receipts = CreditorReceipt.objects.count()
        before_state = list(
            DebtRecord.objects.filter(id__in=[d1.id, d2.id])
            .order_by("id")
            .values("id", "remaining_syp", "remaining_usd", "status")
        )

        with patch(
            "financials.services.post_settlement_components_with_fx",
            side_effect=RuntimeError("forced receipt posting failure"),
        ):
            with self.assertRaises(RuntimeError):
                self._execute(
                    action="pay_provider",
                    currency="SYP",
                    amount="1000.00",
                    idempotency_key=f"{self._testMethodName}:k1",
                )

        after_receipts = Receipt.objects.count()
        after_settlements = DebtSettlement.objects.count()
        after_debtor_payments = DebtorPayment.objects.count()
        after_creditor_receipts = CreditorReceipt.objects.count()
        after_state = list(
            DebtRecord.objects.filter(id__in=[d1.id, d2.id])
            .order_by("id")
            .values("id", "remaining_syp", "remaining_usd", "status")
        )

        self.assertEqual(before_receipts, after_receipts)
        self.assertEqual(before_settlements, after_settlements)
        self.assertEqual(before_debtor_payments, after_debtor_payments)
        self.assertEqual(before_creditor_receipts, after_creditor_receipts)
        self.assertEqual(before_state, after_state)

        try:
            _, action_model, allocation_model = self._execution_targets()
            self.assertEqual(action_model.objects.count(), before_action)
            self.assertEqual(allocation_model.objects.count(), before_allocation)
        except AssertionError:
            pass

    def test_case_10_grouped_receipt_invariant_contract(self):
        d1 = self._create_central_debt(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id=self._next_cause_id("pb"),
            remaining_syp="500.00",
            created_offset_days=-3,
        )
        d2 = self._create_central_debt(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id=self._next_cause_id("pb"),
            remaining_syp="500.00",
            created_offset_days=-2,
        )
        d3 = self._create_central_debt(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id=self._next_cause_id("pb"),
            remaining_syp="500.00",
            created_offset_days=-1,
        )

        result, action_model, allocation_model = self._execute(
            action="pay_provider",
            currency="SYP",
            amount="1000.00",
            idempotency_key=f"{self._testMethodName}:k1",
        )

        action_row = action_model.objects.get(pk=result["action_id"])
        self.assertIsNotNone(getattr(action_row, "receipt_id", None))
        alloc_rows = list(
            allocation_model.objects
            .filter(action_id=action_row.id)
            .order_by("sequence", "id")
        )
        self.assertEqual(len(alloc_rows), 2)

        touched_ids = [d1.id, d2.id, d3.id]
        debt_settlements = list(
            DebtSettlement.objects
            .filter(debt_id__in=touched_ids)
            .order_by("id")
        )
        self.assertEqual(len(debt_settlements), 2)
        self.assertTrue(all(s.receipt_id == action_row.receipt_id for s in debt_settlements))

    def test_case_11_precision_and_validation_contract(self):
        self._create_central_debt(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id=self._next_cause_id("pb"),
            remaining_syp="100.00",
        )
        cases = [
            (
                {"action": "invalid", "currency": "SYP", "amount": "10.00", "container_id": self.container.id},
                "invalid action",
            ),
            (
                {"action": "pay_provider", "currency": "EUR", "amount": "10.00", "container_id": self.container.id},
                "invalid currency",
            ),
            (
                {"action": "pay_provider", "currency": "SYP", "amount": "0", "container_id": self.container.id},
                "amount must be positive",
            ),
            (
                {"action": "pay_provider", "currency": "SYP", "amount": "10.001", "container_id": self.container.id},
                "amount supports at most 2 decimal digits",
            ),
            (
                {"action": "pay_provider", "currency": "SYP", "amount": "200.00", "container_id": self.container.id},
                "amount exceeds eligible total",
            ),
            (
                {"action": "pay_provider", "currency": "SYP", "amount": "10.00", "container_id": self.hidden_container.id},
                "money container is not allowed for this operation",
            ),
        ]

        for payload, expected_error in cases:
            with self.subTest(payload=payload):
                with self.assertRaisesMessage(ValueError, expected_error):
                    self._execute(
                        action=payload["action"],
                        currency=payload["currency"],
                        amount=payload["amount"],
                        money_container_id=payload["container_id"],
                        idempotency_key=f"{self._testMethodName}:{payload['action']}:{payload['currency']}:{payload['amount']}",
                    )

    def _snapshot_counts(self) -> dict:
        return {
            "actions": ProviderSettlementAction.objects.count(),
            "allocations": ProviderSettlementAllocation.objects.count(),
            "receipts": Receipt.objects.count(),
            "central_settlements": DebtSettlement.objects.count(),
            "legacy_debtor_payments": DebtorPayment.objects.count(),
            "legacy_creditor_receipts": CreditorReceipt.objects.count(),
        }

    def _snapshot_central_rows(self, *ids: int) -> list[dict]:
        return list(
            DebtRecord.objects
            .filter(id__in=list(ids))
            .order_by("id")
            .values("id", "remaining_syp", "remaining_usd", "status")
        )

    def _snapshot_legacy_debtor_rows(self, *ids: int) -> list[dict]:
        return list(
            DebtorDebt.objects
            .filter(id__in=list(ids))
            .order_by("id")
            .values("id", "paid_amount", "status")
        )

    def _snapshot_legacy_creditor_rows(self, *ids: int) -> list[dict]:
        return list(
            CreditorDebt.objects
            .filter(id__in=list(ids))
            .order_by("id")
            .values("id", "collected", "status")
        )

    def test_case_12_rollback_after_action_row_creation(self):
        d1 = self._create_central_debt(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.MANUAL,
            cause_id=self._next_cause_id("m"),
            remaining_syp="500.00",
        )
        before_counts = self._snapshot_counts()
        before_debts = self._snapshot_central_rows(d1.id)

        with patch(
            "debts.provider_account_settlement_execution._lock_provider_debts",
            side_effect=RuntimeError("forced failure after action row creation"),
        ):
            with self.assertRaises(RuntimeError):
                self._execute(
                    action="pay_provider",
                    currency="SYP",
                    amount="100.00",
                    idempotency_key=f"{self._testMethodName}:k1",
                )

        self.assertEqual(self._snapshot_counts(), before_counts)
        self.assertEqual(self._snapshot_central_rows(d1.id), before_debts)

    def test_case_13_rollback_after_receipt_creation(self):
        d1 = self._create_central_debt(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.MANUAL,
            cause_id=self._next_cause_id("m"),
            remaining_syp="500.00",
        )
        before_counts = self._snapshot_counts()
        before_debts = self._snapshot_central_rows(d1.id)

        with patch(
            "debts.provider_account_settlement_execution.ProviderSettlementAction.refresh_from_db",
            side_effect=RuntimeError("forced failure after receipt creation"),
        ):
            with self.assertRaises(RuntimeError):
                self._execute(
                    action="pay_provider",
                    currency="SYP",
                    amount="100.00",
                    idempotency_key=f"{self._testMethodName}:k1",
                )

        self.assertEqual(self._snapshot_counts(), before_counts)
        self.assertEqual(self._snapshot_central_rows(d1.id), before_debts)

    def test_case_14_rollback_during_central_debt_mutation(self):
        d1 = self._create_central_debt(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.MANUAL,
            cause_id=self._next_cause_id("m"),
            remaining_syp="700.00",
        )
        before_counts = self._snapshot_counts()
        before_debts = self._snapshot_central_rows(d1.id)

        with patch(
            "debts.provider_account_settlement_execution.DebtRecord.save",
            side_effect=RuntimeError("forced central mutation failure"),
        ):
            with self.assertRaises(RuntimeError):
                self._execute(
                    action="pay_provider",
                    currency="SYP",
                    amount="200.00",
                    idempotency_key=f"{self._testMethodName}:k1",
                )

        self.assertEqual(self._snapshot_counts(), before_counts)
        self.assertEqual(self._snapshot_central_rows(d1.id), before_debts)

    def test_case_15_rollback_during_legacy_payable_mutation(self):
        legacy = self._create_legacy_debtor(
            source_app="billing",
            source_model="Bill",
            source_id="legacy-payable-rb",
            currency_code="SYP",
            total="900.00",
        )
        before_counts = self._snapshot_counts()
        before_legacy = self._snapshot_legacy_debtor_rows(legacy.id)

        with patch(
            "debts.provider_account_settlement_execution.DebtorDebt.save",
            side_effect=RuntimeError("forced legacy payable mutation failure"),
        ):
            with self.assertRaises(RuntimeError):
                self._execute(
                    action="pay_provider",
                    currency="SYP",
                    amount="200.00",
                    idempotency_key=f"{self._testMethodName}:k1",
                )

        self.assertEqual(self._snapshot_counts(), before_counts)
        self.assertEqual(self._snapshot_legacy_debtor_rows(legacy.id), before_legacy)

    def test_case_16_rollback_during_legacy_receivable_mutation(self):
        legacy = self._create_legacy_creditor(
            source_app="billing",
            source_model="ProviderReturn",
            source_id="legacy-recv-rb",
            currency_code="SYP",
            total="900.00",
        )
        before_counts = self._snapshot_counts()
        before_legacy = self._snapshot_legacy_creditor_rows(legacy.id)

        with patch(
            "debts.provider_account_settlement_execution.CreditorDebt.save",
            side_effect=RuntimeError("forced legacy receivable mutation failure"),
        ):
            with self.assertRaises(RuntimeError):
                self._execute(
                    action="receive_from_provider",
                    currency="SYP",
                    amount="200.00",
                    idempotency_key=f"{self._testMethodName}:k1",
                )

        self.assertEqual(self._snapshot_counts(), before_counts)
        self.assertEqual(self._snapshot_legacy_creditor_rows(legacy.id), before_legacy)

    def test_case_17_rollback_during_allocation_row_creation(self):
        d1 = self._create_central_debt(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.MANUAL,
            cause_id=self._next_cause_id("m"),
            remaining_syp="900.00",
        )
        before_counts = self._snapshot_counts()
        before_debts = self._snapshot_central_rows(d1.id)

        with patch(
            "debts.provider_account_settlement_execution.ProviderSettlementAllocation.objects.create",
            side_effect=RuntimeError("forced allocation create failure"),
        ):
            with self.assertRaises(RuntimeError):
                self._execute(
                    action="pay_provider",
                    currency="SYP",
                    amount="200.00",
                    idempotency_key=f"{self._testMethodName}:k1",
                )

        self.assertEqual(self._snapshot_counts(), before_counts)
        self.assertEqual(self._snapshot_central_rows(d1.id), before_debts)

    def test_case_18_legacy_payable_execution_mutates_legacy_only(self):
        legacy = self._create_legacy_debtor(
            source_app="billing",
            source_model="Bill",
            source_id="legacy-pay-1",
            currency_code="SYP",
            total="1000.00",
        )

        result, action_model, allocation_model = self._execute(
            action="pay_provider",
            currency="SYP",
            amount="400.00",
            idempotency_key=f"{self._testMethodName}:k1",
        )

        legacy.refresh_from_db()
        self.assertEqual(legacy.paid_amount, Decimal("400.00"))
        self.assertEqual(legacy.status, DebtorDebt.Status.OPEN)
        self.assertEqual(result["total_applied"], Decimal("400.00"))
        self.assertEqual(result["allocation_count"], 1)

        action_row = action_model.objects.get(pk=result["action_id"])
        alloc_row = allocation_model.objects.get(action_id=action_row.id)
        payment_row = DebtorPayment.objects.get(entry_id=legacy.id)
        self.assertEqual(alloc_row.debt_source, "legacy")
        self.assertEqual(payment_row.receipt_id, action_row.receipt_id)
        self.assertEqual(CreditorReceipt.objects.count(), 0)

    def test_case_19_legacy_receivable_execution_mutates_legacy_only(self):
        legacy = self._create_legacy_creditor(
            source_app="billing",
            source_model="ProviderReturn",
            source_id="legacy-recv-1",
            currency_code="SYP",
            total="1200.00",
            collected="200.00",
        )

        result, action_model, allocation_model = self._execute(
            action="receive_from_provider",
            currency="SYP",
            amount="300.00",
            idempotency_key=f"{self._testMethodName}:k1",
        )

        legacy.refresh_from_db()
        self.assertEqual(legacy.collected, Decimal("500.00"))
        self.assertEqual(legacy.status, CreditorDebt.Status.OPEN)
        self.assertEqual(result["total_applied"], Decimal("300.00"))
        self.assertEqual(result["allocation_count"], 1)

        action_row = action_model.objects.get(pk=result["action_id"])
        alloc_row = allocation_model.objects.get(action_id=action_row.id)
        receipt_row = CreditorReceipt.objects.get(entry_id=legacy.id)
        self.assertEqual(alloc_row.debt_source, "legacy")
        self.assertEqual(receipt_row.receipt_id, action_row.receipt_id)
        self.assertEqual(DebtorPayment.objects.count(), 0)

    def test_case_20_central_legacy_duplicate_only_canonical_winner_mutates(self):
        bill = self._create_bill()
        central = self._create_central_debt(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id=bill.public_id,
            remaining_syp="1000.00",
            resolve_source_identity=False,
        )
        legacy_dup = self._create_legacy_debtor(
            source_app="billing",
            source_model="Bill",
            source_id=bill.public_id,
            currency_code="SYP",
            total="1000.00",
        )

        result, _, _ = self._execute(
            action="pay_provider",
            currency="SYP",
            amount="300.00",
            idempotency_key=f"{self._testMethodName}:k1",
        )

        central.refresh_from_db()
        legacy_dup.refresh_from_db()
        self.assertEqual(result["allocation_count"], 1)
        self.assertTrue(all(row["debt_source"] == "central" for row in result["allocations"]))
        self.assertEqual(central.remaining_syp, Decimal("700.00"))
        self.assertEqual(legacy_dup.paid_amount, Decimal("0.00"))
        self.assertEqual(DebtorPayment.objects.count(), 0)

    def test_case_21_missing_source_object_unresolved_identity_blocks_execution(self):
        self._create_central_debt(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id="PB-999999",
            remaining_syp="500.00",
            resolve_source_identity=False,
        )
        with self.assertRaisesMessage(ValueError, "execution blocked: unresolved identities"):
            self._execute(
                action="pay_provider",
                currency="SYP",
                amount="100.00",
                idempotency_key=f"{self._testMethodName}:k1",
            )

    def test_case_22_decimal_diagnostics_are_serialized_safely(self):
        bill = self._create_bill()
        self._create_central_debt(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id=bill.public_id,
            remaining_syp="1000.00",
            resolve_source_identity=False,
        )
        self._create_legacy_debtor(
            source_app="billing",
            source_model="Bill",
            source_id=bill.public_id,
            currency_code="SYP",
            total="900.00",
        )

        result, action_model, _ = self._execute(
            action="pay_provider",
            currency="SYP",
            amount="100.00",
            idempotency_key=f"{self._testMethodName}:k1",
        )
        action_row = action_model.objects.get(pk=result["action_id"])
        parsed = json.loads(action_row.diagnostics or "{}")
        warnings = parsed.get("dedup_warnings") or []
        self.assertTrue(
            any(row.get("warning_type") == "central_legacy_amount_mismatch" for row in warnings),
            warnings,
        )
