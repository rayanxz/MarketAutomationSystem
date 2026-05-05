from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.test import TestCase

from billing.models import Provider
from catalog.models import Product, ProductCollection, ProductSet, UnitType
from debts import services as DebtSV
from debts.models import CreditorDebt, DebtorDebt, PartyType
from financials import services as FinSV
from financials.models import Currency
from inventory import services as InvSV
from pos import services_returns as ReturnSV
from pos.models import CustomerProfile, SalesBill, SalesBillRow
from stock.models import ProductContainer


class DebtSchemaIdentityIntegrityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.user = User.objects.create_user(username="schema_mgr", password="pw12345", is_staff=True)

        Currency.objects.get_or_create(
            code="SYP",
            defaults={"name": "Syrian Pound", "decimals": 0, "is_active": True},
        )
        Currency.objects.get_or_create(
            code="USD",
            defaults={"name": "US Dollar", "decimals": 2, "is_active": True},
        )
        FinSV.set_current_fx(actor=cls.user, rate_syp_per_usd=Decimal("10000"))

        cls.container, _ = ProductContainer.objects.get_or_create(
            code="schema_store",
            defaults={"name": "Schema Store", "is_store": False, "is_active": True},
        )

        col = ProductCollection.objects.create(name="Schema Collection")
        st = ProductSet.objects.create(collection=col, name="Schema Set")
        cls.product = Product.objects.create(
            name="Schema Product",
            set=st,
            unit_primary=UnitType.PIECE,
            allow_syp_sales=True,
            allow_usd_sales=True,
            allow_syp_purchasing=True,
            allow_usd_purchasing=True,
            default_sale_currency="USD",
            default_price_usd=Decimal("10"),
            default_cost_usd=Decimal("5"),
        )

        cls.provider = Provider.objects.create(name="Schema Provider")
        cls.customer = CustomerProfile.objects.create(name="Schema Customer", created_by=cls.user)

    def _create_finalized_sale_bill(self) -> tuple[SalesBill, SalesBillRow]:
        bill = SalesBill.objects.create(
            customer=self.customer,
            customer_name=self.customer.name,
            cashier=self.user,
            pay_status=SalesBill.PAY_FULL,
            total_amount=Decimal("20.00"),
            total_syp=Decimal("0.00"),
            total_usd=Decimal("20.00"),
            paid_amount=Decimal("20.00"),
            settlement_mode=SalesBill.SETTLE_ALL_USD,
            settlement_currency="USD",
            fx_rate_used=Decimal("10000"),
            parked=False,
            finalized=True,
        )

        row = SalesBillRow.objects.create(
            bill=bill,
            product_id=self.product.id,
            product_name=self.product.name,
            product_number=str(self.product.id),
            qty=Decimal("2.00"),
            uom_index=1,
            unit_price=Decimal("10.00"),
            sale_currency="USD",
            disc_amount=Decimal("0.00"),
            disc_pct=Decimal("0.00"),
        )
        return bill, row

    def test_pos_credit_return_allows_creditor_entry_without_provider(self):
        # Seed stock then consume via POS sale so return can reconstruct FIFO cost.
        InvSV.record_purchase_item(
            actor=self.user,
            product=self.product,
            unit_index=1,
            qty_primary=Decimal("10.00"),
            unit_cost=Decimal("5.00"),
            cost_currency="USD",
            source_app="tests",
            source_model="Seed",
            source_id="schema-seed",
            container=self.container,
        )

        bill, row = self._create_finalized_sale_bill()
        InvSV.record_sale_item(
            actor=self.user,
            product=self.product,
            unit_index=1,
            qty_primary=Decimal("2.00"),
            unit_cost=Decimal("5.00"),
            sale_unit_price_at_txn=Decimal("10.00"),
            sale_currency_at_txn="USD",
            source_app="pos",
            source_model="SalesBill",
            source_id=str(bill.id),
            container=self.container,
        )

        ret = ReturnSV.create_sales_return_draft(
            actor=self.user,
            sale_bill_id=bill.id,
            stock_container_id=self.container.id,
            items=[{"sale_row_id": row.id, "qty": Decimal("1.00"), "reason": "schema"}],
        )
        ReturnSV.post_sales_return(
            actor=self.user,
            return_id=ret.id,
            settle_mode="credit",
            money_container_id=None,
        )

        entry = CreditorDebt.objects.get(
            source_app="pos",
            source_model="SalesReturn",
            source_id=str(ret.id),
            currency_code="USD",
        )
        self.assertIsNone(entry.provider_id)
        self.assertEqual(entry.party_type, PartyType.CUSTOMER)
        self.assertEqual(entry.customer_id, bill.customer_id)
        self.assertGreater(entry.total, Decimal("0"))

    def test_provider_based_creditor_entry_still_valid(self):
        entry = DebtSV.create_creditor_entry(
            provider=self.provider,
            total=Decimal("100.00"),
            collected=Decimal("0.00"),
            source_app="billing",
            source_model="ProviderReturn",
            source_id="9001",
            currency_code="SYP",
            doc_serial=9001,
            party_type=PartyType.PROVIDER,
            party_name=self.provider.name,
        )
        self.assertEqual(entry.provider_id, self.provider.id)
        self.assertEqual(entry.currency_code, "SYP")
        self.assertEqual(entry.source_id, "9001")

    def test_cross_currency_entries_use_same_canonical_source_id(self):
        DebtSV.create_debtor_entry(
            provider=self.provider,
            total=Decimal("1000.00"),
            paid_amount=Decimal("0.00"),
            source_app="billing",
            source_model="Bill",
            source_id="777",
            currency_code="SYP",
            doc_serial=777,
        )
        DebtSV.create_debtor_entry(
            provider=self.provider,
            total=Decimal("10.00"),
            paid_amount=Decimal("0.00"),
            source_app="billing",
            source_model="Bill",
            source_id="777",
            currency_code="USD",
            doc_serial=777,
        )

        rows = DebtorDebt.objects.filter(
            source_app="billing",
            source_model="Bill",
            source_id="777",
        ).order_by("currency_code")
        self.assertEqual(rows.count(), 2)
        self.assertEqual({r.currency_code for r in rows}, {"SYP", "USD"})
        self.assertFalse(
            DebtorDebt.objects.filter(
                source_app="billing",
                source_model="Bill",
                source_id="777:USD",
            ).exists()
        )

    def test_legacy_source_id_suffix_is_normalized_to_canonical_identity(self):
        first = DebtSV.create_creditor_entry(
            provider=self.provider,
            total=Decimal("5.00"),
            collected=Decimal("0.00"),
            source_app="billing",
            source_model="ProviderReturn",
            source_id="555:USD",
            currency_code="SYP",
            doc_serial=555,
        )
        second = DebtSV.create_creditor_entry(
            provider=self.provider,
            total=Decimal("9.00"),
            collected=Decimal("0.00"),
            source_app="billing",
            source_model="ProviderReturn",
            source_id="555",
            currency_code="USD",
            doc_serial=555,
        )

        self.assertEqual(first.id, second.id)
        second.refresh_from_db()
        self.assertEqual(second.source_id, "555")
        self.assertEqual(second.currency_code, "USD")
        self.assertEqual(second.legacy_source_id, "555:USD")
        self.assertEqual(second.total, Decimal("9.00"))

    def test_migration_graph_has_no_conflicts_and_keeps_debts_anchor_dependency(self):
        loader = MigrationLoader(connection, ignore_no_migrations=True)
        self.assertEqual(loader.detect_conflicts(), {})
        self.assertIn(("billing", "0018_migration_lineage_anchor"), loader.graph.nodes)
        self.assertIn(("debts", "0005_sqlite_schema_integrity_and_identity"), loader.graph.nodes)

        # Guard the explicit cross-app ownership dependency introduced in Phase 1.
        forward_plan = loader.graph.forwards_plan(("debts", "0005_sqlite_schema_integrity_and_identity"))
        self.assertIn(("billing", "0018_migration_lineage_anchor"), forward_plan)
