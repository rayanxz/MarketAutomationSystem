from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import AccountProfile
from billing import services as BillingSV
from billing.models import Bill, BillItem, Provider, ProviderReturn
from catalog.models import Product, ProductCollection, ProductSet, UnitType
from debts.models import (
    CreditorDebt,
    CreditorReceipt,
    DebtRecord,
    DebtSettlement,
    DebtDirection,
    DebtCauseType,
)
from financials import services as FinSV
from financials.models import Currency, MoneyContainer, MoneyContainerCurrency, PostingTargetType, Receipt, ReceiptStatus
from inventory.models import DEC0, ProductMovement, q3
from stock.models import ProductContainer, StockEntry, StockFifoLayer


class PostStabilizationSystemPathTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.actor = User.objects.create_user(username="sys_path_mgr", password="pw12345")
        AccountProfile.objects.create(user=cls.actor, role=AccountProfile.Role.MANAGER)

        cls.syp, _ = Currency.objects.get_or_create(
            code="SYP",
            defaults={"name": "Syrian Pound", "decimals": 0, "is_active": True},
        )
        cls.usd, _ = Currency.objects.get_or_create(
            code="USD",
            defaults={"name": "US Dollar", "decimals": 2, "is_active": True},
        )
        FinSV.set_current_fx(actor=cls.actor, rate_syp_per_usd=Decimal("15000"))

        cls.store, _ = ProductContainer.objects.get_or_create(
            code="store",
            defaults={"name": "Store", "is_store": True, "is_active": True},
        )

        cls.cash = MoneyContainer.objects.create(
            name="System Path Billing Cash",
            container_type=MoneyContainer.ContainerType.DRAWER,
            is_active=True,
            created_by=cls.actor,
        )
        cls.cash.allowed_users.add(cls.actor)
        MoneyContainerCurrency.objects.update_or_create(
            container=cls.cash,
            currency=cls.syp,
            defaults={"is_enabled": True},
        )
        MoneyContainerCurrency.objects.update_or_create(
            container=cls.cash,
            currency=cls.usd,
            defaults={"is_enabled": True},
        )

        cls.provider = Provider.objects.create(name="System Path Provider")
        col = ProductCollection.objects.create(name="System Path Collection")
        cls.prod_set = ProductSet.objects.create(collection=col, name="System Path Set")

    def _product(self, *, name: str) -> Product:
        return Product.objects.create(
            name=name,
            set=self.prod_set,
            unit_primary=UnitType.PIECE,
            allow_syp_purchasing=True,
            allow_usd_purchasing=True,
            allow_syp_sales=True,
            allow_usd_sales=True,
            default_cost_syp=Decimal("1000"),
            default_cost_usd=Decimal("5"),
            default_price_syp=Decimal("1200"),
            default_price_usd=Decimal("6"),
        )

    def _create_purchase_bill(
        self,
        *,
        product: Product,
        qty: str,
        cost: str,
        currency: str,
        status: str = "unpaid",
        paid_amount: Decimal = Decimal("0"),
    ) -> Bill:
        return BillingSV.create_bill(
            actor=self.actor,
            provider_id=self.provider.id,
            status=status,
            paid_amount=paid_amount,
            items=[
                {
                    "product_id": product.id,
                    "unit_index": 1,
                    "qty_raw": qty,
                    "cost": cost,
                    "currency": currency,
                }
            ],
            container=self.store,
            money_container_id=self.cash.id,
            settlement_currency=currency,
        )

    def _entry_for_bill(self, bill: Bill) -> DebtRecord:
        return DebtRecord.objects.get(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id=bill.public_id,
        )

    def _entries_for_return(self, ret: ProviderReturn) -> list[CreditorDebt]:
        return list(
            CreditorDebt.objects.filter(
                source_app="billing",
                source_model="ProviderReturn",
                source_id=str(ret.id),
            ).order_by("currency_code")
        )

    def test_purchase_partial_pay_then_delete_is_numerically_symmetric(self):
        product = self._product(name="Purchase Lifecycle Product")
        self.cash.balance_syp = Decimal("10000")
        self.cash.balance_usd = Decimal("50")
        self.cash.save(update_fields=["balance_syp", "balance_usd"])

        base_bal = FinSV.container_balance(container_id=self.cash.id)
        self.assertEqual(base_bal.get("SYP", DEC0), DEC0)

        bill = self._create_purchase_bill(
            product=product,
            qty="1",
            cost="1000",
            currency="SYP",
            status="partial",
            paid_amount=Decimal("300"),
        )
        item = BillItem.objects.get(bill=bill)

        entry = self._entry_for_bill(bill)
        self.assertEqual(entry.total_syp, Decimal("700"))
        self.assertEqual(entry.remaining_syp, Decimal("700"))
        self.assertEqual(entry.remaining_usd, DEC0)

        mid_bal = FinSV.container_balance(container_id=self.cash.id)
        self.assertEqual(mid_bal.get("SYP", DEC0), Decimal("-300"))

        BillingSV.pay_partial(
            actor=self.actor,
            bill_id=bill.id,
            amount=Decimal("700"),
            money_container_id=self.cash.id,
            currency_code="SYP",
        )
        entry.refresh_from_db()
        self.assertEqual(entry.remaining_syp, DEC0)
        self.assertEqual(entry.status, "closed")

        before_delete_receipt_ids = list(
            Receipt.objects.filter(source_app="billing", source_model="Bill", source_id=str(bill.id)).values_list("id", flat=True)
        )
        later_payment_receipt_ids = list(
            DebtSettlement.objects.filter(debt=entry, receipt__isnull=False).values_list("receipt_id", flat=True)
        )

        BillingSV.delete_bill(actor=self.actor, bill_id=bill.id)

        after_bal = FinSV.container_balance(container_id=self.cash.id)
        self.assertEqual(after_bal.get("SYP", DEC0), base_bal.get("SYP", DEC0))

        self.assertFalse(Bill.objects.filter(id=bill.id).exists())
        self.assertFalse(
            DebtRecord.objects.filter(
                direction=DebtDirection.PAYABLE,
                cause_type=DebtCauseType.PURCHASE_BILL,
                cause_id=bill.public_id,
            ).exists()
        )

        self.assertEqual(
            StockFifoLayer.objects.filter(
                source_app="billing",
                source_model="BillItem",
                source_id=str(item.id),
            ).count(),
            0,
        )
        self.assertEqual(
            ProductMovement.objects.filter(
                source_app="billing",
                source_model="BillItem",
                source_id=str(item.id),
            ).count(),
            0,
        )

        all_expected_reversed = set(before_delete_receipt_ids) | set(later_payment_receipt_ids)
        self.assertGreater(len(all_expected_reversed), 0)
        self.assertEqual(
            Receipt.objects.filter(id__in=all_expected_reversed, status=ReceiptStatus.REVERSED).count(),
            len(all_expected_reversed),
        )

        stock_entry = StockEntry.objects.get(product=product, container=self.store)
        self.assertEqual(q3(stock_entry.qty_primary), DEC0)

    def test_provider_return_paid_then_delete_is_numerically_symmetric_for_same_currency(self):
        product = self._product(name="Provider Return Paid Product")
        bill = self._create_purchase_bill(
            product=product,
            qty="1",
            cost="1000",
            currency="SYP",
            status="unpaid",
            paid_amount=Decimal("0"),
        )
        bill_item = bill.items.get()

        base_bal = FinSV.container_balance(container_id=self.cash.id)

        ret = BillingSV.create_return(
            actor=self.actor,
            provider_id=self.provider.id,
            status="paid",
            paid_amount=Decimal("1000"),
            items=[
                {
                    "bill_item_id": bill_item.id,
                    "product_id": product.id,
                    "unit_index": 1,
                    "qty_primary": "1",
                    "container_splits": [{"code": "store", "qty_primary": "1"}],
                }
            ],
            container=None,
            source_bill_serial=bill.serial,
            money_container_id=self.cash.id,
            currency_code="SYP",
            valuation_mode="HISTORICAL",
        )

        mid_bal = FinSV.container_balance(container_id=self.cash.id)
        self.assertEqual(mid_bal.get("SYP", DEC0), base_bal.get("SYP", DEC0) + Decimal("1000"))

        BillingSV.delete_return(actor=self.actor, return_id=ret.id)

        after_bal = FinSV.container_balance(container_id=self.cash.id)
        self.assertEqual(after_bal.get("SYP", DEC0), base_bal.get("SYP", DEC0))
        self.assertFalse(ProviderReturn.objects.filter(id=ret.id).exists())
        self.assertEqual(
            CreditorDebt.objects.filter(
                source_app="billing",
                source_model="ProviderReturn",
                source_id=str(ret.id),
            ).count(),
            0,
        )

    def test_delete_return_should_reverse_later_collection_receipts_too(self):
        product = self._product(name="Return Delete Later Collection")
        bill = self._create_purchase_bill(
            product=product,
            qty="2",
            cost="1000",
            currency="SYP",
            status="unpaid",
            paid_amount=Decimal("0"),
        )
        bill_item = bill.items.get()
        base_bal = FinSV.container_balance(container_id=self.cash.id)

        ret = BillingSV.create_return(
            actor=self.actor,
            provider_id=self.provider.id,
            status="unpaid",
            paid_amount=Decimal("0"),
            items=[
                {
                    "bill_item_id": bill_item.id,
                    "product_id": product.id,
                    "unit_index": 1,
                    "qty_primary": "1",
                    "container_splits": [{"code": "store", "qty_primary": "1"}],
                }
            ],
            container=None,
            source_bill_serial=bill.serial,
            money_container_id=None,
            currency_code="SYP",
            valuation_mode="HISTORICAL",
        )

        BillingSV.collect_partial(
            actor=self.actor,
            return_id=ret.id,
            amount=Decimal("500"),
            money_container_id=self.cash.id,
            currency_code="SYP",
        )
        mid_bal = FinSV.container_balance(container_id=self.cash.id)
        self.assertEqual(mid_bal.get("SYP", DEC0), base_bal.get("SYP", DEC0) + Decimal("500"))

        billing_receipt_ids = list(
            Receipt.objects.filter(
                source_app="billing",
                source_model="ProviderReturn",
                source_id=str(ret.id),
                status=ReceiptStatus.POSTED,
            ).values_list("id", flat=True)
        )

        entries = self._entries_for_return(ret)
        debts_receipt_ids = list(
            CreditorReceipt.objects.filter(entry__in=entries, receipt__isnull=False).values_list("receipt_id", flat=True)
        )
        self.assertGreater(len(debts_receipt_ids), 0)

        BillingSV.delete_return(actor=self.actor, return_id=ret.id)

        after_bal = FinSV.container_balance(container_id=self.cash.id)
        self.assertEqual(after_bal.get("SYP", DEC0), base_bal.get("SYP", DEC0))
        self.assertFalse(ProviderReturn.objects.filter(id=ret.id).exists())
        self.assertFalse(
            CreditorDebt.objects.filter(
                source_app="billing",
                source_model="ProviderReturn",
                source_id=str(ret.id),
            ).exists()
        )
        self.assertEqual(
            Receipt.objects.filter(id__in=debts_receipt_ids, status=ReceiptStatus.REVERSED).count(),
            len(debts_receipt_ids),
        )
        all_related_receipt_ids = set(billing_receipt_ids) | set(debts_receipt_ids)
        self.assertEqual(
            Receipt.objects.filter(id__in=all_related_receipt_ids, status=ReceiptStatus.REVERSED).count(),
            len(all_related_receipt_ids),
        )

    def test_mixed_currency_paid_return_should_use_single_canonical_receipt(self):
        p_syp = self._product(name="Return Mixed SYP")
        p_usd = self._product(name="Return Mixed USD")

        bill = BillingSV.create_bill(
            actor=self.actor,
            provider_id=self.provider.id,
            status="unpaid",
            paid_amount=Decimal("0"),
            items=[
                {"product_id": p_syp.id, "unit_index": 1, "qty_raw": "1", "cost": "5000", "currency": "SYP"},
                {"product_id": p_usd.id, "unit_index": 1, "qty_raw": "2", "cost": "3", "currency": "USD"},
            ],
            container=self.store,
            money_container_id=self.cash.id,
            settlement_currency="SYP",
            fx_usd_syp=Decimal("15000"),
        )
        items = list(bill.items.order_by("id"))
        base_bal = FinSV.container_balance(container_id=self.cash.id)

        ret = BillingSV.create_return(
            actor=self.actor,
            provider_id=self.provider.id,
            status="paid",
            paid_amount=Decimal("0"),
            items=[
                {
                    "bill_item_id": items[0].id,
                    "product_id": items[0].product_id,
                    "unit_index": 1,
                    "qty_primary": "1",
                    "container_splits": [{"code": "store", "qty_primary": "1"}],
                },
                {
                    "bill_item_id": items[1].id,
                    "product_id": items[1].product_id,
                    "unit_index": 1,
                    "qty_primary": "2",
                    "container_splits": [{"code": "store", "qty_primary": "2"}],
                },
            ],
            container=None,
            source_bill_serial=bill.serial,
            money_container_id=self.cash.id,
            currency_code="SYP",
            valuation_mode="HISTORICAL",
        )

        entries = self._entries_for_return(ret)
        rows = list(
            CreditorReceipt.objects.filter(entry__in=entries, receipt__isnull=False).select_related("receipt")
        )
        self.assertEqual(len(rows), 2)
        by_cur = {row.currency_code: row for row in rows}
        self.assertEqual(q3(by_cur["SYP"].amount), q3(ret.total_syp))
        self.assertEqual(q3(by_cur["USD"].amount), q3(ret.total_usd))

        receipt_ids = {row.receipt_id for row in rows}
        self.assertEqual(len(receipt_ids), 1)
        receipt = rows[0].receipt

        container_totals: dict[str, Decimal] = {}
        counterparty_totals: dict[str, Decimal] = {}
        for ln in receipt.lines.select_related("currency").all():
            if ln.target_type == PostingTargetType.CONTAINER:
                container_totals[ln.currency.code] = q3(container_totals.get(ln.currency.code, DEC0) + ln.amount)
            else:
                counterparty_totals[ln.currency.code] = q3(counterparty_totals.get(ln.currency.code, DEC0) + ln.amount)

        # Return settled in SYP: one cash-in line in settlement currency only.
        self.assertEqual(q3(container_totals.get("SYP", DEC0)), q3(ret.total))
        self.assertEqual(q3(container_totals.get("USD", DEC0)), DEC0)

        # Fully-settled return keeps counterparty net at zero per currency.
        self.assertEqual(q3(counterparty_totals.get("SYP", DEC0)), DEC0)
        self.assertEqual(q3(counterparty_totals.get("USD", DEC0)), DEC0)

        after_bal = FinSV.container_balance(container_id=self.cash.id)
        self.assertEqual(
            q3(after_bal.get("SYP", DEC0) - base_bal.get("SYP", DEC0)),
            q3(ret.total),
        )
        self.assertEqual(q3(after_bal.get("USD", DEC0) - base_bal.get("USD", DEC0)), DEC0)
