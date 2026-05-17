from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from typing import Any
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase

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
from debts.provider_account_settlement_reversal import reverse_provider_account_settlement
from debts.provider_position import get_provider_net_position
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


class _ProviderSettlementReversalBase:
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.manager = user_model.objects.create_user(
            username=f"m5e_rev_mgr_{cls.__name__}",
            password="123456",
        )
        AccountProfile.objects.create(user=cls.manager, role=AccountProfile.Role.MANAGER)

    def setUp(self):
        self.provider = Provider.objects.create(name=f"M5E Reversal Provider {self._testMethodName}")
        self._cause_seq = 0

        self.container = MoneyContainer.objects.create(
            name=f"M5E Reversal Cash {self._testMethodName}",
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
            actor_username="m5e-reversal",
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
        source_app: str = "billing",
        source_model: str = "Bill",
        source_id: str = "",
        currency_code: str = "SYP",
    ) -> DebtorDebt:
        return DebtorDebt.objects.create(
            provider=self.provider,
            source_app=source_app,
            source_model=source_model,
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
        source_app: str = "billing",
        source_model: str = "ProviderReturn",
        source_id: str = "",
        currency_code: str = "SYP",
    ) -> CreditorDebt:
        return CreditorDebt.objects.create(
            provider=self.provider,
            source_app=source_app,
            source_model=source_model,
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
    ) -> dict[str, Any]:
        return execute_provider_account_settlement(
            provider_id=self.provider.id,
            action=action,
            currency=currency,
            amount=amount,
            money_container_id=self.container.id,
            idempotency_key=idempotency_key,
            user=self.manager,
        )

    def _reverse(self, *, action_id: int, idempotency_key: str, reason: str = "") -> dict[str, Any]:
        return reverse_provider_account_settlement(
            action_id=action_id,
            idempotency_key=idempotency_key,
            user=self.manager,
            reason=reason,
        )


class ProviderAccountSettlementReversalHardeningM5ETests(_ProviderSettlementReversalBase, TestCase):
    def _assert_rollback_integrity(
        self,
        *,
        original_action_id: int,
        original_receipt_id: int,
        expected_receipt_status: str,
    ) -> None:
        original_action = ProviderSettlementAction.objects.get(pk=original_action_id)
        original_receipt = Receipt.objects.get(pk=original_receipt_id)
        self.assertEqual(original_action.status, ProviderSettlementActionStatus.COMMITTED)
        self.assertFalse(original_action.reversed_by_action_id)
        self.assertEqual(original_receipt.status, expected_receipt_status)
        self.assertEqual(Receipt.objects.filter(reverses_id=original_receipt_id).count(), 0)
        self.assertEqual(ProviderSettlementAction.objects.count(), 1)

    def test_projection_restores_exactly_after_execute_then_reverse(self):
        self._create_central_debt(
            direction=DebtDirection.PAYABLE,
            remaining_syp="1000.00",
        )
        before_projection = get_provider_net_position(provider_id=self.provider.id)

        executed = self._execute(
            action="pay_provider",
            currency="SYP",
            amount="250.00",
            idempotency_key=f"{self._testMethodName}:exec",
        )
        mid_projection = get_provider_net_position(provider_id=self.provider.id)
        self.assertNotEqual(mid_projection, before_projection)

        self._reverse(
            action_id=int(executed["action_id"]),
            idempotency_key=f"{self._testMethodName}:rev",
            reason="projection parity check",
        )
        after_projection = get_provider_net_position(provider_id=self.provider.id)
        self.assertEqual(after_projection, before_projection)

    def test_rollback_after_receipt_reversal_before_original_action_status_update(self):
        debt = self._create_central_debt(direction=DebtDirection.PAYABLE, remaining_syp="900.00")
        executed = self._execute(
            action="pay_provider",
            currency="SYP",
            amount="200.00",
            idempotency_key=f"{self._testMethodName}:exec",
        )
        original_action_id = int(executed["action_id"])
        original_receipt_id = int(executed["receipt_id"])
        debt.refresh_from_db()
        after_exec_remaining = debt.remaining_syp

        import debts.provider_account_settlement_reversal as ReverseSV

        orig_save = ReverseSV.ProviderSettlementAction.save

        def fail_on_original_reversed(self, *args, **kwargs):
            if (
                int(getattr(self, "id", 0) or 0) == original_action_id
                and getattr(self, "status", "") == ProviderSettlementActionStatus.REVERSED
            ):
                raise RuntimeError("forced failure after receipt reversal before original action update")
            return orig_save(self, *args, **kwargs)

        with patch(
            "debts.provider_account_settlement_reversal.ProviderSettlementAction.save",
            new=fail_on_original_reversed,
        ):
            with self.assertRaises(RuntimeError):
                self._reverse(
                    action_id=original_action_id,
                    idempotency_key=f"{self._testMethodName}:rev",
                    reason="rollback after receipt reversal",
                )

        debt.refresh_from_db()
        self.assertEqual(debt.remaining_syp, after_exec_remaining)
        self._assert_rollback_integrity(
            original_action_id=original_action_id,
            original_receipt_id=original_receipt_id,
            expected_receipt_status=ReceiptStatus.POSTED,
        )

    def test_rollback_when_central_restore_save_fails(self):
        debt = self._create_central_debt(direction=DebtDirection.PAYABLE, remaining_syp="1000.00")
        executed = self._execute(
            action="pay_provider",
            currency="SYP",
            amount="300.00",
            idempotency_key=f"{self._testMethodName}:exec",
        )
        original_action_id = int(executed["action_id"])
        original_receipt_id = int(executed["receipt_id"])
        debt.refresh_from_db()
        after_exec_remaining = debt.remaining_syp

        import debts.provider_account_settlement_reversal as ReverseSV

        orig_save = ReverseSV.DebtRecord.save

        def fail_target_central(self, *args, **kwargs):
            if int(getattr(self, "id", 0) or 0) == int(debt.id):
                raise RuntimeError("forced central restore failure")
            return orig_save(self, *args, **kwargs)

        with patch(
            "debts.provider_account_settlement_reversal.DebtRecord.save",
            new=fail_target_central,
        ):
            with self.assertRaises(RuntimeError):
                self._reverse(
                    action_id=original_action_id,
                    idempotency_key=f"{self._testMethodName}:rev",
                    reason="central restore failure",
                )

        debt.refresh_from_db()
        self.assertEqual(debt.remaining_syp, after_exec_remaining)
        self._assert_rollback_integrity(
            original_action_id=original_action_id,
            original_receipt_id=original_receipt_id,
            expected_receipt_status=ReceiptStatus.POSTED,
        )

    def test_rollback_when_legacy_payable_restore_save_fails(self):
        legacy = self._create_legacy_debtor(total="1000.00", paid_amount="0.00")
        executed = self._execute(
            action="pay_provider",
            currency="SYP",
            amount="300.00",
            idempotency_key=f"{self._testMethodName}:exec",
        )
        original_action_id = int(executed["action_id"])
        original_receipt_id = int(executed["receipt_id"])
        legacy.refresh_from_db()
        after_exec_paid = legacy.paid_amount

        import debts.provider_account_settlement_reversal as ReverseSV

        orig_save = ReverseSV.DebtorDebt.save

        def fail_target_legacy(self, *args, **kwargs):
            if int(getattr(self, "id", 0) or 0) == int(legacy.id):
                raise RuntimeError("forced legacy payable restore failure")
            return orig_save(self, *args, **kwargs)

        with patch(
            "debts.provider_account_settlement_reversal.DebtorDebt.save",
            new=fail_target_legacy,
        ):
            with self.assertRaises(RuntimeError):
                self._reverse(
                    action_id=original_action_id,
                    idempotency_key=f"{self._testMethodName}:rev",
                    reason="legacy payable restore failure",
                )

        legacy.refresh_from_db()
        self.assertEqual(legacy.paid_amount, after_exec_paid)
        self._assert_rollback_integrity(
            original_action_id=original_action_id,
            original_receipt_id=original_receipt_id,
            expected_receipt_status=ReceiptStatus.POSTED,
        )

    def test_rollback_when_legacy_receivable_restore_save_fails(self):
        legacy = self._create_legacy_creditor(total="900.00", collected="100.00")
        executed = self._execute(
            action="receive_from_provider",
            currency="SYP",
            amount="200.00",
            idempotency_key=f"{self._testMethodName}:exec",
        )
        original_action_id = int(executed["action_id"])
        original_receipt_id = int(executed["receipt_id"])
        legacy.refresh_from_db()
        after_exec_collected = legacy.collected

        import debts.provider_account_settlement_reversal as ReverseSV

        orig_save = ReverseSV.CreditorDebt.save

        def fail_target_legacy(self, *args, **kwargs):
            if int(getattr(self, "id", 0) or 0) == int(legacy.id):
                raise RuntimeError("forced legacy receivable restore failure")
            return orig_save(self, *args, **kwargs)

        with patch(
            "debts.provider_account_settlement_reversal.CreditorDebt.save",
            new=fail_target_legacy,
        ):
            with self.assertRaises(RuntimeError):
                self._reverse(
                    action_id=original_action_id,
                    idempotency_key=f"{self._testMethodName}:rev",
                    reason="legacy receivable restore failure",
                )

        legacy.refresh_from_db()
        self.assertEqual(legacy.collected, after_exec_collected)
        self._assert_rollback_integrity(
            original_action_id=original_action_id,
            original_receipt_id=original_receipt_id,
            expected_receipt_status=ReceiptStatus.POSTED,
        )

    def test_coexistence_duplicate_reversal_restores_canonical_winner_only(self):
        legacy_mirror = self._create_legacy_debtor(
            total="1000.00",
            paid_amount="0.00",
            source_app="debts",
            source_model="ManualDebt",
            source_id="manual-dup",
        )
        central = self._create_central_debt(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.MANUAL,
            cause_id=str(legacy_mirror.id),
            remaining_syp="1000.00",
        )
        before_projection = get_provider_net_position(provider_id=self.provider.id)

        executed = self._execute(
            action="pay_provider",
            currency="SYP",
            amount="250.00",
            idempotency_key=f"{self._testMethodName}:exec",
        )
        alloc_rows = list(
            ProviderSettlementAllocation.objects
            .filter(action_id=int(executed["action_id"]))
            .order_by("sequence", "id")
            .values("central_debt_id", "legacy_debtor_debt_id")
        )
        self.assertEqual(len(alloc_rows), 1)
        self.assertEqual(int(alloc_rows[0]["central_debt_id"] or 0), int(central.id))
        self.assertFalse(alloc_rows[0]["legacy_debtor_debt_id"])

        central.refresh_from_db()
        legacy_mirror.refresh_from_db()
        self.assertEqual(central.remaining_syp, Decimal("750.00"))
        self.assertEqual(legacy_mirror.paid_amount, Decimal("0.00"))

        self._reverse(
            action_id=int(executed["action_id"]),
            idempotency_key=f"{self._testMethodName}:rev",
            reason="duplicate coexistence reversal",
        )

        central.refresh_from_db()
        legacy_mirror.refresh_from_db()
        self.assertEqual(central.remaining_syp, Decimal("1000.00"))
        self.assertEqual(legacy_mirror.paid_amount, Decimal("0.00"))
        after_projection = get_provider_net_position(provider_id=self.provider.id)
        self.assertEqual(after_projection, before_projection)


@skipUnless(
    connection.vendor == "postgresql",
    "Phase M5E reversal concurrency verification requires PostgreSQL row-lock semantics.",
)
class ProviderAccountSettlementReversalConcurrencyM5ETests(_ProviderSettlementReversalBase, TransactionTestCase):
    reset_sequences = True

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls._manager_id = int(cls.manager.id)

    def _reverse_call(
        self,
        *,
        action_id: int,
        idempotency_key: str,
        reason: str,
    ) -> tuple[str, Any]:
        close_old_connections()
        try:
            actor = get_user_model().objects.get(pk=self._manager_id)
            result = reverse_provider_account_settlement(
                action_id=action_id,
                idempotency_key=idempotency_key,
                user=actor,
                reason=reason,
            )
            return ("ok", result)
        except Exception as exc:  # pragma: no cover - explicit concurrency classification
            return ("err", exc)
        finally:
            close_old_connections()

    def _execute_call(
        self,
        *,
        action: str,
        currency: str,
        amount: str,
        idempotency_key: str,
    ) -> tuple[str, Any]:
        close_old_connections()
        try:
            actor = get_user_model().objects.get(pk=self._manager_id)
            result = execute_provider_account_settlement(
                provider_id=self.provider.id,
                action=action,
                currency=currency,
                amount=amount,
                money_container_id=self.container.id,
                idempotency_key=idempotency_key,
                user=actor,
            )
            return ("ok", result)
        except Exception as exc:  # pragma: no cover - explicit concurrency classification
            return ("err", exc)
        finally:
            close_old_connections()

    def _run_parallel(self, fn_specs: list[tuple[str, dict[str, Any]]]) -> list[tuple[str, Any]]:
        with ThreadPoolExecutor(max_workers=len(fn_specs)) as pool:
            futures = []
            for fn_name, spec in fn_specs:
                if fn_name == "reverse":
                    futures.append(pool.submit(self._reverse_call, **spec))
                else:
                    futures.append(pool.submit(self._execute_call, **spec))
        return [f.result() for f in futures]

    def test_same_reversal_key_same_target_parallel_replays_safely(self):
        self._create_central_debt(direction=DebtDirection.PAYABLE, remaining_syp="1000.00")
        executed = self._execute(
            action="pay_provider",
            currency="SYP",
            amount="250.00",
            idempotency_key=f"{self._testMethodName}:exec",
        )
        original_action_id = int(executed["action_id"])
        original_receipt_id = int(executed["receipt_id"])

        results = self._run_parallel(
            [
                ("reverse", {"action_id": original_action_id, "idempotency_key": "m5e:rev:same", "reason": "parallel replay"}),
                ("reverse", {"action_id": original_action_id, "idempotency_key": "m5e:rev:same", "reason": "parallel replay"}),
            ]
        )
        ok_rows = [row[1] for row in results if row[0] == "ok"]
        err_rows = [row[1] for row in results if row[0] == "err"]
        self.assertEqual(err_rows, [])
        self.assertEqual(len(ok_rows), 2)
        self.assertEqual(ok_rows[0]["reversal_action_id"], ok_rows[1]["reversal_action_id"])
        self.assertEqual(ok_rows[0]["reversal_receipt_id"], ok_rows[1]["reversal_receipt_id"])
        self.assertEqual(Receipt.objects.filter(reverses_id=original_receipt_id).count(), 1)

    def test_same_reversal_key_different_target_conflicts_cleanly(self):
        self._create_central_debt(direction=DebtDirection.PAYABLE, remaining_syp="1200.00", cause_id="rev-k-diff-target-1")
        self._create_central_debt(direction=DebtDirection.PAYABLE, remaining_syp="1200.00", cause_id="rev-k-diff-target-2")
        executed_a = self._execute(
            action="pay_provider",
            currency="SYP",
            amount="200.00",
            idempotency_key=f"{self._testMethodName}:exec-a",
        )
        executed_b = self._execute(
            action="pay_provider",
            currency="SYP",
            amount="200.00",
            idempotency_key=f"{self._testMethodName}:exec-b",
        )

        results = self._run_parallel(
            [
                ("reverse", {"action_id": int(executed_a["action_id"]), "idempotency_key": "m5e:rev:conflict", "reason": "A"}),
                ("reverse", {"action_id": int(executed_b["action_id"]), "idempotency_key": "m5e:rev:conflict", "reason": "B"}),
            ]
        )
        ok_rows = [row[1] for row in results if row[0] == "ok"]
        err_rows = [row[1] for row in results if row[0] == "err"]
        self.assertEqual(len(ok_rows), 1)
        self.assertEqual(len(err_rows), 1)
        self.assertIn("idempotency key conflict", str(err_rows[0]))

    def test_different_reversal_keys_same_original_action_no_double_reversal(self):
        self._create_central_debt(direction=DebtDirection.PAYABLE, remaining_syp="1000.00")
        executed = self._execute(
            action="pay_provider",
            currency="SYP",
            amount="250.00",
            idempotency_key=f"{self._testMethodName}:exec",
        )
        original_action_id = int(executed["action_id"])
        original_receipt_id = int(executed["receipt_id"])

        results = self._run_parallel(
            [
                ("reverse", {"action_id": original_action_id, "idempotency_key": "m5e:rev:k1", "reason": "parallel key 1"}),
                ("reverse", {"action_id": original_action_id, "idempotency_key": "m5e:rev:k2", "reason": "parallel key 2"}),
            ]
        )
        ok_rows = [row[1] for row in results if row[0] == "ok"]
        err_rows = [row[1] for row in results if row[0] == "err"]
        self.assertEqual(len(ok_rows), 1)
        self.assertEqual(len(err_rows), 1)
        self.assertIn("action already reversed", str(err_rows[0]))
        self.assertEqual(Receipt.objects.filter(reverses_id=original_receipt_id).count(), 1)

    def test_reversal_racing_with_downstream_mutation_is_safe(self):
        debt = self._create_central_debt(direction=DebtDirection.PAYABLE, remaining_syp="1000.00")
        executed = self._execute(
            action="pay_provider",
            currency="SYP",
            amount="200.00",
            idempotency_key=f"{self._testMethodName}:exec",
        )
        original_action_id = int(executed["action_id"])
        original_receipt_id = int(executed["receipt_id"])

        results = self._run_parallel(
            [
                ("reverse", {"action_id": original_action_id, "idempotency_key": "m5e:rev:race", "reason": "race"}),
                (
                    "execute",
                    {
                        "action": "pay_provider",
                        "currency": "SYP",
                        "amount": "100.00",
                        "idempotency_key": "m5e:exec:race-mutator",
                    },
                ),
            ]
        )
        rev_rows = [row for row in results if row[0] == "ok" and "reversal_action_id" in row[1]]
        rev_errs = [row[1] for row in results if row[0] == "err" and "downstream changes detected" in str(row[1])]
        exec_rows = [row for row in results if row[0] == "ok" and "allocation_count" in row[1]]
        self.assertGreaterEqual(len(exec_rows), 1)
        self.assertTrue(len(rev_rows) == 1 or len(rev_errs) == 1)
        debt.refresh_from_db()
        self.assertGreaterEqual(debt.remaining_syp, DEC0)
        self.assertLessEqual(debt.remaining_syp, Decimal("1000.00"))
        self.assertLessEqual(Receipt.objects.filter(reverses_id=original_receipt_id).count(), 1)

    def test_reversal_deadlock_stress_loop_reports_no_db_deadlocks(self):
        errors: list[Exception] = []
        for idx in range(10):
            self._create_central_debt(
                direction=DebtDirection.PAYABLE,
                remaining_syp="1000.00",
                cause_id=f"{self._testMethodName}:loop:{idx}",
            )
            executed = self._execute(
                action="pay_provider",
                currency="SYP",
                amount="100.00",
                idempotency_key=f"{self._testMethodName}:exec:{idx}",
            )
            action_id = int(executed["action_id"])
            results = self._run_parallel(
                [
                    ("reverse", {"action_id": action_id, "idempotency_key": f"m5e:loop:{idx}:a", "reason": "loop a"}),
                    ("reverse", {"action_id": action_id, "idempotency_key": f"m5e:loop:{idx}:b", "reason": "loop b"}),
                ]
            )
            for status, payload in results:
                if status == "err" and "action already reversed" not in str(payload):
                    errors.append(payload)

        self.assertEqual(errors, [], [str(e) for e in errors])
