from __future__ import annotations

import re
from pathlib import Path

from django.test import TestCase


class LedgerImportGuardTests(TestCase):
    def test_no_ledger_imports_outside_ledger_app(self):
        base = Path("app")
        ledger_dir = base / "ledger"
        offenders = []
        pattern = re.compile(r"^\s*(from|import)\s+ledger\b", re.M)

        for path in base.rglob("*.py"):
            if ledger_dir in path.parents:
                continue
            text = path.read_text(encoding="utf-8")
            if pattern.search(text):
                offenders.append(str(path))

        if offenders:
            self.fail("Ledger imports found:\n" + "\n".join(offenders))
