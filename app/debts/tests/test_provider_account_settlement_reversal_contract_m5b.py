from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import AccountProfile
from billing.models import Provider
from debts.models import (
    CreditorDebt,
    DebtCauseType,
    DebtDirection,
    DebtRecord,
    DebtStatus,
    DebtorDebt,
    OtherPartyType,
    PartyType,
    ProviderSettlementAction,
    ProviderSettlementActionStatus,
    ProviderSettlementAllocation,
)
from debts.provider_account_settlement_execution import execute_provider_account_settlement
from financials import services as FinSV
from financials.models import (
    ContainerFeature,
    Currency,
    MoneyContainer,
    MoneyContainerCurrency,
    Receipt,
    ReceiptStatus,
)


DEC0 = Decimal("0.00")


class ProviderAccountSettlementReversalContractM5BTests(TestCase):
    """
    Phase M5B contract tests for future provider-account settlement reversal service.

    These tests freeze reversal behavior before implementation.
    """

    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.manager = user_model.objects.create_user(
            username="m5b_reversal_mgr",
            password="123456",
        )
        AccountProfile.objects.create(user=cls.manager, role=AccountProfile.Role.MANAGER)

    def setUp(self):
        self.provider = Provider.objects.create(name=f"M5B Reversal Provider {self._testMethodName}")
        self._cause_seq = 0

        self.container = MoneyContainer.objects.create(
            name=f"M5B Reversal Cash {self._testMethodName}",
            container_type=MoneyContainer.ContainerType.DRAWER,
            is_active=True,
            created_by=self.manager,
        )
        self.container.allowed_users.add(self.manager)
        feature, _ = ContainerFeature.objects.get_or_create(
            code="provider_account_settlement",
            defaults={"name": "Provider Account Settlement", "is_active": True},
        )
        if not feature.is_active:
            feature.is_active = True
            feature.save(update_fields=["is_active"])
        self.container.features.add(feature)

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
        FinSV.set_current_fx(actor=self.manager, rate_syp_per_usd=Decimal("20000"))

    def _next_cause(self, prefix: str) -> str:
        self._cause_seq += 1
        return f"{prefix}-{self._cause_seq}"

    def _create_central_debt(
        self,
        *,
        direction: str,
        cause_type: str = DebtCauseType.MANUAL,
        cause_id: str = "",
        remaining_syp: str = "0.00",
        remaining_usd: str = "0.00",
    ) -> DebtRecord:
        return DebtRecord.objects.create(
            direction=direction,
            cause_type=cause_type,
            cause_id=(cause_id or self._next_cause("central")),
            source_app="debts",
            other_party_type=OtherPartyType.PROVIDER,
            other_party_id=str(self.provider.id),
            provider=self.provider,
            actor_username="m5b-reversal",
            total_syp=Decimal(remaining_syp),
            total_usd=Decimal(remaining_usd),
            remaining_syp=Decimal(remaining_syp),
            remaining_usd=Decimal(remaining_usd),
            status=DebtStatus.OPEN,
        )

    def _create_legacy_debtor(
        self,
        *,
        total: str,
        paid_amount: str = "0.00",
        source_id: str = "",
        currency_code: str = "SYP",
    ) -> DebtorDebt:
        return DebtorDebt.objects.create(
            provider=self.provider,
            source_app="billing",
            source_model="Bill",
            source_id=(source_id or self._next_cause("legacy-debtor")),
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
        total: str,
        collected: str = "0.00",
        source_id: str = "",
        currency_code: str = "SYP",
    ) -> CreditorDebt:
        return CreditorDebt.objects.create(
            provider=self.provider,
            source_app="billing",
            source_model="ProviderReturn",
            source_id=(source_id or self._next_cause("legacy-creditor")),
            total=Decimal(total),
            collected=Decimal(collected),
            status=CreditorDebt.Status.OPEN,
            party_type=PartyType.PROVIDER,
            party_name=self.provider.name,
            currency_code=currency_code,
        )

    def _execute(
        self,
        *,
        action: str,
        currency: str,
        amount: str,
        idempotency_key: str,
    ) -> dict:
        return execute_provider_account_settlement(
            provider_id=self.provider.id,
            action=action,
            currency=currency,
            amount=amount,
            money_container_id=self.container.id,
            idempotency_key=idempotency_key,
            user=self.manager,
        )

    def _reverse_targets(self):
        try:
            from debts.provider_account_settlement_reversal import (
                reverse_provider_account_settlement,
            )
            return reverse_provider_account_settlement
        except Exception as exc:
            self.fail(
                "Phase M5B reversal contract target missing: expected "
                "`debts.provider_account_settlement_reversal.reverse_provider_account_settlement(...)` "
                f"to exist in future implementation phase. Import error: {exc!r}"
            )

    def _reverse(self, *, action_id: int, idempotency_key: str, reason: str = "") -> dict:
        target = self._reverse_targets()
        return target(
            action_id=action_id,
            idempotency_key=idempotency_key,
            user=self.manager,
            reason=reason,
        )

    def _snapshot_allocations(self, *, action_id: int) -> list[tuple]:
        rows = (
            ProviderSettlementAllocation.objects
            .filter(action_id=action_id)
            .order_by("sequence", "id")
            .values_list(
                "id",
                "sequence",
                "debt_source",
                "direction",
                "currency",
                "before_remaining",
                "applied",
                "after_remaining",
                "central_debt_id",
                "legacy_debtor_debt_id",
                "legacy_creditor_debt_id",
                "debt_settlement_id",
                "debtor_payment_id",
                "creditor_receipt_id",
            )
        )
        return list(rows)

    def test_case_01_reverse_basic_central_action_contract(self):
        debt = self._create_central_debt(
            direction=DebtDirection.PAYABLE,
            remaining_syp="1000.00",
        )
        executed = self._execute(
            action="pay_provider",
            currency="SYP",
            amount="300.00",
            idempotency_key=f"{self._testMethodName}:exec",
        )
        original_action = ProviderSettlementAction.objects.get(pk=executed["action_id"])
        original_receipt_id = int(executed["receipt_id"])

        self.assertEqual(debt.remaining_syp, Decimal("1000.00"))
        debt.refresh_from_db()
        self.assertEqual(debt.remaining_syp, Decimal("700.00"))

        result = self._reverse(
            action_id=original_action.id,
            idempotency_key=f"{self._testMethodName}:rev",
            reason="manual correction",
        )

        debt.refresh_from_db()
        original_action.refresh_from_db()
        self.assertEqual(result["original_action_id"], original_action.id)
        self.assertEqual(result["original_receipt_id"], original_receipt_id)
        self.assertEqual(result["status"], "reversed")
        self.assertEqual(debt.remaining_syp, Decimal("1000.00"))
        self.assertEqual(original_action.status, ProviderSettlementActionStatus.REVERSED)
        self.assertTrue(original_action.reversed_by_action_id)
        self.assertEqual(
            Receipt.objects.filter(id=original_receipt_id).values_list("status", flat=True).first(),
            ReceiptStatus.REVERSED,
        )
        self.assertEqual(
            Receipt.objects.filter(reverses_id=original_receipt_id, status=ReceiptStatus.POSTED).count(),
            1,
        )

    def test_case_02_reverse_legacy_payable_action_contract(self):
        legacy = self._create_legacy_debtor(total="1000.00", paid_amount="0.00")
        executed = self._execute(
            action="pay_provider",
            currency="SYP",
            amount="400.00",
            idempotency_key=f"{self._testMethodName}:exec",
        )
        legacy.refresh_from_db()
        self.assertEqual(legacy.paid_amount, Decimal("400.00"))
        self.assertEqual(legacy.status, DebtorDebt.Status.OPEN)

        self._reverse(
            action_id=int(executed["action_id"]),
            idempotency_key=f"{self._testMethodName}:rev",
            reason="legacy payable rollback",
        )

        legacy.refresh_from_db()
        self.assertEqual(legacy.paid_amount, Decimal("0.00"))
        self.assertEqual(legacy.status, DebtorDebt.Status.OPEN)

    def test_case_03_reverse_legacy_receivable_action_contract(self):
        legacy = self._create_legacy_creditor(total="1200.00", collected="200.00")
        executed = self._execute(
            action="receive_from_provider",
            currency="SYP",
            amount="300.00",
            idempotency_key=f"{self._testMethodName}:exec",
        )
        legacy.refresh_from_db()
        self.assertEqual(legacy.collected, Decimal("500.00"))
        self.assertEqual(legacy.status, CreditorDebt.Status.OPEN)

        self._reverse(
            action_id=int(executed["action_id"]),
            idempotency_key=f"{self._testMethodName}:rev",
            reason="legacy receivable rollback",
        )

        legacy.refresh_from_db()
        self.assertEqual(legacy.collected, Decimal("200.00"))
        self.assertEqual(legacy.status, CreditorDebt.Status.OPEN)

    def test_case_04_idempotent_reversal_replay_contract(self):
        debt = self._create_central_debt(direction=DebtDirection.PAYABLE, remaining_syp="800.00")
        executed = self._execute(
            action="pay_provider",
            currency="SYP",
            amount="200.00",
            idempotency_key=f"{self._testMethodName}:exec",
        )
        original_receipt_id = int(executed["receipt_id"])
        rev_key = f"{self._testMethodName}:rev"
        first = self._reverse(
            action_id=int(executed["action_id"]),
            idempotency_key=rev_key,
            reason="operator replay check",
        )
        second = self._reverse(
            action_id=int(executed["action_id"]),
            idempotency_key=rev_key,
            reason="operator replay check",
        )

        self.assertEqual(first["reversal_action_id"], second["reversal_action_id"])
        self.assertEqual(first["reversal_receipt_id"], second["reversal_receipt_id"])
        self.assertEqual(
            Receipt.objects.filter(reverses_id=original_receipt_id, status=ReceiptStatus.POSTED).count(),
            1,
        )
        debt.refresh_from_db()
        self.assertEqual(debt.remaining_syp, Decimal("800.00"))

    def test_case_05_reversal_idempotency_conflict_contract(self):
        self._create_central_debt(direction=DebtDirection.PAYABLE, remaining_syp="900.00")
        executed = self._execute(
            action="pay_provider",
            currency="SYP",
            amount="100.00",
            idempotency_key=f"{self._testMethodName}:exec",
        )
        rev_key = f"{self._testMethodName}:rev"
        self._reverse(
            action_id=int(executed["action_id"]),
            idempotency_key=rev_key,
            reason="initial reason",
        )
        with self.assertRaisesMessage(ValueError, "idempotency key conflict"):
            self._reverse(
                action_id=int(executed["action_id"]),
                idempotency_key=rev_key,
                reason="different reason",
            )

    def test_case_06_already_reversed_action_rejects_new_key_contract(self):
        self._create_central_debt(direction=DebtDirection.PAYABLE, remaining_syp="750.00")
        executed = self._execute(
            action="pay_provider",
            currency="SYP",
            amount="250.00",
            idempotency_key=f"{self._testMethodName}:exec",
        )
        self._reverse(
            action_id=int(executed["action_id"]),
            idempotency_key=f"{self._testMethodName}:rev-1",
            reason="first reversal",
        )

        with self.assertRaisesMessage(ValueError, "action already reversed"):
            self._reverse(
                action_id=int(executed["action_id"]),
                idempotency_key=f"{self._testMethodName}:rev-2",
                reason="new key should reject",
            )

    def test_case_07_downstream_conflict_blocks_reversal_contract(self):
        debt = self._create_central_debt(direction=DebtDirection.PAYABLE, remaining_syp="1000.00")
        executed = self._execute(
            action="pay_provider",
            currency="SYP",
            amount="300.00",
            idempotency_key=f"{self._testMethodName}:exec",
        )
        original_action = ProviderSettlementAction.objects.get(pk=executed["action_id"])
        original_receipt = Receipt.objects.get(pk=executed["receipt_id"])

        DebtRecord.objects.filter(id=debt.id).update(remaining_syp=Decimal("650.00"))

        with self.assertRaisesMessage(ValueError, "downstream changes detected"):
            self._reverse(
                action_id=original_action.id,
                idempotency_key=f"{self._testMethodName}:rev",
                reason="should block due to changed debt state",
            )

        original_action.refresh_from_db()
        debt.refresh_from_db()
        original_receipt.refresh_from_db()
        self.assertEqual(original_action.status, ProviderSettlementActionStatus.COMMITTED)
        self.assertEqual(debt.remaining_syp, Decimal("650.00"))
        self.assertEqual(original_receipt.status, ReceiptStatus.POSTED)
        self.assertEqual(
            Receipt.objects.filter(reverses_id=original_receipt.id).count(),
            0,
        )

    def test_case_08_atomic_rollback_contract_for_reversal(self):
        debt = self._create_central_debt(direction=DebtDirection.PAYABLE, remaining_syp="900.00")
        executed = self._execute(
            action="pay_provider",
            currency="SYP",
            amount="200.00",
            idempotency_key=f"{self._testMethodName}:exec",
        )
        original_action = ProviderSettlementAction.objects.get(pk=executed["action_id"])
        original_receipt = Receipt.objects.get(pk=executed["receipt_id"])
        debt.refresh_from_db()
        before_remaining = debt.remaining_syp

        target = self._reverse_targets()
        module_name = target.__module__
        with patch(
            f"{module_name}.FinSV.reverse_receipt",
            side_effect=RuntimeError("forced reversal receipt failure"),
        ):
            with self.assertRaises(RuntimeError):
                self._reverse(
                    action_id=original_action.id,
                    idempotency_key=f"{self._testMethodName}:rev",
                    reason="rollback injection",
                )

        original_action.refresh_from_db()
        original_receipt.refresh_from_db()
        debt.refresh_from_db()
        self.assertEqual(original_action.status, ProviderSettlementActionStatus.COMMITTED)
        self.assertFalse(original_action.reversed_by_action_id)
        self.assertEqual(original_receipt.status, ReceiptStatus.POSTED)
        self.assertEqual(debt.remaining_syp, before_remaining)
        self.assertEqual(Receipt.objects.filter(reverses_id=original_receipt.id).count(), 0)

    def test_case_09_receipt_reversal_alone_is_not_provider_settlement_reversal(self):
        debt = self._create_central_debt(direction=DebtDirection.PAYABLE, remaining_syp="1000.00")
        executed = self._execute(
            action="pay_provider",
            currency="SYP",
            amount="300.00",
            idempotency_key=f"{self._testMethodName}:exec",
        )
        debt.refresh_from_db()
        self.assertEqual(debt.remaining_syp, Decimal("700.00"))

        original_receipt = Receipt.objects.get(pk=executed["receipt_id"])
        FinSV.reverse_receipt(
            actor=self.manager,
            receipt_id=original_receipt.id,
            reason_note="danger probe: receipt reversal only",
        )

        debt.refresh_from_db()
        original_receipt.refresh_from_db()
        self.assertEqual(
            debt.remaining_syp,
            Decimal("700.00"),
            "Raw receipt reversal must not be considered provider-settlement debt reversal.",
        )
        self.assertEqual(original_receipt.status, ReceiptStatus.REVERSED)
        self.assertTrue(Receipt.objects.filter(reverses_id=original_receipt.id).exists())

    def test_case_10_original_allocation_history_preserved_contract(self):
        self._create_central_debt(direction=DebtDirection.PAYABLE, remaining_syp="2000.00")
        self._create_central_debt(direction=DebtDirection.PAYABLE, remaining_syp="500.00")
        executed = self._execute(
            action="pay_provider",
            currency="SYP",
            amount="1200.00",
            idempotency_key=f"{self._testMethodName}:exec",
        )
        action_id = int(executed["action_id"])
        before_rows = self._snapshot_allocations(action_id=action_id)
        self.assertGreater(len(before_rows), 0)

        self._reverse(
            action_id=action_id,
            idempotency_key=f"{self._testMethodName}:rev",
            reason="history preservation",
        )

        after_rows = self._snapshot_allocations(action_id=action_id)
        self.assertEqual(before_rows, after_rows)
