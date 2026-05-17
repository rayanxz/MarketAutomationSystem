from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from typing import Any
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection
from django.test import TransactionTestCase

from accounts.models import AccountProfile
from billing.models import Provider
from debts.models import (
    DebtCauseType,
    DebtDirection,
    DebtRecord,
    DebtSettlement,
    DebtStatus,
    OtherPartyType,
    ProviderSettlementAction,
    ProviderSettlementActionStatus,
    ProviderSettlementAllocation,
)
from debts.provider_account_allocator import simulate_provider_account_allocation
from debts.provider_account_settlement_execution import (
    _allocation_fingerprint,
    execute_provider_account_settlement,
)
from financials import services as FinSV
from financials.models import (
    ContainerFeature,
    Currency,
    MoneyContainer,
    MoneyContainerCurrency,
    PostingLine,
    Receipt,
)


@skipUnless(
    connection.vendor == "postgresql",
    "Phase 5G concurrency verification requires PostgreSQL row-lock semantics.",
)
class ProviderAccountSettlementConcurrencyPhase5GTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        user_model = get_user_model()
        self.manager = user_model.objects.create_user(
            username=f"phase5g_concurrency_mgr_{self._testMethodName}",
            password="123456",
        )
        AccountProfile.objects.create(user=self.manager, role=AccountProfile.Role.MANAGER)
        self.manager_id = int(self.manager.id)

        self.provider = Provider.objects.create(name=f"Phase5G Provider {self._testMethodName}")
        self.container = MoneyContainer.objects.create(
            name=f"Phase5G Drawer {self._testMethodName}",
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

    def _create_payable(
        self,
        *,
        cause_id: str,
        remaining_syp: str = "0.00",
        remaining_usd: str = "0.00",
    ) -> DebtRecord:
        return DebtRecord.objects.create(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.MANUAL,
            cause_id=cause_id,
            source_app="debts",
            other_party_type=OtherPartyType.PROVIDER,
            other_party_id=str(self.provider.id),
            provider=self.provider,
            actor_username="phase5g",
            total_syp=Decimal(remaining_syp),
            total_usd=Decimal(remaining_usd),
            remaining_syp=Decimal(remaining_syp),
            remaining_usd=Decimal(remaining_usd),
            status=DebtStatus.OPEN,
        )

    def _exec_call(
        self,
        *,
        action: str,
        currency: str,
        amount: str,
        idempotency_key: str,
        preview_fingerprint: str | None = None,
    ) -> tuple[str, Any]:
        close_old_connections()
        try:
            actor = get_user_model().objects.get(pk=self.manager_id)
            result = execute_provider_account_settlement(
                provider_id=self.provider.id,
                action=action,
                currency=currency,
                amount=amount,
                money_container_id=self.container.id,
                idempotency_key=idempotency_key,
                preview_fingerprint=preview_fingerprint,
                user=actor,
            )
            return ("ok", result)
        except Exception as exc:  # pragma: no cover - explicit concurrency classification
            return ("err", exc)
        finally:
            close_old_connections()

    def _run_parallel(self, call_specs: list[dict[str, Any]]) -> list[tuple[str, Any]]:
        with ThreadPoolExecutor(max_workers=len(call_specs)) as pool:
            futures = [pool.submit(self._exec_call, **spec) for spec in call_specs]
        return [f.result() for f in futures]

    def test_same_idempotency_key_same_payload_parallel_replays_safely(self):
        debt = self._create_payable(cause_id="phase5g-k-same", remaining_syp="1000.00")
        results = self._run_parallel(
            [
                {
                    "action": "pay_provider",
                    "currency": "SYP",
                    "amount": "500.00",
                    "idempotency_key": "phase5g:same-k",
                },
                {
                    "action": "pay_provider",
                    "currency": "SYP",
                    "amount": "500.00",
                    "idempotency_key": "phase5g:same-k",
                },
            ]
        )

        ok_rows = [row[1] for row in results if row[0] == "ok"]
        err_rows = [row[1] for row in results if row[0] == "err"]
        self.assertEqual(len(err_rows), 0, err_rows)
        self.assertEqual(len(ok_rows), 2)
        self.assertEqual(ok_rows[0]["action_id"], ok_rows[1]["action_id"])
        self.assertEqual(ok_rows[0]["receipt_id"], ok_rows[1]["receipt_id"])

        debt.refresh_from_db()
        self.assertEqual(debt.remaining_syp, Decimal("500.00"))
        self.assertEqual(ProviderSettlementAction.objects.count(), 1)
        self.assertEqual(Receipt.objects.count(), 1)
        self.assertEqual(ProviderSettlementAllocation.objects.count(), 1)
        self.assertEqual(DebtSettlement.objects.count(), 1)
        self.assertEqual(PostingLine.objects.count(), 2)

    def test_same_idempotency_key_different_payload_parallel_conflicts_cleanly(self):
        debt = self._create_payable(cause_id="phase5g-k-conflict", remaining_syp="1500.00")
        results = self._run_parallel(
            [
                {
                    "action": "pay_provider",
                    "currency": "SYP",
                    "amount": "500.00",
                    "idempotency_key": "phase5g:conflict-k",
                },
                {
                    "action": "pay_provider",
                    "currency": "SYP",
                    "amount": "600.00",
                    "idempotency_key": "phase5g:conflict-k",
                },
            ]
        )

        ok_rows = [row[1] for row in results if row[0] == "ok"]
        err_rows = [row[1] for row in results if row[0] == "err"]
        self.assertEqual(len(ok_rows), 1)
        self.assertEqual(len(err_rows), 1)
        self.assertIn("idempotency key conflict", str(err_rows[0]))

        debt.refresh_from_db()
        self.assertIn(debt.remaining_syp, {Decimal("1000.00"), Decimal("900.00")})
        self.assertEqual(ProviderSettlementAction.objects.count(), 1)
        self.assertEqual(Receipt.objects.count(), 1)
        self.assertEqual(DebtSettlement.objects.count(), 1)
        self.assertEqual(ProviderSettlementAllocation.objects.count(), 1)

    def test_different_idempotency_keys_parallel_no_overapplication(self):
        debt = self._create_payable(cause_id="phase5g-no-over", remaining_syp="10000.00")
        results = self._run_parallel(
            [
                {
                    "action": "pay_provider",
                    "currency": "SYP",
                    "amount": "7000.00",
                    "idempotency_key": "phase5g:race-a",
                },
                {
                    "action": "pay_provider",
                    "currency": "SYP",
                    "amount": "7000.00",
                    "idempotency_key": "phase5g:race-b",
                },
            ]
        )

        ok_rows = [row[1] for row in results if row[0] == "ok"]
        err_rows = [row[1] for row in results if row[0] == "err"]
        self.assertEqual(len(ok_rows), 1)
        self.assertEqual(len(err_rows), 1)
        self.assertIn("amount exceeds eligible total", str(err_rows[0]))

        debt.refresh_from_db()
        self.assertEqual(debt.remaining_syp, Decimal("3000.00"))
        self.assertGreaterEqual(debt.remaining_syp, Decimal("0.00"))
        self.assertEqual(ProviderSettlementAction.objects.count(), 1)
        self.assertEqual(Receipt.objects.count(), 1)
        self.assertEqual(DebtSettlement.objects.count(), 1)

    def test_parallel_currency_lanes_remain_isolated(self):
        debt = self._create_payable(
            cause_id="phase5g-currency-lanes",
            remaining_syp="2000.00",
            remaining_usd="5.00",
        )
        results = self._run_parallel(
            [
                {
                    "action": "pay_provider",
                    "currency": "SYP",
                    "amount": "1000.00",
                    "idempotency_key": "phase5g:lane-syp",
                },
                {
                    "action": "pay_provider",
                    "currency": "USD",
                    "amount": "2.00",
                    "idempotency_key": "phase5g:lane-usd",
                },
            ]
        )

        err_rows = [row[1] for row in results if row[0] == "err"]
        self.assertEqual(len(err_rows), 0, err_rows)
        debt.refresh_from_db()
        self.assertEqual(debt.remaining_syp, Decimal("1000.00"))
        self.assertEqual(debt.remaining_usd, Decimal("3.00"))
        self.assertEqual(Receipt.objects.count(), 2)
        self.assertEqual(DebtSettlement.objects.count(), 2)
        self.assertEqual(ProviderSettlementAllocation.objects.count(), 2)
        self.assertEqual(PostingLine.objects.count(), 4)

    def test_stale_preview_rejected_after_concurrent_change(self):
        debt = self._create_payable(cause_id="phase5g-stale", remaining_syp="1000.00")
        preview = simulate_provider_account_allocation(
            provider_id=self.provider.id,
            action="pay_provider",
            currency="SYP",
            amount="300.00",
        )
        stale_fingerprint = _allocation_fingerprint(preview)

        mutator = self._exec_call(
            action="pay_provider",
            currency="SYP",
            amount="200.00",
            idempotency_key="phase5g:mutator",
        )
        self.assertEqual(mutator[0], "ok", mutator[1])

        before_counts = (
            ProviderSettlementAction.objects.count(),
            Receipt.objects.count(),
            ProviderSettlementAllocation.objects.count(),
            DebtSettlement.objects.count(),
        )
        stale = self._exec_call(
            action="pay_provider",
            currency="SYP",
            amount="300.00",
            idempotency_key="phase5g:stale-attempt",
            preview_fingerprint=stale_fingerprint,
        )
        self.assertEqual(stale[0], "err")
        self.assertIn("stale preview fingerprint", str(stale[1]))

        after_counts = (
            ProviderSettlementAction.objects.count(),
            Receipt.objects.count(),
            ProviderSettlementAllocation.objects.count(),
            DebtSettlement.objects.count(),
        )
        self.assertEqual(before_counts, after_counts)
        debt.refresh_from_db()
        self.assertEqual(debt.remaining_syp, Decimal("800.00"))

    def test_deadlock_stress_loop_reports_no_db_deadlocks(self):
        debt = self._create_payable(cause_id="phase5g-deadlock-loop", remaining_syp="100000.00")
        errors: list[Exception] = []
        for idx in range(20):
            results = self._run_parallel(
                [
                    {
                        "action": "pay_provider",
                        "currency": "SYP",
                        "amount": "500.00",
                        "idempotency_key": f"phase5g:loop:{idx}:a",
                    },
                    {
                        "action": "pay_provider",
                        "currency": "SYP",
                        "amount": "500.00",
                        "idempotency_key": f"phase5g:loop:{idx}:b",
                    },
                ]
            )
            for status, payload in results:
                if status == "err":
                    errors.append(payload)

        self.assertEqual(errors, [], [str(e) for e in errors])
        debt.refresh_from_db()
        self.assertEqual(debt.remaining_syp, Decimal("80000.00"))
        self.assertGreaterEqual(debt.remaining_syp, Decimal("0.00"))
        self.assertTrue(
            ProviderSettlementAction.objects.filter(
                status=ProviderSettlementActionStatus.COMMITTED,
                receipt__isnull=False,
            ).count()
            >= 40
        )
