from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from accounts.models import AccountProfile
from billing import services as BillingSV
from billing.models import Provider
from catalog.models import Product, ProductCollection, ProductSet, UnitType
from debts.models import DebtCauseType, DebtDirection, DebtRecord
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
    ReceiptStatus,
)
from stock.models import ProductContainer


class PurchaseBillPaymentFullCoverageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.actor = user_model.objects.create_user(username="bill_payment_cov_mgr", password="123")
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
            name="Coverage Cash",
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
        MoneyContainerCurrency.objects.get_or_create(
            container=cls.cash,
            currency=cls.syp,
            defaults={"is_enabled": True},
        )
        MoneyContainerCurrency.objects.get_or_create(
            container=cls.cash,
            currency=cls.usd,
            defaults={"is_enabled": True},
        )

        cls.provider = Provider.objects.create(name="Coverage Provider")
        collection = ProductCollection.objects.create(name="Coverage Collection")
        pset = ProductSet.objects.create(collection=collection, name="Coverage Set")

        cls.prod_syp = Product.objects.create(
            name="Coverage SYP Product",
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
            name="Coverage USD Product",
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

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _q(self, currency_code: str, value: Decimal) -> Decimal:
        return FinSV.q_money(amount=Decimal(value), currency_code=currency_code)

    def _q_fx(self, value: Decimal) -> Decimal:
        return FinSV.q_fx(Decimal(value))

    def _mixed_items(self, *, total_syp: str = "1000", total_usd: str = "2") -> list[dict]:
        return [
            {
                "product_id": self.prod_syp.id,
                "unit_index": 1,
                "qty_raw": "1",
                "cost": str(total_syp),
                "currency": "SYP",
            },
            {
                "product_id": self.prod_usd.id,
                "unit_index": 1,
                "qty_raw": "1",
                "cost": str(total_usd),
                "currency": "USD",
            },
        ]

    def _syp_items(self, *, total_syp: str = "1000") -> list[dict]:
        return [
            {
                "product_id": self.prod_syp.id,
                "unit_index": 1,
                "qty_raw": "1",
                "cost": str(total_syp),
                "currency": "SYP",
            },
        ]

    def _usd_items(self, *, total_usd: str = "2") -> list[dict]:
        return [
            {
                "product_id": self.prod_usd.id,
                "unit_index": 1,
                "qty_raw": "1",
                "cost": str(total_usd),
                "currency": "USD",
            },
        ]

    def _create_bill(
        self,
        *,
        status: str,
        items: list[dict],
        payment_method: str | None,
        paid_syp: Decimal | None,
        paid_usd: Decimal | None,
        settlement_currency: str = "SYP",
        fx_syp_per_usd: Decimal = Decimal("15000"),
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
            fx_usd_syp=fx_syp_per_usd,
        )

    def _debt_for_bill(self, bill):
        return (
            DebtRecord.objects
            .filter(
                direction=DebtDirection.PAYABLE,
                cause_type=DebtCauseType.PURCHASE_BILL,
                cause_id=bill.public_id,
            )
            .first()
        )

    def _posted_receipts_for_bill(self, bill):
        return list(
            Receipt.objects.filter(
                source_app="billing",
                source_model="Bill",
                source_id=str(bill.id),
                status=ReceiptStatus.POSTED,
            ).order_by("id")
        )

    def _expected_remaining(
        self,
        *,
        total_syp: Decimal,
        total_usd: Decimal,
        paid_syp: Decimal,
        paid_usd: Decimal,
        fx_syp_per_usd: Decimal,
    ) -> tuple[Decimal, Decimal]:
        rem_syp = Decimal(self._q("SYP", total_syp))
        rem_usd = Decimal(self._q("USD", total_usd))
        pool_syp = Decimal(self._q("SYP", paid_syp))
        pool_usd = Decimal(self._q("USD", paid_usd))
        fx = Decimal(self._q_fx(fx_syp_per_usd))

        paid_syp_native = min(pool_syp, rem_syp)
        rem_syp -= paid_syp_native
        pool_syp -= paid_syp_native

        paid_usd_native = min(pool_usd, rem_usd)
        rem_usd -= paid_usd_native
        pool_usd -= paid_usd_native

        if pool_syp > 0 and rem_usd > 0:
            usd_extra = min(rem_usd, pool_syp / fx)
            rem_usd -= usd_extra
            pool_syp -= usd_extra * fx

        if pool_usd > 0 and rem_syp > 0:
            syp_extra = min(rem_syp, pool_usd * fx)
            rem_syp -= syp_extra
            pool_usd -= syp_extra / fx

        return self._q("SYP", max(Decimal("0"), rem_syp)), self._q("USD", max(Decimal("0"), rem_usd))

    def _assert_receipts_match_paid_components(
        self,
        *,
        bill,
        paid_syp: Decimal,
        paid_usd: Decimal,
        fx_syp_per_usd: Decimal,
    ):
        expected_paid_syp = self._q("SYP", paid_syp)
        expected_paid_usd = self._q("USD", paid_usd)
        expected_fx = self._q_fx(fx_syp_per_usd)
        expected_receipts_count = int(expected_paid_syp > 0) + int(expected_paid_usd > 0)

        receipts = self._posted_receipts_for_bill(bill)
        self.assertEqual(len(receipts), expected_receipts_count)
        for receipt in receipts:
            self.assertEqual(receipt.kind, ReceiptKind.COUNTERPARTY_SETTLE)
            self.assertEqual(receipt.fx_syp_per_usd, expected_fx)

        container_lines = list(
            PostingLine.objects.filter(
                receipt__in=receipts,
                target_type=PostingTargetType.CONTAINER,
            ).select_related("currency")
        )
        line_map: dict[str, Decimal] = {}
        for line in container_lines:
            line_map[line.currency.code] = line_map.get(line.currency.code, Decimal("0")) + Decimal(line.amount)

        if expected_paid_syp > 0:
            self.assertEqual(line_map.get("SYP"), -expected_paid_syp)
        else:
            self.assertNotIn("SYP", line_map)

        if expected_paid_usd > 0:
            self.assertEqual(line_map.get("USD"), -expected_paid_usd)
        else:
            self.assertNotIn("USD", line_map)

    def _assert_partial_snapshot(
        self,
        *,
        bill,
        paid_syp: Decimal,
        paid_usd: Decimal,
        fx_syp_per_usd: Decimal,
    ):
        debt = self._debt_for_bill(bill)
        self.assertIsNotNone(debt)

        exp_rem_syp, exp_rem_usd = self._expected_remaining(
            total_syp=bill.total_syp,
            total_usd=bill.total_usd,
            paid_syp=paid_syp,
            paid_usd=paid_usd,
            fx_syp_per_usd=fx_syp_per_usd,
        )

        self.assertEqual(debt.remaining_syp, exp_rem_syp)
        self.assertEqual(debt.remaining_usd, exp_rem_usd)
        self.assertEqual(debt.total_syp, exp_rem_syp)
        self.assertEqual(debt.total_usd, exp_rem_usd)
        self.assertGreaterEqual(debt.remaining_syp, Decimal("0"))
        self.assertGreaterEqual(debt.remaining_usd, Decimal("0"))

        self._assert_receipts_match_paid_components(
            bill=bill,
            paid_syp=paid_syp,
            paid_usd=paid_usd,
            fx_syp_per_usd=fx_syp_per_usd,
        )

    # ------------------------------------------------------------------
    # 1) PAYMENT STATUS COVERAGE
    # ------------------------------------------------------------------
    def test_unpaid_creates_full_debt_snapshot_and_no_receipts(self):
        bill = self._create_bill(
            status="unpaid",
            items=self._mixed_items(),
            payment_method="none",
            paid_syp=Decimal("0"),
            paid_usd=Decimal("0"),
        )
        debt = self._debt_for_bill(bill)
        self.assertIsNotNone(debt)
        self.assertEqual(debt.remaining_syp, self._q("SYP", bill.total_syp))
        self.assertEqual(debt.remaining_usd, self._q("USD", bill.total_usd))
        self.assertEqual(debt.total_syp, debt.remaining_syp)
        self.assertEqual(debt.total_usd, debt.remaining_usd)
        self.assertEqual(len(self._posted_receipts_for_bill(bill)), 0)

    def test_unpaid_rejects_non_none_methods_and_nonzero_amounts(self):
        invalid_payloads = [
            ("syp_only", Decimal("0"), Decimal("0")),
            ("usd_only", Decimal("0"), Decimal("0")),
            ("mixed", Decimal("0"), Decimal("0")),
            ("separate", Decimal("0"), Decimal("0")),
            ("none", Decimal("1"), Decimal("0")),
            ("none", Decimal("0"), Decimal("1")),
        ]
        for method, paid_syp, paid_usd in invalid_payloads:
            with self.subTest(method=method, paid_syp=paid_syp, paid_usd=paid_usd):
                with self.assertRaises(ValidationError):
                    self._create_bill(
                        status="unpaid",
                        items=self._mixed_items(),
                        payment_method=method,
                        paid_syp=paid_syp,
                        paid_usd=paid_usd,
                    )

    # ------------------------------------------------------------------
    # 2) FULLY PAID METHODS
    # ------------------------------------------------------------------
    def test_full_paid_syp_only_creates_no_debt_and_posts_syp_receipt(self):
        bill = self._create_bill(
            status="paid",
            items=self._mixed_items(),
            payment_method="syp_only",
            paid_syp=Decimal("31000"),
            paid_usd=Decimal("0"),
            settlement_currency="SYP",
        )
        self.assertIsNone(self._debt_for_bill(bill))
        self._assert_receipts_match_paid_components(
            bill=bill,
            paid_syp=Decimal("31000"),
            paid_usd=Decimal("0"),
            fx_syp_per_usd=Decimal("15000"),
        )

    def test_full_paid_usd_only_creates_no_debt_and_posts_usd_receipt(self):
        bill = self._create_bill(
            status="paid",
            items=self._mixed_items(),
            payment_method="usd_only",
            paid_syp=Decimal("0"),
            paid_usd=Decimal("2.07"),
            settlement_currency="USD",
        )
        self.assertIsNone(self._debt_for_bill(bill))
        self._assert_receipts_match_paid_components(
            bill=bill,
            paid_syp=Decimal("0"),
            paid_usd=Decimal("2.07"),
            fx_syp_per_usd=Decimal("15000"),
        )

    def test_full_paid_mixed_creates_no_debt_and_posts_two_receipts(self):
        bill = self._create_bill(
            status="paid",
            items=self._mixed_items(),
            payment_method="mixed",
            paid_syp=Decimal("1000"),
            paid_usd=Decimal("2"),
            settlement_currency="SYP",
        )
        self.assertIsNone(self._debt_for_bill(bill))
        self._assert_receipts_match_paid_components(
            bill=bill,
            paid_syp=Decimal("1000"),
            paid_usd=Decimal("2"),
            fx_syp_per_usd=Decimal("15000"),
        )

    def test_full_paid_separate_creates_no_debt_and_posts_two_receipts(self):
        bill = self._create_bill(
            status="paid",
            items=self._mixed_items(),
            payment_method="separate",
            paid_syp=Decimal("1000"),
            paid_usd=Decimal("2"),
            settlement_currency="SYP",
        )
        self.assertIsNone(self._debt_for_bill(bill))
        self._assert_receipts_match_paid_components(
            bill=bill,
            paid_syp=Decimal("1000"),
            paid_usd=Decimal("2"),
            fx_syp_per_usd=Decimal("15000"),
        )

    def test_full_paid_rejects_mismatch_against_full_total(self):
        with self.assertRaises(ValidationError):
            self._create_bill(
                status="paid",
                items=self._mixed_items(),
                payment_method="syp_only",
                paid_syp=Decimal("30998"),
                paid_usd=Decimal("0"),
                settlement_currency="SYP",
            )

    # ------------------------------------------------------------------
    # 3) PARTIAL: SYP ONLY (<, =, overflow)
    # ------------------------------------------------------------------
    def test_partial_syp_only_less_than_syp_leg(self):
        bill = self._create_bill(
            status="partial",
            items=self._mixed_items(),
            payment_method="syp_only",
            paid_syp=Decimal("400"),
            paid_usd=Decimal("0"),
        )
        self._assert_partial_snapshot(
            bill=bill,
            paid_syp=Decimal("400"),
            paid_usd=Decimal("0"),
            fx_syp_per_usd=Decimal("15000"),
        )

    def test_partial_syp_only_equal_syp_leg(self):
        bill = self._create_bill(
            status="partial",
            items=self._mixed_items(),
            payment_method="syp_only",
            paid_syp=Decimal("1000"),
            paid_usd=Decimal("0"),
        )
        self._assert_partial_snapshot(
            bill=bill,
            paid_syp=Decimal("1000"),
            paid_usd=Decimal("0"),
            fx_syp_per_usd=Decimal("15000"),
        )

    def test_partial_syp_only_overflow_reduces_usd_without_shape_rewrite(self):
        bill = self._create_bill(
            status="partial",
            items=self._mixed_items(),
            payment_method="syp_only",
            paid_syp=Decimal("16000"),
            paid_usd=Decimal("0"),
        )
        self._assert_partial_snapshot(
            bill=bill,
            paid_syp=Decimal("16000"),
            paid_usd=Decimal("0"),
            fx_syp_per_usd=Decimal("15000"),
        )
        debt = self._debt_for_bill(bill)
        self.assertEqual(debt.remaining_syp, Decimal("0"))
        self.assertEqual(debt.remaining_usd, Decimal("1"))

    # ------------------------------------------------------------------
    # 4) PARTIAL: USD ONLY (<, =, overflow + rounding edge)
    # ------------------------------------------------------------------
    def test_partial_usd_only_less_than_usd_leg(self):
        bill = self._create_bill(
            status="partial",
            items=self._mixed_items(),
            payment_method="usd_only",
            paid_syp=Decimal("0"),
            paid_usd=Decimal("1"),
        )
        self._assert_partial_snapshot(
            bill=bill,
            paid_syp=Decimal("0"),
            paid_usd=Decimal("1"),
            fx_syp_per_usd=Decimal("15000"),
        )

    def test_partial_usd_only_equal_usd_leg(self):
        bill = self._create_bill(
            status="partial",
            items=self._mixed_items(),
            payment_method="usd_only",
            paid_syp=Decimal("0"),
            paid_usd=Decimal("2"),
        )
        self._assert_partial_snapshot(
            bill=bill,
            paid_syp=Decimal("0"),
            paid_usd=Decimal("2"),
            fx_syp_per_usd=Decimal("15000"),
        )

    def test_partial_usd_only_overflow_reduces_syp_without_shape_rewrite(self):
        bill = self._create_bill(
            status="partial",
            items=self._mixed_items(),
            payment_method="usd_only",
            paid_syp=Decimal("0"),
            paid_usd=Decimal("2.05"),
        )
        self._assert_partial_snapshot(
            bill=bill,
            paid_syp=Decimal("0"),
            paid_usd=Decimal("2.05"),
            fx_syp_per_usd=Decimal("15000"),
        )
        debt = self._debt_for_bill(bill)
        self.assertEqual(debt.remaining_syp, Decimal("250"))
        self.assertEqual(debt.remaining_usd, Decimal("0"))

    def test_partial_usd_only_overflow_rounding_edge(self):
        bill = self._create_bill(
            status="partial",
            items=self._mixed_items(),
            payment_method="usd_only",
            paid_syp=Decimal("0"),
            paid_usd=Decimal("2.03"),
        )
        self._assert_partial_snapshot(
            bill=bill,
            paid_syp=Decimal("0"),
            paid_usd=Decimal("2.03"),
            fx_syp_per_usd=Decimal("15000"),
        )
        debt = self._debt_for_bill(bill)
        self.assertEqual(debt.remaining_syp, Decimal("550"))
        self.assertEqual(debt.remaining_usd, Decimal("0"))

    # ------------------------------------------------------------------
    # 5) PARTIAL: MIXED MATRIX
    # ------------------------------------------------------------------
    def test_partial_mixed_both_less_than_totals(self):
        bill = self._create_bill(
            status="partial",
            items=self._mixed_items(),
            payment_method="mixed",
            paid_syp=Decimal("700"),
            paid_usd=Decimal("1.5"),
        )
        self._assert_partial_snapshot(
            bill=bill,
            paid_syp=Decimal("700"),
            paid_usd=Decimal("1.5"),
            fx_syp_per_usd=Decimal("15000"),
        )

    def test_partial_mixed_one_equal_total_other_less(self):
        bill = self._create_bill(
            status="partial",
            items=self._mixed_items(),
            payment_method="mixed",
            paid_syp=Decimal("1000"),
            paid_usd=Decimal("1.5"),
        )
        self._assert_partial_snapshot(
            bill=bill,
            paid_syp=Decimal("1000"),
            paid_usd=Decimal("1.5"),
            fx_syp_per_usd=Decimal("15000"),
        )

    def test_partial_mixed_one_overflows_other_less_syp_side(self):
        bill = self._create_bill(
            status="partial",
            items=self._mixed_items(),
            payment_method="mixed",
            paid_syp=Decimal("16000"),
            paid_usd=Decimal("0.5"),
        )
        self._assert_partial_snapshot(
            bill=bill,
            paid_syp=Decimal("16000"),
            paid_usd=Decimal("0.5"),
            fx_syp_per_usd=Decimal("15000"),
        )

    def test_partial_mixed_one_overflows_other_less_usd_side(self):
        bill = self._create_bill(
            status="partial",
            items=self._mixed_items(),
            payment_method="mixed",
            paid_syp=Decimal("100"),
            paid_usd=Decimal("2.02"),
        )
        self._assert_partial_snapshot(
            bill=bill,
            paid_syp=Decimal("100"),
            paid_usd=Decimal("2.02"),
            fx_syp_per_usd=Decimal("15000"),
        )

    def test_partial_mixed_combined_near_full_total(self):
        bill = self._create_bill(
            status="partial",
            items=self._mixed_items(),
            payment_method="mixed",
            paid_syp=Decimal("999"),
            paid_usd=Decimal("1.99"),
        )
        self._assert_partial_snapshot(
            bill=bill,
            paid_syp=Decimal("999"),
            paid_usd=Decimal("1.99"),
            fx_syp_per_usd=Decimal("15000"),
        )
        debt = self._debt_for_bill(bill)
        self.assertEqual(debt.remaining_syp, Decimal("1"))
        self.assertEqual(debt.remaining_usd, Decimal("0.01"))

    def test_partial_mixed_combined_well_below_full_total(self):
        bill = self._create_bill(
            status="partial",
            items=self._mixed_items(),
            payment_method="mixed",
            paid_syp=Decimal("200"),
            paid_usd=Decimal("0.2"),
        )
        self._assert_partial_snapshot(
            bill=bill,
            paid_syp=Decimal("200"),
            paid_usd=Decimal("0.2"),
            fx_syp_per_usd=Decimal("15000"),
        )

    # ------------------------------------------------------------------
    # 6) VALIDATION COVERAGE
    # ------------------------------------------------------------------
    def test_partial_rejects_equal_to_full_total(self):
        with self.assertRaises(ValidationError):
            self._create_bill(
                status="partial",
                items=self._mixed_items(),
                payment_method="syp_only",
                paid_syp=Decimal("31000"),
                paid_usd=Decimal("0"),
                settlement_currency="SYP",
            )

    def test_partial_rejects_overpayment(self):
        with self.assertRaises(ValidationError):
            self._create_bill(
                status="partial",
                items=self._mixed_items(),
                payment_method="syp_only",
                paid_syp=Decimal("31001"),
                paid_usd=Decimal("0"),
                settlement_currency="SYP",
            )

    def test_partial_rejects_separate_method(self):
        with self.assertRaises(ValidationError):
            self._create_bill(
                status="partial",
                items=self._mixed_items(),
                payment_method="separate",
                paid_syp=Decimal("1000"),
                paid_usd=Decimal("1"),
            )

    def test_partial_rejects_invalid_mixed_cases(self):
        invalid_cases = [
            {
                "name": "missing_syp_amount",
                "items": self._mixed_items(),
                "paid_syp": Decimal("0"),
                "paid_usd": Decimal("1"),
            },
            {
                "name": "missing_usd_amount",
                "items": self._mixed_items(),
                "paid_syp": Decimal("100"),
                "paid_usd": Decimal("0"),
            },
            {
                "name": "bill_not_multicurrency",
                "items": self._syp_items(total_syp="1000"),
                "paid_syp": Decimal("100"),
                "paid_usd": Decimal("0.1"),
            },
        ]
        for case in invalid_cases:
            with self.subTest(case=case["name"]):
                with self.assertRaises(ValidationError):
                    self._create_bill(
                        status="partial",
                        items=case["items"],
                        payment_method="mixed",
                        paid_syp=case["paid_syp"],
                        paid_usd=case["paid_usd"],
                    )

    # ------------------------------------------------------------------
    # 7) CONSISTENCY: CREATION PARTIAL VS LATER SETTLEMENT
    # ------------------------------------------------------------------
    def test_consistency_creation_partial_vs_later_payment_syp_overflow(self):
        bill_at_creation = self._create_bill(
            status="partial",
            items=self._mixed_items(),
            payment_method="syp_only",
            paid_syp=Decimal("16000"),
            paid_usd=Decimal("0"),
            fx_syp_per_usd=Decimal("15000"),
        )
        debt_creation = self._debt_for_bill(bill_at_creation)

        bill_unpaid = self._create_bill(
            status="unpaid",
            items=self._mixed_items(),
            payment_method="none",
            paid_syp=Decimal("0"),
            paid_usd=Decimal("0"),
            fx_syp_per_usd=Decimal("15000"),
        )
        BillingSV.pay_partial(
            actor=self.actor,
            bill_id=bill_unpaid.id,
            amount=Decimal("16000"),
            money_container_id=self.cash.id,
            currency_code="SYP",
        )
        debt_later = self._debt_for_bill(bill_unpaid)

        self.assertEqual(debt_later.remaining_syp, debt_creation.remaining_syp)
        self.assertEqual(debt_later.remaining_usd, debt_creation.remaining_usd)

    def test_consistency_creation_partial_vs_later_payment_usd_overflow(self):
        bill_at_creation = self._create_bill(
            status="partial",
            items=self._mixed_items(),
            payment_method="usd_only",
            paid_syp=Decimal("0"),
            paid_usd=Decimal("2.05"),
            fx_syp_per_usd=Decimal("15000"),
        )
        debt_creation = self._debt_for_bill(bill_at_creation)

        bill_unpaid = self._create_bill(
            status="unpaid",
            items=self._mixed_items(),
            payment_method="none",
            paid_syp=Decimal("0"),
            paid_usd=Decimal("0"),
            fx_syp_per_usd=Decimal("15000"),
        )
        BillingSV.pay_partial(
            actor=self.actor,
            bill_id=bill_unpaid.id,
            amount=Decimal("2.05"),
            money_container_id=self.cash.id,
            currency_code="USD",
        )
        debt_later = self._debt_for_bill(bill_unpaid)

        self.assertEqual(debt_later.remaining_syp, debt_creation.remaining_syp)
        self.assertEqual(debt_later.remaining_usd, debt_creation.remaining_usd)

    # ------------------------------------------------------------------
    # 8) FX + BOUNDARY / ROUNDING EDGES
    # ------------------------------------------------------------------
    def test_overflow_reduction_uses_creation_fx_snapshot(self):
        FinSV.set_current_fx(actor=self.actor, rate_syp_per_usd=Decimal("20000"))
        bill = self._create_bill(
            status="partial",
            items=self._mixed_items(),
            payment_method="syp_only",
            paid_syp=Decimal("13000"),
            paid_usd=Decimal("0"),
            fx_syp_per_usd=Decimal("12000"),
        )
        debt = self._debt_for_bill(bill)
        self.assertEqual(debt.remaining_syp, Decimal("0"))
        self.assertEqual(debt.remaining_usd, Decimal("1"))
        self._assert_receipts_match_paid_components(
            bill=bill,
            paid_syp=Decimal("13000"),
            paid_usd=Decimal("0"),
            fx_syp_per_usd=Decimal("12000"),
        )

    def test_small_value_scenario_preserves_non_negative_remaining(self):
        bill = self._create_bill(
            status="partial",
            items=self._mixed_items(total_syp="1", total_usd="0.03"),
            payment_method="usd_only",
            paid_syp=Decimal("0"),
            paid_usd=Decimal("0.02"),
            settlement_currency="USD",
            fx_syp_per_usd=Decimal("15000"),
        )
        self._assert_partial_snapshot(
            bill=bill,
            paid_syp=Decimal("0"),
            paid_usd=Decimal("0.02"),
            fx_syp_per_usd=Decimal("15000"),
        )
        debt = self._debt_for_bill(bill)
        self.assertEqual(debt.remaining_syp, Decimal("1"))
        self.assertEqual(debt.remaining_usd, Decimal("0.01"))
        self.assertGreaterEqual(debt.remaining_syp, Decimal("0"))
        self.assertGreaterEqual(debt.remaining_usd, Decimal("0"))
