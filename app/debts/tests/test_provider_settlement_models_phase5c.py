from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase

from accounts.models import AccountProfile
from billing.models import Provider
from debts.models import (
    DebtCauseType,
    DebtDirection,
    DebtRecord,
    DebtStatus,
    OtherPartyType,
    ProviderSettlementAction,
    ProviderSettlementActionStatus,
    ProviderSettlementActionType,
    ProviderSettlementAllocation,
    ProviderSettlementDebtSource,
)
from financials.models import MoneyContainer


class ProviderSettlementPersistencePhase5CTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.manager = user_model.objects.create_user(
            username="phase5c_model_mgr",
            password="123456",
        )
        AccountProfile.objects.create(user=cls.manager, role=AccountProfile.Role.MANAGER)
        cls.provider = Provider.objects.create(name="Phase5C Provider")
        cls.container = MoneyContainer.objects.create(
            name="Phase5C Drawer",
            container_type=MoneyContainer.ContainerType.DRAWER,
            is_active=True,
            created_by=cls.manager,
        )

    def _mk_action(self, **overrides) -> ProviderSettlementAction:
        payload = {
            "provider": self.provider,
            "action": ProviderSettlementActionType.PAY_PROVIDER,
            "currency": "SYP",
            "requested_amount": Decimal("100.00"),
            "eligible_total_remaining": Decimal("100.00"),
            "total_applied": Decimal("0.00"),
            "unallocated_amount": Decimal("100.00"),
            "money_container": self.container,
            "idempotency_key": "phase5c:idem:1",
            "status": ProviderSettlementActionStatus.PENDING,
            "created_by": self.manager,
        }
        payload.update(overrides)
        return ProviderSettlementAction.objects.create(**payload)

    def _mk_central_debt(self, *, cause_id: str = "phase5c-cause") -> DebtRecord:
        return DebtRecord.objects.create(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.MANUAL,
            cause_id=cause_id,
            source_app="tests",
            other_party_type=OtherPartyType.PROVIDER,
            other_party_id=str(self.provider.id),
            provider=self.provider,
            actor_username="phase5c",
            total_syp=Decimal("100.00"),
            total_usd=Decimal("0.00"),
            remaining_syp=Decimal("100.00"),
            remaining_usd=Decimal("0.00"),
            status=DebtStatus.OPEN,
        )

    def test_models_can_be_created_manually(self):
        action = self._mk_action()
        debt = self._mk_central_debt()
        alloc = ProviderSettlementAllocation.objects.create(
            action=action,
            sequence=1,
            debt_source=ProviderSettlementDebtSource.CENTRAL,
            direction=DebtDirection.PAYABLE,
            currency="SYP",
            central_debt=debt,
            debt_id=str(debt.id),
            debt_public_id=debt.public_id,
            cause_type=debt.cause_type,
            source_identity=f"central:{debt.id}",
            before_remaining=Decimal("100.00"),
            applied=Decimal("25.00"),
            after_remaining=Decimal("75.00"),
            would_close=False,
            closed_after=False,
        )
        self.assertTrue(action.id > 0)
        self.assertTrue(alloc.id > 0)
        self.assertEqual(action.allocations.count(), 1)

    def test_unique_provider_idempotency_key_is_enforced(self):
        self._mk_action(idempotency_key="phase5c:idem:unique")
        with self.assertRaises(IntegrityError):
            self._mk_action(idempotency_key="phase5c:idem:unique")

    def test_money_precision_guard_applies(self):
        with self.assertRaises(ValidationError):
            self._mk_action(requested_amount=Decimal("10.001"))

        action = self._mk_action(idempotency_key="phase5c:idem:prec")
        debt = self._mk_central_debt(cause_id="phase5c-cause-prec")
        with self.assertRaises(ValidationError):
            ProviderSettlementAllocation.objects.create(
                action=action,
                sequence=1,
                debt_source=ProviderSettlementDebtSource.CENTRAL,
                direction=DebtDirection.PAYABLE,
                currency="SYP",
                central_debt=debt,
                before_remaining=Decimal("10.00"),
                applied=Decimal("1.001"),
                after_remaining=Decimal("8.999"),
            )

    def test_allocations_preserve_before_applied_after_values(self):
        action = self._mk_action(idempotency_key="phase5c:idem:values")
        debt = self._mk_central_debt(cause_id="phase5c-cause-values")
        alloc = ProviderSettlementAllocation.objects.create(
            action=action,
            sequence=1,
            debt_source=ProviderSettlementDebtSource.CENTRAL,
            direction=DebtDirection.PAYABLE,
            currency="SYP",
            central_debt=debt,
            before_remaining=Decimal("80.00"),
            applied=Decimal("30.00"),
            after_remaining=Decimal("50.00"),
            would_close=False,
            closed_after=False,
        )
        alloc.refresh_from_db()
        self.assertEqual(alloc.before_remaining, Decimal("80.00"))
        self.assertEqual(alloc.applied, Decimal("30.00"))
        self.assertEqual(alloc.after_remaining, Decimal("50.00"))

    def test_choice_fields_validate(self):
        action = ProviderSettlementAction(
            provider=self.provider,
            action="bad-action",
            currency="EUR",
            requested_amount=Decimal("1.00"),
            eligible_total_remaining=Decimal("1.00"),
            total_applied=Decimal("0.00"),
            unallocated_amount=Decimal("1.00"),
            money_container=self.container,
            idempotency_key="phase5c:idem:choices",
            status="bad-status",
        )
        with self.assertRaises(ValidationError):
            action.full_clean()
