from __future__ import annotations

from django.db import connection
from django.test import TestCase

from debts.schema_contract import DEBT_TABLE_OWNERSHIP, managed_model_contract


class DebtSchemaContractGuardTests(TestCase):
    def test_unmanaged_models_keep_billing_table_contract(self):
        contract = managed_model_contract()
        for model_name, expected_table in DEBT_TABLE_OWNERSHIP.items():
            managed, table = contract[model_name]
            self.assertFalse(managed, msg=f"{model_name} must stay unmanaged")
            self.assertEqual(table, expected_table)

    def test_owned_billing_tables_exist_and_keep_identity_columns(self):
        required_columns = {
            "billing_debtorentry": {"source_id", "legacy_source_id", "currency_code"},
            "billing_creditorentry": {"source_id", "legacy_source_id", "currency_code"},
        }
        with connection.cursor() as cursor:
            table_names = set(connection.introspection.table_names(cursor))
            for table_name, required in required_columns.items():
                self.assertIn(table_name, table_names)
                cols = {
                    c.name
                    for c in connection.introspection.get_table_description(cursor, table_name)
                }
                self.assertTrue(required.issubset(cols))

    def test_payment_and_receipt_fk_targets_point_to_current_debt_tables(self):
        with connection.cursor() as cursor:
            dp_constraints = connection.introspection.get_constraints(cursor, "billing_debtorpayment")
            cr_constraints = connection.introspection.get_constraints(cursor, "billing_creditorreceipt")

        debtor_targets = {
            c["foreign_key"][0]
            for c in dp_constraints.values()
            if c.get("foreign_key")
        }
        creditor_targets = {
            c["foreign_key"][0]
            for c in cr_constraints.values()
            if c.get("foreign_key")
        }

        self.assertIn("billing_debtorentry", debtor_targets)
        self.assertIn("billing_creditorentry", creditor_targets)
