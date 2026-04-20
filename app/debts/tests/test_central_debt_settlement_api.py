from __future__ import annotations

import json
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
)
from financials import services as FinSV
from financials.models import (
    Currency,
    MoneyContainer,
    MoneyContainerCurrency,
    PostingLine,
    PostingTargetType,
    ReceiptKind,
    ReceiptStatus,
)


class CentralDebtSettlementApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.manager = user_model.objects.create_user(
            username="central_settle_mgr",
            password="123456",
        )
        AccountProfile.objects.create(user=cls.manager, role=AccountProfile.Role.MANAGER)

        cls.provider = Provider.objects.create(name="Central Settlement Provider")
        cls.container = MoneyContainer.objects.create(
            name="Central Settle Box",
            container_type=MoneyContainer.ContainerType.DRAWER,
            is_active=True,
            created_by=cls.manager,
        )
        cls.container.allowed_users.add(cls.manager)

        cls.syp, _ = Currency.objects.get_or_create(
            code="SYP",
            defaults={"name": "Syrian Pound", "decimals": 0, "is_active": True},
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

        FinSV.set_current_fx(actor=cls.manager, rate_syp_per_usd=Decimal("15000"))

    def setUp(self):
        self.client.force_login(self.manager)
        self._cause_seq = 0

    def _new_cause(self) -> str:
        self._cause_seq += 1
        return f"manual-settle-{self._cause_seq}"

    def _create_debt(
        self,
        *,
        direction: str = DebtDirection.PAYABLE,
        remaining_syp: Decimal = Decimal("1000"),
        remaining_usd: Decimal = Decimal("2"),
        total_syp: Decimal | None = None,
        total_usd: Decimal | None = None,
        fx_creation: Decimal | None = Decimal("10000"),
    ) -> DebtRecord:
        total_syp = remaining_syp if total_syp is None else total_syp
        total_usd = remaining_usd if total_usd is None else total_usd
        return DebtRecord.objects.create(
            direction=direction,
            cause_type=DebtCauseType.MANUAL,
            cause_id=self._new_cause(),
            source_app="debts",
            other_party_type=OtherPartyType.PROVIDER,
            other_party_id=str(self.provider.id),
            provider=self.provider,
            actor_username=self.manager.username,
            total_syp=total_syp,
            total_usd=total_usd,
            remaining_syp=remaining_syp,
            remaining_usd=remaining_usd,
            fx_syp_per_usd_at_creation=fx_creation,
            status=DebtStatus.OPEN,
        )

    def _settle(self, debt: DebtRecord, payload: dict) -> dict:
        resp = self.client.post(
            f"/manager/debts/api/record/{debt.public_id}/settle/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertTrue(data["ok"], data)
        return data

    def _assert_container_delta(self, *, receipt_id: int, currency: str, expected_amount: Decimal):
        line = PostingLine.objects.get(
            receipt_id=receipt_id,
            target_type=PostingTargetType.CONTAINER,
            currency__code=currency,
        )
        self.assertEqual(Decimal(line.amount), expected_amount)

    def test_partial_syp_reduces_syp_leg_only_without_totals_rewrite(self):
        debt = self._create_debt()

        self._settle(
            debt,
            {
                "cover_type": "partial",
                "payment_method": "syp_only",
                "money_container_id": self.container.id,
                "paid_syp": "400",
                "paid_usd": "0",
            },
        )

        debt.refresh_from_db()
        settlement = DebtSettlement.objects.get(debt=debt)
        self.assertEqual(debt.total_syp, Decimal("1000"))
        self.assertEqual(debt.total_usd, Decimal("2"))
        self.assertEqual(debt.remaining_syp, Decimal("600"))
        self.assertEqual(debt.remaining_usd, Decimal("2"))
        self.assertEqual(debt.status, DebtStatus.OPEN)
        self.assertEqual(settlement.payment_syp, Decimal("400"))
        self.assertEqual(settlement.payment_usd, Decimal("0"))
        self.assertEqual(settlement.applied_syp, Decimal("400"))
        self.assertEqual(settlement.applied_usd, Decimal("0"))
        self.assertEqual(settlement.receipt.kind, ReceiptKind.COUNTERPARTY_SETTLE)
        self.assertEqual(settlement.receipt.status, ReceiptStatus.POSTED)
        self._assert_container_delta(receipt_id=settlement.receipt_id, currency="SYP", expected_amount=Decimal("-400"))

    def test_partial_usd_reduces_usd_leg_only(self):
        debt = self._create_debt()

        self._settle(
            debt,
            {
                "cover_type": "partial",
                "payment_method": "usd_only",
                "money_container_id": self.container.id,
                "paid_syp": "0",
                "paid_usd": "1",
            },
        )

        debt.refresh_from_db()
        settlement = DebtSettlement.objects.get(debt=debt)
        self.assertEqual(debt.remaining_syp, Decimal("1000"))
        self.assertEqual(debt.remaining_usd, Decimal("1"))
        self.assertEqual(settlement.applied_syp, Decimal("0"))
        self.assertEqual(settlement.applied_usd, Decimal("1"))
        self._assert_container_delta(receipt_id=settlement.receipt_id, currency="USD", expected_amount=Decimal("-1"))

    def test_partial_syp_overflow_uses_current_fx_not_creation_fx(self):
        debt = self._create_debt(fx_creation=Decimal("10000"))
        self._settle(
            debt,
            {
                "cover_type": "partial",
                "payment_method": "syp_only",
                "money_container_id": self.container.id,
                "paid_syp": "16000",
                "paid_usd": "0",
            },
        )
        debt.refresh_from_db()
        self.assertEqual(debt.remaining_syp, Decimal("0"))
        self.assertEqual(debt.remaining_usd, Decimal("1"))

    def test_partial_usd_overflow_reduces_syp_leg(self):
        debt = self._create_debt()
        self._settle(
            debt,
            {
                "cover_type": "partial",
                "payment_method": "usd_only",
                "money_container_id": self.container.id,
                "paid_syp": "0",
                "paid_usd": "2.05",
            },
        )
        debt.refresh_from_db()
        self.assertEqual(debt.remaining_syp, Decimal("250"))
        self.assertEqual(debt.remaining_usd, Decimal("0"))

    def test_partial_mixed_with_overflow_preserves_factual_remaining_legs(self):
        debt = self._create_debt()
        self._settle(
            debt,
            {
                "cover_type": "partial",
                "payment_method": "mixed",
                "money_container_id": self.container.id,
                "paid_syp": "16000",
                "paid_usd": "0.5",
            },
        )
        debt.refresh_from_db()
        self.assertEqual(debt.total_syp, Decimal("1000"))
        self.assertEqual(debt.total_usd, Decimal("2"))
        self.assertEqual(debt.remaining_syp, Decimal("0"))
        self.assertEqual(debt.remaining_usd, Decimal("0.5"))

    def test_full_settlement_closes_debt_and_posts_both_currency_lines_when_separate(self):
        debt = self._create_debt()
        self._settle(
            debt,
            {
                "cover_type": "full",
                "payment_method": "separate",
                "money_container_id": self.container.id,
                "paid_syp": "0",
                "paid_usd": "0",
            },
        )
        debt.refresh_from_db()
        settlement = DebtSettlement.objects.get(debt=debt)
        self.assertEqual(debt.remaining_syp, Decimal("0"))
        self.assertEqual(debt.remaining_usd, Decimal("0"))
        self.assertEqual(debt.status, DebtStatus.CLOSED)
        self.assertEqual(settlement.payment_syp, Decimal("1000"))
        self.assertEqual(settlement.payment_usd, Decimal("2"))
        self.assertEqual(
            PostingLine.objects.filter(
                receipt=settlement.receipt,
                target_type=PostingTargetType.CONTAINER,
            ).count(),
            2,
        )

    def test_receivable_settlement_moves_cash_into_container(self):
        debt = self._create_debt(direction=DebtDirection.RECEIVABLE, remaining_syp=Decimal("500"), remaining_usd=Decimal("0"))
        self._settle(
            debt,
            {
                "cover_type": "partial",
                "payment_method": "syp_only",
                "money_container_id": self.container.id,
                "paid_syp": "300",
                "paid_usd": "0",
            },
        )
        debt.refresh_from_db()
        settlement = DebtSettlement.objects.get(debt=debt)
        self.assertEqual(debt.remaining_syp, Decimal("200"))
        self._assert_container_delta(receipt_id=settlement.receipt_id, currency="SYP", expected_amount=Decimal("300"))

    def test_validation_rejects_partial_equal_full(self):
        debt = self._create_debt()
        resp = self.client.post(
            f"/manager/debts/api/record/{debt.public_id}/settle/",
            data=json.dumps({
                "cover_type": "partial",
                "payment_method": "syp_only",
                "money_container_id": self.container.id,
                "paid_syp": "31000",
                "paid_usd": "0",
            }),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json()["ok"])

    def test_validation_rejects_partial_overpayment(self):
        debt = self._create_debt()
        resp = self.client.post(
            f"/manager/debts/api/record/{debt.public_id}/settle/",
            data=json.dumps({
                "cover_type": "partial",
                "payment_method": "syp_only",
                "money_container_id": self.container.id,
                "paid_syp": "31001",
                "paid_usd": "0",
            }),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json()["ok"])

    def test_validation_rejects_partial_separate_method(self):
        debt = self._create_debt()
        resp = self.client.post(
            f"/manager/debts/api/record/{debt.public_id}/settle/",
            data=json.dumps({
                "cover_type": "partial",
                "payment_method": "separate",
                "money_container_id": self.container.id,
                "paid_syp": "100",
                "paid_usd": "1",
            }),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json()["ok"])
