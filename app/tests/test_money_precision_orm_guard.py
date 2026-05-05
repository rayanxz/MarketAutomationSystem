from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from billing.models import Bill, Provider
from debts.models import DebtorDebt
from financials.models import Currency, MoneyContainer, PostingLine, PostingTargetType, Receipt, ReceiptKind
from pos.models import SalesBill


class MoneyPrecisionOrmGuardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.user = User.objects.create_user(username="orm_guard_u", password="pw")
        cls.provider = Provider.objects.create(name="ORM Guard Provider")
        cls.syp, _ = Currency.objects.get_or_create(
            code="SYP",
            defaults={"name": "Syrian Pound", "decimals": 2, "is_active": True},
        )
        cls.container = MoneyContainer.objects.create(
            name="ORM Guard Drawer",
            container_type=MoneyContainer.ContainerType.DRAWER,
            is_active=True,
            created_by=cls.user,
        )

    def test_debtor_debt_direct_save_rejects_more_than_2_decimals(self):
        with self.assertRaises(ValidationError):
            DebtorDebt.objects.create(
                source_app="debts",
                source_model="ManualDebt",
                source_id="manual:orm-guard",
                currency_code="SYP",
                total=Decimal("1.237"),
                paid_amount=Decimal("0.00"),
            )

    def test_bill_direct_save_rejects_more_than_2_decimals(self):
        with self.assertRaises(ValidationError):
            Bill.objects.create(
                provider=self.provider,
                creation_paid_syp=Decimal("1.237"),
                creation_paid_usd=Decimal("0.00"),
                total=Decimal("0.00"),
            )

    def test_sales_bill_direct_save_rejects_more_than_2_decimals(self):
        with self.assertRaises(ValidationError):
            SalesBill.objects.create(
                total_amount=Decimal("2.00"),
                paid_amount=Decimal("1.237"),
            )

    def test_posting_line_direct_save_rejects_more_than_2_decimals(self):
        receipt = Receipt.objects.create(
            kind=ReceiptKind.CASH_ADD,
            actor=self.user,
            note="guard test",
        )
        with self.assertRaises(ValidationError):
            PostingLine.objects.create(
                receipt=receipt,
                target_type=PostingTargetType.CONTAINER,
                container=self.container,
                counterparty=None,
                currency=self.syp,
                amount=Decimal("1.237"),
                meta_json="{}",
            )
