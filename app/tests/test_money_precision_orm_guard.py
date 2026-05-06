from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection
from django.db.models.expressions import RawSQL
from django.test import TestCase
from django.utils import timezone

from billing.models import Bill, Provider
from debts.models import DebtCauseType, DebtDirection, DebtorDebt, DebtRecord, OtherPartyType
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


class MoneyPrecisionDbConstraintBypassTests(TestCase):
    def _mk_debtor_entry(self, source_id: str) -> DebtorDebt:
        return DebtorDebt.objects.create(
            source_app="debts",
            source_model="ManualDebt",
            source_id=source_id,
            currency_code="SYP",
            total=Decimal("10.00"),
            paid_amount=Decimal("0.00"),
            status="open",
            party_type="provider",
            party_name="x",
        )

    def _mk_debt_record(self, cause_id: str) -> DebtRecord:
        return DebtRecord.objects.create(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.MANUAL,
            cause_id=cause_id,
            source_app="tests",
            other_party_type=OtherPartyType.OTHER,
            other_party_id="manual",
            actor_username="tester",
            total_syp=Decimal("10.00"),
            total_usd=Decimal("0.00"),
            remaining_syp=Decimal("10.00"),
            remaining_usd=Decimal("0.00"),
        )

    def test_queryset_update_normalizes_to_2_decimals(self):
        entry = self._mk_debtor_entry("manual:db-check-update")
        DebtorDebt.objects.filter(pk=entry.pk).update(total=Decimal("1.237"))
        entry.refresh_from_db()
        self.assertEqual(entry.total, Decimal("1.24"))

    def test_bulk_create_normalizes_to_2_decimals(self):
        DebtorDebt.objects.bulk_create(
            [
                DebtorDebt(
                    source_app="debts",
                    source_model="ManualDebt",
                    source_id="manual:db-check-bulk-create",
                    currency_code="SYP",
                    total=Decimal("1.237"),
                    paid_amount=Decimal("0.00"),
                    status="open",
                    party_type="provider",
                    party_name="x",
                )
            ]
        )
        entry = DebtorDebt.objects.get(source_id="manual:db-check-bulk-create")
        self.assertEqual(entry.total, Decimal("1.24"))

    def test_bulk_update_normalizes_to_2_decimals(self):
        entry = self._mk_debtor_entry("manual:db-check-bulk-update")
        entry.total = Decimal("1.237")
        DebtorDebt.objects.bulk_update([entry], ["total"])
        entry.refresh_from_db()
        self.assertEqual(entry.total, Decimal("1.24"))

    def test_raw_sql_update_rejects_more_than_2_decimals(self):
        entry = self._mk_debtor_entry("manual:db-check-raw-update")
        with self.assertRaises(IntegrityError):
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE billing_debtorentry SET total='1.237' WHERE id=%s",
                    [entry.pk],
                )

    def test_queryset_update_rawsql_rejects_more_than_2_decimals(self):
        entry = self._mk_debtor_entry("manual:db-check-qs-rawsql")
        with self.assertRaises(IntegrityError):
            DebtorDebt.objects.filter(pk=entry.pk).update(total=RawSQL("1.237", []))

    def test_raw_sql_insert_rejects_more_than_2_decimals(self):
        with self.assertRaises(IntegrityError):
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO billing_debtorentry (
                        source_app, source_model, source_id, created_at, total, paid_amount,
                        status, party_type, party_name, currency_code, legacy_source_id
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    [
                        "debts",
                        "ManualDebt",
                        "manual:db-check-raw",
                        timezone.now(),
                        "1.237",
                        "0.00",
                        "open",
                        "provider",
                        "x",
                        "SYP",
                        "",
                    ],
                )

    def test_managed_table_queryset_update_normalizes_to_2_decimals(self):
        rec = self._mk_debt_record("db-check-record")
        DebtRecord.objects.filter(pk=rec.pk).update(total_syp=Decimal("1.237"), remaining_syp=Decimal("1.237"))
        rec.refresh_from_db()
        self.assertEqual(rec.total_syp, Decimal("1.24"))
        self.assertEqual(rec.remaining_syp, Decimal("1.24"))

    def test_managed_table_raw_sql_update_rejects_more_than_2_decimals(self):
        rec = self._mk_debt_record("db-check-record-raw")
        with self.assertRaises(IntegrityError):
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE debts_debtrecord SET total_syp='1.237', remaining_syp='1.237' WHERE id=%s",
                    [rec.pk],
                )

    def test_valid_2dp_update_still_succeeds(self):
        entry = self._mk_debtor_entry("manual:db-check-valid")
        DebtorDebt.objects.filter(pk=entry.pk).update(total=Decimal("1.23"))
        entry.refresh_from_db()
        self.assertEqual(entry.total, Decimal("1.23"))
