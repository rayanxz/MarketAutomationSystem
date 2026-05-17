from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

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
    ProviderSettlementActionType,
    ProviderSettlementAllocation,
)
from debts.provider_account_receipts import post_provider_account_settlement_receipt
from financials import services as FinSV
from financials.models import (
    ContainerFeature,
    Currency,
    MoneyContainer,
    MoneyContainerCurrency,
    PostingLine,
    PostingTargetType,
    Receipt,
    ReceiptKind,
)


class ProviderAccountReceiptAdapterPhase5DTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.manager = user_model.objects.create_user(
            username="phase5d_receipt_mgr",
            password="123456",
        )
        AccountProfile.objects.create(user=cls.manager, role=AccountProfile.Role.MANAGER)

        cls.provider = Provider.objects.create(name="Phase5D Provider")

        cls.container = MoneyContainer.objects.create(
            name="Phase5D Drawer",
            container_type=MoneyContainer.ContainerType.DRAWER,
            is_active=True,
            created_by=cls.manager,
        )
        cls.container.allowed_users.add(cls.manager)
        feature, _ = ContainerFeature.objects.get_or_create(
            code="provider_account_settlement",
            defaults={"name": "Provider Account Settlement", "is_active": True},
        )
        cls.container.features.add(feature)

        cls.syp, _ = Currency.objects.get_or_create(
            code="SYP",
            defaults={"name": "Syrian Pound", "decimals": 2, "is_active": True},
        )
        cls.usd, _ = Currency.objects.get_or_create(
            code="USD",
            defaults={"name": "US Dollar", "decimals": 2, "is_active": True},
        )
        MoneyContainerCurrency.objects.update_or_create(
            container=cls.container,
            currency=cls.syp,
            defaults={"is_enabled": True},
        )
        MoneyContainerCurrency.objects.update_or_create(
            container=cls.container,
            currency=cls.usd,
            defaults={"is_enabled": True},
        )

        FinSV.set_current_fx(actor=cls.manager, rate_syp_per_usd=Decimal("20000"))

    def _mk_action(
        self,
        *,
        action: str,
        currency: str,
        amount: str,
        idem_key: str,
    ) -> ProviderSettlementAction:
        return ProviderSettlementAction.objects.create(
            provider=self.provider,
            action=action,
            currency=currency,
            requested_amount=Decimal(amount),
            eligible_total_remaining=Decimal(amount),
            total_applied=Decimal("0.00"),
            unallocated_amount=Decimal(amount),
            money_container=self.container,
            idempotency_key=idem_key,
            created_by=self.manager,
        )

    def _receipt_lines(self, receipt_id: int):
        return list(
            PostingLine.objects
            .filter(receipt_id=receipt_id)
            .select_related("currency")
            .order_by("id")
        )

    def test_grouped_receipt_creation_and_action_link(self):
        action = self._mk_action(
            action=ProviderSettlementActionType.PAY_PROVIDER,
            currency="SYP",
            amount="100.00",
            idem_key="phase5d:k1",
        )
        result = post_provider_account_settlement_receipt(
            settlement_action_id=action.id,
            actor=self.manager,
        )
        action.refresh_from_db()
        receipt = Receipt.objects.get(pk=result.receipt_id)
        self.assertEqual(action.receipt_id, receipt.id)
        self.assertEqual(receipt.kind, ReceiptKind.COUNTERPARTY_SETTLE)
        self.assertEqual(receipt.source_app, "debts")
        self.assertEqual(receipt.source_model, "ProviderSettlementAction")
        self.assertFalse(result.reused)
        self.assertEqual(len(self._receipt_lines(receipt.id)), 2)

    def test_idempotent_replay_returns_same_receipt(self):
        action = self._mk_action(
            action=ProviderSettlementActionType.PAY_PROVIDER,
            currency="SYP",
            amount="70.00",
            idem_key="phase5d:k2",
        )
        first = post_provider_account_settlement_receipt(
            settlement_action_id=action.id,
            actor=self.manager,
        )
        second = post_provider_account_settlement_receipt(
            settlement_action_id=action.id,
            actor=self.manager,
        )
        self.assertEqual(first.receipt_id, second.receipt_id)
        self.assertTrue(second.reused)
        self.assertEqual(Receipt.objects.count(), 1)
        self.assertEqual(PostingLine.objects.count(), 2)

    def test_idempotency_conflict_rejects_when_payload_changes(self):
        action = self._mk_action(
            action=ProviderSettlementActionType.PAY_PROVIDER,
            currency="SYP",
            amount="90.00",
            idem_key="phase5d:k3",
        )
        post_provider_account_settlement_receipt(
            settlement_action_id=action.id,
            actor=self.manager,
        )
        with self.assertRaisesMessage(ValueError, "idempotency key conflict"):
            post_provider_account_settlement_receipt(
                settlement_action_id=action.id,
                actor=self.manager,
                amount="95.00",
            )
        self.assertEqual(Receipt.objects.count(), 1)

    def test_pay_provider_semantics(self):
        action = self._mk_action(
            action=ProviderSettlementActionType.PAY_PROVIDER,
            currency="SYP",
            amount="55.00",
            idem_key="phase5d:k4",
        )
        result = post_provider_account_settlement_receipt(
            settlement_action_id=action.id,
            actor=self.manager,
        )
        lines = self._receipt_lines(result.receipt_id)
        container_line = next(ln for ln in lines if ln.target_type == PostingTargetType.CONTAINER)
        counterparty_line = next(ln for ln in lines if ln.target_type == PostingTargetType.COUNTERPARTY)
        self.assertEqual(container_line.amount, Decimal("-55.00"))
        self.assertEqual(counterparty_line.amount, Decimal("55.00"))
        self.assertEqual(result.cash_amount_signed, Decimal("-55.00"))

    def test_receive_from_provider_semantics(self):
        action = self._mk_action(
            action=ProviderSettlementActionType.RECEIVE_FROM_PROVIDER,
            currency="SYP",
            amount="55.00",
            idem_key="phase5d:k5",
        )
        result = post_provider_account_settlement_receipt(
            settlement_action_id=action.id,
            actor=self.manager,
        )
        lines = self._receipt_lines(result.receipt_id)
        container_line = next(ln for ln in lines if ln.target_type == PostingTargetType.CONTAINER)
        counterparty_line = next(ln for ln in lines if ln.target_type == PostingTargetType.COUNTERPARTY)
        self.assertEqual(container_line.amount, Decimal("55.00"))
        self.assertEqual(counterparty_line.amount, Decimal("-55.00"))
        self.assertEqual(result.cash_amount_signed, Decimal("55.00"))

    def test_syp_only_receipt(self):
        action = self._mk_action(
            action=ProviderSettlementActionType.PAY_PROVIDER,
            currency="SYP",
            amount="42.00",
            idem_key="phase5d:k6",
        )
        result = post_provider_account_settlement_receipt(
            settlement_action_id=action.id,
            actor=self.manager,
        )
        lines = self._receipt_lines(result.receipt_id)
        self.assertTrue(lines)
        self.assertTrue(all(ln.currency.code == "SYP" for ln in lines))

    def test_usd_only_receipt(self):
        action = self._mk_action(
            action=ProviderSettlementActionType.RECEIVE_FROM_PROVIDER,
            currency="USD",
            amount="8.00",
            idem_key="phase5d:k7",
        )
        result = post_provider_account_settlement_receipt(
            settlement_action_id=action.id,
            actor=self.manager,
        )
        lines = self._receipt_lines(result.receipt_id)
        self.assertTrue(lines)
        self.assertTrue(all(ln.currency.code == "USD" for ln in lines))

    def test_no_debt_mutations_and_no_allocation_rows(self):
        debt = DebtRecord.objects.create(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.MANUAL,
            cause_id="phase5d-no-debt-mutation",
            source_app="tests",
            other_party_type=OtherPartyType.PROVIDER,
            other_party_id=str(self.provider.id),
            provider=self.provider,
            actor_username="phase5d",
            total_syp=Decimal("200.00"),
            total_usd=Decimal("0.00"),
            remaining_syp=Decimal("200.00"),
            remaining_usd=Decimal("0.00"),
            status=DebtStatus.OPEN,
        )
        before = {
            "remaining_syp": debt.remaining_syp,
            "remaining_usd": debt.remaining_usd,
            "status": debt.status,
        }
        before_settlements = DebtSettlement.objects.count()
        before_alloc_rows = ProviderSettlementAllocation.objects.count()

        action = self._mk_action(
            action=ProviderSettlementActionType.PAY_PROVIDER,
            currency="SYP",
            amount="15.00",
            idem_key="phase5d:k8",
        )
        post_provider_account_settlement_receipt(
            settlement_action_id=action.id,
            actor=self.manager,
        )

        debt.refresh_from_db()
        self.assertEqual(debt.remaining_syp, before["remaining_syp"])
        self.assertEqual(debt.remaining_usd, before["remaining_usd"])
        self.assertEqual(debt.status, before["status"])
        self.assertEqual(DebtSettlement.objects.count(), before_settlements)
        self.assertEqual(ProviderSettlementAllocation.objects.count(), before_alloc_rows)
