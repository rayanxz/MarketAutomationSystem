from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from accounts.models import AccountProfile
from billing import services as BillingSV
from billing.models import Provider
from catalog.models import Product, ProductCollection, ProductSet, UnitType
from debts.models import (
    DebtRecord,
    DebtSettlement,
    DebtDirection,
    DebtCauseType,
    DebtStatus,
)
from financials import services as FinSV
from financials.models import ContainerFeature, Currency, MoneyContainer, MoneyContainerCurrency, Receipt, ReceiptStatus
from stock.models import ProductContainer


class PurchaseBillCentralDebtLogicTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.actor = user_model.objects.create_user(username="central_debt_mgr", password="123")
        AccountProfile.objects.create(user=cls.actor, role=AccountProfile.Role.MANAGER)

        cls.syp, _ = Currency.objects.get_or_create(
            code="SYP",
            defaults={"name": "Syrian Pound", "decimals": 0, "is_active": True},
        )
        cls.usd, _ = Currency.objects.get_or_create(
            code="USD",
            defaults={"name": "US Dollar", "decimals": 2, "is_active": True},
        )

        cls.store, _ = ProductContainer.objects.get_or_create(
            code="store",
            defaults={"name": "Store", "is_store": True, "is_active": True},
        )

        cls.cash = MoneyContainer.objects.create(
            name="Central Debt Cash",
            container_type=MoneyContainer.ContainerType.DRAWER,
            is_active=True,
            created_by=cls.actor,
        )
        cls.cash.allowed_users.add(cls.actor)
        feature, _ = ContainerFeature.objects.get_or_create(
            code="purchase_bills",
            defaults={"name": "Purchase Bills", "is_active": True},
        )
        cls.cash.features.add(feature)
        MoneyContainerCurrency.objects.get_or_create(container=cls.cash, currency=cls.syp, defaults={"is_enabled": True})
        MoneyContainerCurrency.objects.get_or_create(container=cls.cash, currency=cls.usd, defaults={"is_enabled": True})

        cls.provider = Provider.objects.create(name="Central Debt Provider")
        col = ProductCollection.objects.create(name="Central Debt Collection")
        pset = ProductSet.objects.create(collection=col, name="Central Debt Set")

        cls.prod_syp = Product.objects.create(
            name="SYP Product",
            set=pset,
            unit_primary=UnitType.PIECE,
            allow_syp_purchasing=True,
            allow_usd_purchasing=False,
            allow_syp_sales=True,
            allow_usd_sales=False,
            default_cost_syp=Decimal("1000"),
            default_price_syp=Decimal("1200"),
        )
        cls.prod_usd = Product.objects.create(
            name="USD Product",
            set=pset,
            unit_primary=UnitType.PIECE,
            allow_syp_purchasing=False,
            allow_usd_purchasing=True,
            allow_syp_sales=False,
            allow_usd_sales=True,
            default_cost_usd=Decimal("2"),
            default_price_usd=Decimal("3"),
        )

        FinSV.set_current_fx(actor=cls.actor, rate_syp_per_usd=Decimal("15000"))

    def _mixed_items(self) -> list[dict]:
        return [
            {"product_id": self.prod_syp.id, "unit_index": 1, "qty_raw": "1", "cost": "1000", "currency": "SYP"},
            {"product_id": self.prod_usd.id, "unit_index": 1, "qty_raw": "1", "cost": "2", "currency": "USD"},
        ]

    def _syp_items(self) -> list[dict]:
        return [
            {"product_id": self.prod_syp.id, "unit_index": 1, "qty_raw": "1", "cost": "1000", "currency": "SYP"},
        ]

    def _create_bill(
        self,
        *,
        status: str,
        items: list[dict],
        payment_method: str | None = None,
        paid_syp: Decimal | None = None,
        paid_usd: Decimal | None = None,
        settlement_currency: str = "SYP",
    ):
        return BillingSV.create_bill(
            actor=self.actor,
            provider_id=self.provider.id,
            status=status,
            paid_amount=Decimal("0"),
            payment_status=status,
            payment_method=payment_method,
            paid_syp=paid_syp,
            paid_usd=paid_usd,
            items=items,
            container=self.store,
            money_container_id=self.cash.id,
            settlement_currency=settlement_currency,
            fx_usd_syp=Decimal("15000"),
        )

    def _debt_for_bill(self, bill_id: int) -> DebtRecord:
        return DebtRecord.objects.get(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id=str(bill_id),
        )

    def test_partial_rejects_equal_full_settlement(self):
        with self.assertRaises(ValidationError):
            self._create_bill(
                status="partial",
                items=self._syp_items(),
                payment_method="syp_only",
                paid_syp=Decimal("1000"),
                paid_usd=Decimal("0"),
            )

    def test_partial_rejects_over_settlement(self):
        with self.assertRaises(ValidationError):
            self._create_bill(
                status="partial",
                items=self._syp_items(),
                payment_method="syp_only",
                paid_syp=Decimal("1001"),
                paid_usd=Decimal("0"),
            )

    def test_not_paid_creates_one_open_central_debt_and_no_receipt(self):
        bill = self._create_bill(status="unpaid", items=self._mixed_items(), payment_method="none", paid_syp=Decimal("0"), paid_usd=Decimal("0"))
        debt = self._debt_for_bill(bill.id)
        self.assertEqual(DebtRecord.objects.filter(cause_type=DebtCauseType.PURCHASE_BILL, cause_id=str(bill.id)).count(), 1)
        self.assertEqual(debt.remaining_syp, Decimal("1000"))
        self.assertEqual(debt.remaining_usd, Decimal("2"))
        self.assertEqual(debt.status, DebtStatus.OPEN)
        self.assertEqual(
            Receipt.objects.filter(source_app="billing", source_model="Bill", source_id=str(bill.id), status=ReceiptStatus.POSTED).count(),
            0,
        )

    def test_syp_only_partial_mixed_bill_when_paid_syp_within_syp_leg(self):
        bill = self._create_bill(
            status="partial",
            items=self._mixed_items(),
            payment_method="syp_only",
            paid_syp=Decimal("1000"),
            paid_usd=Decimal("0"),
        )
        debt = self._debt_for_bill(bill.id)
        self.assertEqual(debt.remaining_syp, Decimal("0"))
        self.assertEqual(debt.remaining_usd, Decimal("2"))
        self.assertEqual(
            Receipt.objects.filter(source_app="billing", source_model="Bill", source_id=str(bill.id), status=ReceiptStatus.POSTED).count(),
            1,
        )

    def test_syp_only_partial_mixed_bill_when_paid_syp_over_syp_leg(self):
        bill = self._create_bill(
            status="partial",
            items=self._mixed_items(),
            payment_method="syp_only",
            paid_syp=Decimal("16000"),
            paid_usd=Decimal("0"),
        )
        debt = self._debt_for_bill(bill.id)
        self.assertEqual(debt.remaining_syp, Decimal("15000"))
        self.assertEqual(debt.remaining_usd, Decimal("0"))

    def test_usd_only_partial_mixed_bill_when_paid_usd_within_usd_leg(self):
        bill = self._create_bill(
            status="partial",
            items=self._mixed_items(),
            payment_method="usd_only",
            paid_syp=Decimal("0"),
            paid_usd=Decimal("1"),
            settlement_currency="USD",
        )
        debt = self._debt_for_bill(bill.id)
        self.assertEqual(debt.remaining_syp, Decimal("1000"))
        self.assertEqual(debt.remaining_usd, Decimal("1"))

    def test_usd_only_partial_mixed_bill_when_paid_usd_over_usd_leg(self):
        bill = self._create_bill(
            status="partial",
            items=self._mixed_items(),
            payment_method="usd_only",
            paid_syp=Decimal("0"),
            paid_usd=Decimal("2.05"),
            settlement_currency="USD",
        )
        debt = self._debt_for_bill(bill.id)
        self.assertEqual(debt.remaining_syp, Decimal("250"))
        self.assertEqual(debt.remaining_usd, Decimal("0"))

    def test_mixed_partial_keeps_currency_leg_limits_and_remaining(self):
        bill = self._create_bill(
            status="partial",
            items=self._mixed_items(),
            payment_method="mixed",
            paid_syp=Decimal("700"),
            paid_usd=Decimal("1.5"),
        )
        debt = self._debt_for_bill(bill.id)
        self.assertEqual(debt.remaining_syp, Decimal("300"))
        self.assertEqual(debt.remaining_usd, Decimal("0.5"))

    def test_mixed_partial_rejects_when_amount_exceeds_currency_leg(self):
        with self.assertRaises(ValidationError):
            self._create_bill(
                status="partial",
                items=self._mixed_items(),
                payment_method="mixed",
                paid_syp=Decimal("1001"),
                paid_usd=Decimal("1"),
            )

    def test_mixed_partial_rejects_when_both_legs_are_fully_paid(self):
        with self.assertRaises(ValidationError):
            self._create_bill(
                status="partial",
                items=self._mixed_items(),
                payment_method="mixed",
                paid_syp=Decimal("1000"),
                paid_usd=Decimal("2"),
            )

    def test_partial_creation_has_receipt_and_one_central_debt(self):
        bill = self._create_bill(
            status="partial",
            items=self._mixed_items(),
            payment_method="mixed",
            paid_syp=Decimal("400"),
            paid_usd=Decimal("0.5"),
        )
        self.assertEqual(
            Receipt.objects.filter(source_app="billing", source_model="Bill", source_id=str(bill.id), status=ReceiptStatus.POSTED).count(),
            2,
        )
        self.assertEqual(DebtSettlement.objects.filter(debt__cause_id=str(bill.id)).count(), 0)
        self.assertEqual(
            DebtRecord.objects.filter(
                direction=DebtDirection.PAYABLE,
                cause_type=DebtCauseType.PURCHASE_BILL,
                cause_id=str(bill.id),
            ).count(),
            1,
        )

    def test_debt_has_independent_identity_from_bill_reference(self):
        bill = self._create_bill(status="unpaid", items=self._mixed_items(), payment_method="none", paid_syp=Decimal("0"), paid_usd=Decimal("0"))
        debt = self._debt_for_bill(bill.id)
        self.assertEqual(debt.cause_id, str(bill.id))
        self.assertTrue(debt.public_id.startswith("D-"))
        self.assertNotEqual(debt.public_id, str(bill.id))

    def test_covering_central_debt_closes_and_overpayment_is_blocked(self):
        bill = self._create_bill(status="unpaid", items=self._syp_items(), payment_method="none", paid_syp=Decimal("0"), paid_usd=Decimal("0"))
        debt = self._debt_for_bill(bill.id)

        with self.assertRaises(ValueError):
            BillingSV.pay_partial(
                actor=self.actor,
                bill_id=bill.id,
                amount=Decimal("1001"),
                money_container_id=self.cash.id,
                currency_code="SYP",
            )

        BillingSV.pay_full(
            actor=self.actor,
            bill_id=bill.id,
            money_container_id=self.cash.id,
            currency_code="SYP",
        )
        debt.refresh_from_db()
        self.assertEqual(debt.remaining_syp, Decimal("0"))
        self.assertEqual(debt.remaining_usd, Decimal("0"))
        self.assertEqual(debt.status, DebtStatus.CLOSED)
        self.assertEqual(DebtSettlement.objects.filter(debt=debt).count(), 1)
