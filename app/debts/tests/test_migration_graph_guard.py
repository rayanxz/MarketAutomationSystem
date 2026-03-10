from __future__ import annotations

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader
from django.test import TestCase


class MigrationGraphGuardTests(TestCase):
    def test_migration_graph_has_no_conflicts(self):
        loader = MigrationLoader(connection, ignore_no_migrations=True)
        self.assertEqual(loader.detect_conflicts(), {})

    def test_project_is_at_latest_leaf_nodes_in_test_database(self):
        executor = MigrationExecutor(connection)
        loader = executor.loader
        leaf_nodes = loader.graph.leaf_nodes()
        plan = executor.migration_plan(leaf_nodes)
        self.assertEqual(plan, [])
