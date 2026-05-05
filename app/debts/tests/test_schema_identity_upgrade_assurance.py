from __future__ import annotations

import os
import sqlite3
import tempfile
from decimal import Decimal
from importlib import import_module

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from billing import services as BillingSV
from billing.models import Provider
from catalog.models import Product, ProductCollection, ProductSet, UnitType
from debts import services as DebtSV
from debts.models import CreditorDebt, DebtorDebt, PartyType
from financials import services as FinSV
from financials.models import Currency, MoneyContainer, MoneyContainerCurrency
from stock.models import ProductContainer


class _CompatSQLiteCursor:
    """Minimal Django-cursor compatibility for migration helper SQL (%s placeholders)."""

    def __init__(self, inner):
        self._inner = inner

    def execute(self, sql, params=None):
        sql = (sql or "").replace("%s", "?")
        self._inner.execute(sql, params or [])
        return self

    def fetchone(self):
        return self._inner.fetchone()

    def fetchall(self):
        return self._inner.fetchall()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self._inner.close()
        return False


class _CompatSQLiteConnection:
    vendor = "sqlite"

    def __init__(self, inner):
        self._inner = inner

    def cursor(self):
        return _CompatSQLiteCursor(self._inner.cursor())


class _CompatSchemaEditor:
    def __init__(self, inner_conn):
        self.connection = _CompatSQLiteConnection(inner_conn)

    def execute(self, sql, params=None):
        with self.connection.cursor() as cursor:
            cursor.execute(sql, params or [])


class DebtSchemaSQLiteMigrationAssuranceTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._mig_mod = import_module("debts.migrations.0005_sqlite_schema_integrity_and_identity")

    def _temp_conn(self):
        fd, path = tempfile.mkstemp(prefix="debts-upgrade-", suffix=".sqlite3")
        os.close(fd)
        conn = sqlite3.connect(path)
        return conn, path

    def _run_upgrade(self, conn) -> None:
        editor = _CompatSchemaEditor(conn)
        self._mig_mod._upgrade_schema_and_identity(None, editor)
        conn.commit()

    def _bootstrap_base_tables(self, conn) -> None:
        cur = conn.cursor()
        cur.execute('CREATE TABLE "billing_provider" ("id" integer PRIMARY KEY, "name" varchar(128) NOT NULL)')
        cur.execute('CREATE TABLE "pos_customerprofile" ("id" integer PRIMARY KEY, "name" varchar(128) NOT NULL)')
        cur.execute('INSERT INTO "billing_provider" ("id", "name") VALUES (1, "Provider One")')
        conn.commit()

    def _bootstrap_legacy_debt_tables(self, conn) -> None:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE "billing_debtorentry" (
                id integer PRIMARY KEY AUTOINCREMENT,
                source_app varchar(64) NOT NULL,
                source_model varchar(64) NOT NULL,
                source_id varchar(64) NOT NULL,
                created_at datetime NOT NULL,
                total numeric(14,3) NOT NULL DEFAULT '0.000',
                paid_amount numeric(14,3) NOT NULL DEFAULT '0.000',
                status varchar(8) NOT NULL DEFAULT 'open',
                party_type varchar(16) NOT NULL DEFAULT 'provider',
                party_name varchar(128) NOT NULL DEFAULT '',
                doc_serial integer NULL,
                due_date date NULL,
                provider_id bigint NOT NULL,
                currency_code varchar(3) NULL,
                legacy_source_id varchar(64) NULL,
                customer_id bigint NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE "billing_creditorentry" (
                id integer PRIMARY KEY AUTOINCREMENT,
                source_app varchar(64) NOT NULL,
                source_model varchar(64) NOT NULL,
                source_id varchar(64) NOT NULL,
                created_at datetime NOT NULL,
                total numeric(14,3) NOT NULL DEFAULT '0.000',
                collected numeric(14,3) NOT NULL DEFAULT '0.000',
                status varchar(8) NOT NULL DEFAULT 'open',
                provider_id bigint NOT NULL,
                doc_serial integer NULL,
                due_date date NULL,
                party_name varchar(128) NOT NULL DEFAULT '',
                party_type varchar(16) NOT NULL DEFAULT 'provider',
                currency_code varchar(3) NULL,
                legacy_source_id varchar(64) NULL,
                customer_id bigint NULL
            )
            """
        )
        conn.commit()

    def test_realistic_legacy_rows_upgrade_preserves_data(self):
        conn, path = self._temp_conn()
        try:
            self._bootstrap_base_tables(conn)
            self._bootstrap_legacy_debt_tables(conn)
            cur = conn.cursor()

            cur.execute(
                """
                CREATE TABLE "billing_debtorpayment" (
                    id integer PRIMARY KEY AUTOINCREMENT,
                    entry_id bigint NOT NULL,
                    amount numeric(14,3) NOT NULL DEFAULT '0.000',
                    FOREIGN KEY(entry_id) REFERENCES "billing_debtorentry"(id) DEFERRABLE INITIALLY DEFERRED
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE "billing_creditorreceipt" (
                    id integer PRIMARY KEY AUTOINCREMENT,
                    entry_id bigint NOT NULL,
                    amount numeric(14,3) NOT NULL DEFAULT '0.000',
                    FOREIGN KEY(entry_id) REFERENCES "billing_creditorentry"(id) DEFERRABLE INITIALLY DEFERRED
                )
                """
            )

            cur.execute(
                """
                INSERT INTO "billing_debtorentry"
                (source_app, source_model, source_id, created_at, total, paid_amount, status, party_type, party_name,
                 doc_serial, provider_id, currency_code, legacy_source_id, customer_id)
                VALUES ('billing', 'Bill', '501:USD', '2026-01-01', 11.000, 2.000, 'open', 'provider', 'P1',
                        501, 1, 'syp', NULL, NULL)
                """
            )
            debtor_id = cur.lastrowid

            cur.execute(
                """
                INSERT INTO "billing_creditorentry"
                (source_app, source_model, source_id, created_at, total, collected, status, provider_id,
                 doc_serial, party_name, party_type, currency_code, legacy_source_id, customer_id)
                VALUES ('billing', 'ProviderReturn', '601:USD', '2026-01-01', 13.000, 3.000, 'open', 1,
                        601, 'P1', 'provider', 'syp', NULL, NULL)
                """
            )
            creditor_id = cur.lastrowid

            cur.execute('INSERT INTO "billing_debtorpayment" (entry_id, amount) VALUES (?, ?)', [debtor_id, 1.000])
            cur.execute('INSERT INTO "billing_creditorreceipt" (entry_id, amount) VALUES (?, ?)', [creditor_id, 1.000])
            conn.commit()

            self._run_upgrade(conn)
            cur = conn.cursor()

            cur.execute(
                'SELECT source_id, currency_code, legacy_source_id FROM "billing_debtorentry" WHERE id=?',
                [debtor_id],
            )
            d = cur.fetchone()
            self.assertEqual(d[0], "501")
            self.assertEqual(d[1], "USD")
            self.assertEqual(d[2], "501:USD")

            cur.execute(
                'SELECT source_id, currency_code, legacy_source_id FROM "billing_creditorentry" WHERE id=?',
                [creditor_id],
            )
            c = cur.fetchone()
            self.assertEqual(c[0], "601")
            self.assertEqual(c[1], "USD")
            self.assertEqual(c[2], "601:USD")

            cur.execute('SELECT entry_id FROM "billing_debtorpayment"')
            self.assertEqual(cur.fetchone()[0], debtor_id)
            cur.execute('SELECT entry_id FROM "billing_creditorreceipt"')
            self.assertEqual(cur.fetchone()[0], creditor_id)
        finally:
            conn.close()
            os.unlink(path)

    def test_collision_case_survives_upgrade_without_merge(self):
        conn, path = self._temp_conn()
        try:
            self._bootstrap_base_tables(conn)
            self._bootstrap_legacy_debt_tables(conn)
            cur = conn.cursor()

            cur.execute(
                """
                INSERT INTO "billing_debtorentry"
                (source_app, source_model, source_id, created_at, total, paid_amount, status, party_type, party_name,
                 doc_serial, provider_id, currency_code, legacy_source_id, customer_id)
                VALUES ('billing', 'Bill', '700', '2026-01-01', 20.000, 0.000, 'open', 'provider', 'P1',
                        700, 1, 'USD', '', NULL)
                """
            )
            cur.execute(
                """
                INSERT INTO "billing_debtorentry"
                (source_app, source_model, source_id, created_at, total, paid_amount, status, party_type, party_name,
                 doc_serial, provider_id, currency_code, legacy_source_id, customer_id)
                VALUES ('billing', 'Bill', '700:USD', '2026-01-01', 9.000, 0.000, 'open', 'provider', 'P1',
                        700, 1, 'SYP', '', NULL)
                """
            )
            conn.commit()

            self._run_upgrade(conn)
            cur = conn.cursor()
            cur.execute(
                """
                SELECT source_id, currency_code, legacy_source_id
                FROM "billing_debtorentry"
                WHERE source_app='billing' AND source_model='Bill'
                ORDER BY source_id
                """
            )
            rows = cur.fetchall()
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0][0], "700")
            self.assertEqual(rows[0][1], "USD")
            self.assertEqual(rows[1][0], "700:USD")
            self.assertEqual(rows[1][1], "USD")
            self.assertEqual(rows[1][2], "700:USD")
        finally:
            conn.close()
            os.unlink(path)

    def test_fk_target_repair_after_sqlite_rebuild(self):
        conn, path = self._temp_conn()
        try:
            self._bootstrap_base_tables(conn)
            self._bootstrap_legacy_debt_tables(conn)
            cur = conn.cursor()

            cur.execute(
                """
                CREATE TABLE "billing_debtorpayment" (
                    id integer PRIMARY KEY AUTOINCREMENT,
                    entry_id bigint NOT NULL,
                    amount numeric(14,3) NOT NULL DEFAULT '0.000',
                    FOREIGN KEY(entry_id) REFERENCES "billing_debtorentry_old_0005"(id) DEFERRABLE INITIALLY DEFERRED
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE "billing_creditorreceipt" (
                    id integer PRIMARY KEY AUTOINCREMENT,
                    entry_id bigint NOT NULL,
                    amount numeric(14,3) NOT NULL DEFAULT '0.000',
                    FOREIGN KEY(entry_id) REFERENCES "billing_creditorentry_old_0005"(id) DEFERRABLE INITIALLY DEFERRED
                )
                """
            )
            conn.commit()

            self._run_upgrade(conn)
            cur = conn.cursor()
            cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='billing_debtorpayment'")
            debtorpayment_sql = (cur.fetchone() or [""])[0]
            cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='billing_creditorreceipt'")
            creditorreceipt_sql = (cur.fetchone() or [""])[0]

            self.assertIn('REFERENCES "billing_debtorentry"', debtorpayment_sql)
            self.assertIn('REFERENCES "billing_creditorentry"', creditorreceipt_sql)
            self.assertNotIn("billing_debtorentry_old_0005", debtorpayment_sql)
            self.assertNotIn("billing_creditorentry_old_0005", creditorreceipt_sql)
        finally:
            conn.close()
            os.unlink(path)


class DebtSchemaIdentityRuntimeAssuranceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.actor = User.objects.create_user(username="mig_schema", password="pw12345", is_staff=True)

        Currency.objects.get_or_create(
            code="SYP",
            defaults={"name": "Syrian Pound", "decimals": 0, "is_active": True},
        )
        Currency.objects.get_or_create(
            code="USD",
            defaults={"name": "US Dollar", "decimals": 2, "is_active": True},
        )
        FinSV.set_current_fx(actor=cls.actor, rate_syp_per_usd=Decimal("10000"))

    def test_collision_rows_keep_canonical_preference_deterministic(self):
        provider = Provider.objects.create(name="Collision Provider")
        canonical = DebtorDebt.objects.create(
            provider=provider,
            source_app="billing",
            source_model="Bill",
            source_id="700",
            total=Decimal("20.00"),
            paid_amount=Decimal("0.00"),
            status=DebtorDebt.Status.OPEN,
            party_type=PartyType.PROVIDER,
            party_name=provider.name,
            doc_serial=700,
            currency_code="USD",
            legacy_source_id="",
        )
        DebtorDebt.objects.create(
            provider=provider,
            source_app="billing",
            source_model="Bill",
            source_id="700:USD",
            total=Decimal("9.00"),
            paid_amount=Decimal("0.00"),
            status=DebtorDebt.Status.OPEN,
            party_type=PartyType.PROVIDER,
            party_name=provider.name,
            doc_serial=700,
            currency_code="USD",
            legacy_source_id="700:USD",
        )

        with self.assertLogs("debts.services", level="WARNING") as cm:
            picked = DebtSV.resolve_debtor_entry_for_source(
                source_app="billing",
                source_model="Bill",
                source_id="700",
                currency_code="USD",
                for_update=False,
            )
        self.assertIsNotNone(picked)
        self.assertEqual(picked.id, canonical.id)
        self.assertTrue(any("collision" in m.lower() for m in cm.output))

    def test_legacy_collision_does_not_break_bill_delete_flow(self):
        store, _ = ProductContainer.objects.get_or_create(
            code="store",
            defaults={"name": "Store", "is_store": True, "is_active": True},
        )
        provider = Provider.objects.create(name="Flow Provider")
        cash = MoneyContainer.objects.create(
            name="Flow Cash",
            container_type=MoneyContainer.ContainerType.DRAWER,
            is_active=True,
            created_by=self.actor,
            balance_usd=Decimal("500.00"),
        )
        cash.allowed_users.add(self.actor)
        syp = Currency.objects.get(code="SYP")
        usd = Currency.objects.get(code="USD")
        MoneyContainerCurrency.objects.get_or_create(container=cash, currency=syp, defaults={"is_enabled": True})
        MoneyContainerCurrency.objects.get_or_create(container=cash, currency=usd, defaults={"is_enabled": True})

        col = ProductCollection.objects.create(name="Flow Coll")
        st = ProductSet.objects.create(collection=col, name="Flow Set")
        product = Product.objects.create(
            name="Flow Product",
            set=st,
            unit_primary=UnitType.PIECE,
            allow_syp_purchasing=True,
            allow_usd_purchasing=True,
            allow_syp_sales=True,
            allow_usd_sales=True,
        )

        bill = BillingSV.create_bill(
            actor=self.actor,
            provider_id=provider.id,
            status="unpaid",
            paid_amount=Decimal("0"),
            items=[{"product_id": product.id, "unit_index": 1, "qty_raw": "1", "cost": "10", "currency": "USD"}],
            container=store,
            money_container_id=cash.id,
            settlement_currency="USD",
            fx_usd_syp=Decimal("10000"),
        )

        legacy = DebtorDebt.objects.create(
            provider=provider,
            source_app="billing",
            source_model="Bill",
            source_id=f"{bill.id}:USD",
            total=Decimal("77.00"),
            paid_amount=Decimal("0.00"),
            status=DebtorDebt.Status.OPEN,
            party_type=PartyType.PROVIDER,
            party_name=provider.name,
            doc_serial=bill.serial,
            currency_code="USD",
            legacy_source_id=f"{bill.id}:USD",
        )

        BillingSV.pay_partial(
            actor=self.actor,
            bill_id=bill.id,
            amount=Decimal("1"),
            money_container_id=cash.id,
            currency_code="USD",
        )
        legacy.refresh_from_db()
        self.assertEqual(legacy.paid_amount, Decimal("0.00"))

        BillingSV.delete_bill(actor=self.actor, bill_id=bill.id)
        self.assertFalse(
            DebtorDebt.objects.filter(
                source_app="billing",
                source_model="Bill",
                source_id__in=[str(bill.id), f"{bill.id}:USD"],
            ).exists()
        )

    def test_creditor_collision_resolution_prefers_canonical(self):
        provider = Provider.objects.create(name="Creditor Collision")
        canonical = CreditorDebt.objects.create(
            provider=provider,
            source_app="billing",
            source_model="ProviderReturn",
            source_id="801",
            total=Decimal("10.00"),
            collected=Decimal("0.00"),
            status=CreditorDebt.Status.OPEN,
            party_type=PartyType.PROVIDER,
            party_name=provider.name,
            doc_serial=801,
            currency_code="USD",
            legacy_source_id="",
        )
        CreditorDebt.objects.create(
            provider=provider,
            source_app="billing",
            source_model="ProviderReturn",
            source_id="801:USD",
            total=Decimal("8.00"),
            collected=Decimal("0.00"),
            status=CreditorDebt.Status.OPEN,
            party_type=PartyType.PROVIDER,
            party_name=provider.name,
            doc_serial=801,
            currency_code="USD",
            legacy_source_id="801:USD",
        )

        with self.assertLogs("debts.services", level="WARNING"):
            picked = DebtSV.resolve_creditor_entry_for_source(
                source_app="billing",
                source_model="ProviderReturn",
                source_id="801",
                currency_code="USD",
                for_update=False,
            )
        self.assertIsNotNone(picked)
        self.assertEqual(picked.id, canonical.id)
