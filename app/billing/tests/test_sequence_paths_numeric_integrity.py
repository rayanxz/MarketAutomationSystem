from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import AccountProfile
from billing import services as BillingSV
from billing.models import Bill, BillItem, Provider
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
from financials.models import Currency, MoneyContainer, MoneyContainerCurrency, Receipt, ReceiptStatus
from inventory.models import DEC0, q3
from stock.models import ProductContainer


class BillingSequencePathNumericIntegrityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.actor = User.objects.create_user(username="billing_seq_mgr", password="pw12345")
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
            name="Billing Sequence Cash",
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

        cls.provider = Provider.objects.create(name="Billing Sequence Provider")
        col = ProductCollection.objects.create(name="Billing Sequence Collection")
        cls.prod_set = ProductSet.objects.create(collection=col, name="Billing Sequence Set")

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

    def _bill_entry(self, bill: Bill) -> DebtRecord:
        return DebtRecord.objects.get(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id=bill.public_id,
        )

    def test_purchase_two_partials_then_delete_restores_numeric_truth(self):
        product = self._product(name="Purchase Seq Partial Product")
        base_bal = FinSV.container_balance(container_id=self.cash.id)

        bill = BillingSV.create_bill(
            actor=self.actor,
            provider_id=self.provider.id,
            status="unpaid",
            paid_amount=Decimal("0"),
            items=[
                {
                    "product_id": product.id,
                    "unit_index": 1,
                    "qty_raw": "1",
                    "cost": "1000",
                    "currency": "SYP",
                }
            ],
            container=self.store,
            money_container_id=self.cash.id,
            settlement_currency="SYP",
        )

        entry = self._bill_entry(bill)
        self.assertEqual(entry.total_syp, Decimal("1000"))
        self.assertEqual(entry.total_usd, DEC0)
        self.assertEqual(entry.remaining_syp, Decimal("1000"))
        self.assertEqual(entry.remaining_usd, DEC0)

        BillingSV.pay_partial(
            actor=self.actor,
            bill_id=bill.id,
            amount=Decimal("300"),
            money_container_id=self.cash.id,
            currency_code="SYP",
        )
        BillingSV.pay_full(
            actor=self.actor,
            bill_id=bill.id,
            money_container_id=self.cash.id,
            currency_code="SYP",
        )

        entry.refresh_from_db()
        self.assertEqual(entry.remaining_syp, DEC0)
        self.assertEqual(entry.remaining_usd, DEC0)
        self.assertEqual(entry.status, "closed")

        payment_rows = list(
            DebtSettlement.objects.filter(debt=entry, receipt__isnull=False).order_by("id")
        )
        self.assertEqual(len(payment_rows), 2)
        self.assertEqual(q3(sum((p.payment_syp for p in payment_rows), Decimal("0"))), Decimal("1000.00"))

        payment_receipt_ids = [p.receipt_id for p in payment_rows]
        bill_receipt_ids = list(
            Receipt.objects.filter(
                source_app="billing",
                source_model="Bill",
                source_id=str(bill.id),
            ).values_list("id", flat=True)
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

        all_related_receipt_ids = set(payment_receipt_ids) | set(bill_receipt_ids)
        self.assertGreater(len(all_related_receipt_ids), 0)
        self.assertEqual(
            Receipt.objects.filter(id__in=all_related_receipt_ids, status=ReceiptStatus.REVERSED).count(),
            len(all_related_receipt_ids),
        )

    def test_mixed_return_collect_sequence_then_delete_return_then_delete_bill(self):
        p_syp = self._product(name="Seq Return SYP Product")
        p_usd = self._product(name="Seq Return USD Product")
        base_bal = FinSV.container_balance(container_id=self.cash.id)

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
        bill_items = list(BillItem.objects.filter(bill=bill).order_by("id"))

        ret = BillingSV.create_return(
            actor=self.actor,
            provider_id=self.provider.id,
            status="unpaid",
            paid_amount=Decimal("0"),
            items=[
                {
                    "bill_item_id": bill_items[0].id,
                    "product_id": bill_items[0].product_id,
                    "unit_index": 1,
                    "qty_primary": "1",
                    "container_splits": [{"code": "store", "qty_primary": "1"}],
                },
                {
                    "bill_item_id": bill_items[1].id,
                    "product_id": bill_items[1].product_id,
                    "unit_index": 1,
                    "qty_primary": "2",
                    "container_splits": [{"code": "store", "qty_primary": "2"}],
                },
            ],
            container=None,
            source_bill_serial=bill.serial,
            money_container_id=None,
            currency_code="SYP",
            valuation_mode="HISTORICAL",
        )

        entry_syp = CreditorDebt.objects.get(
            source_app="billing",
            source_model="ProviderReturn",
            source_id=str(ret.id),
            currency_code="SYP",
        )
        entry_usd = CreditorDebt.objects.get(
            source_app="billing",
            source_model="ProviderReturn",
            source_id=str(ret.id),
            currency_code="USD",
        )
        self.assertEqual(q3(entry_syp.total), Decimal("5000.00"))
        self.assertEqual(q3(entry_usd.total), Decimal("6.00"))

        BillingSV.collect_partial(
            actor=self.actor,
            return_id=ret.id,
            amount=Decimal("2000"),
            money_container_id=self.cash.id,
            currency_code="SYP",
        )
        entry_syp.refresh_from_db()
        self.assertEqual(q3(entry_syp.collected), Decimal("2000.00"))
        self.assertEqual(q3(entry_syp.remaining), Decimal("3000.00"))

        BillingSV.collect_full(
            actor=self.actor,
            return_id=ret.id,
            money_container_id=self.cash.id,
            currency_code="USD",
        )
        entry_usd.refresh_from_db()
        self.assertEqual(q3(entry_usd.collected), Decimal("6.00"))
        self.assertEqual(q3(entry_usd.remaining), Decimal("0.00"))

        BillingSV.collect_full(
            actor=self.actor,
            return_id=ret.id,
            money_container_id=self.cash.id,
            currency_code="SYP",
        )
        entry_syp.refresh_from_db()
        self.assertEqual(q3(entry_syp.collected), Decimal("5000.00"))
        self.assertEqual(q3(entry_syp.remaining), Decimal("0.00"))

        bal_after_collect = FinSV.container_balance(container_id=self.cash.id)
        self.assertEqual(
            q3(bal_after_collect.get("SYP", DEC0) - base_bal.get("SYP", DEC0)),
            Decimal("5000.00"),
        )
        self.assertEqual(
            q3(bal_after_collect.get("USD", DEC0) - base_bal.get("USD", DEC0)),
            Decimal("6.00"),
        )

        entries = [entry_syp, entry_usd]
        debts_receipt_ids = set(
            CreditorReceipt.objects.filter(entry__in=entries, receipt__isnull=False).values_list("receipt_id", flat=True)
        )
        billing_receipt_ids = set(
            Receipt.objects.filter(
                source_app="billing",
                source_model="ProviderReturn",
                source_id=str(ret.id),
            ).values_list("id", flat=True)
        )

        BillingSV.delete_return(actor=self.actor, return_id=ret.id)

        bal_after_return_delete = FinSV.container_balance(container_id=self.cash.id)
        self.assertEqual(bal_after_return_delete.get("SYP", DEC0), base_bal.get("SYP", DEC0))
        self.assertEqual(bal_after_return_delete.get("USD", DEC0), base_bal.get("USD", DEC0))
        self.assertFalse(
            CreditorDebt.objects.filter(
                source_app="billing",
                source_model="ProviderReturn",
                source_id=str(ret.id),
            ).exists()
        )

        all_return_receipts = debts_receipt_ids | billing_receipt_ids
        self.assertGreater(len(all_return_receipts), 0)
        self.assertEqual(
            Receipt.objects.filter(id__in=all_return_receipts, status=ReceiptStatus.REVERSED).count(),
            len(all_return_receipts),
        )

        BillingSV.delete_bill(actor=self.actor, bill_id=bill.id)
        self.assertFalse(Bill.objects.filter(id=bill.id).exists())
        self.assertFalse(
            DebtRecord.objects.filter(
                direction=DebtDirection.PAYABLE,
                cause_type=DebtCauseType.PURCHASE_BILL,
                cause_id=bill.public_id,
            ).exists()
        )
