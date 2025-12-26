# catalog/io_records.py
from __future__ import annotations

import json
from typing import Any

from django.conf import settings
from django.db import models


def _dumps(v: Any) -> str:
    # Store as JSON text (SQLite-safe)
    return json.dumps(v, ensure_ascii=False, default=str)


def _loads(s: str) -> Any:
    if not s:
        return None
    try:
        return json.loads(s)
    except Exception:
        return None


class CatalogDataJob(models.Model):
    class Kind(models.TextChoices):
        IMPORT = "import", "Import"
        EXPORT = "export", "Export"

    class Status(models.TextChoices):
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"

    kind = models.CharField(max_length=16, choices=Kind.choices, db_index=True)
    status = models.CharField(max_length=16, choices=Status.choices, db_index=True)

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="catalog_data_jobs",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    # SQLite-safe JSON storage
    summary_text = models.TextField(blank=True, default="{}")
    rows_text = models.TextField(blank=True, default="[]")
    meta_text = models.TextField(blank=True, default="{}")

    error = models.TextField(blank=True, default="")

    # ---------- convenience properties ----------
    @property
    def summary_json(self) -> dict:
        v = _loads(self.summary_text)
        return v if isinstance(v, dict) else {}

    @summary_json.setter
    def summary_json(self, v: Any) -> None:
        self.summary_text = _dumps(v if isinstance(v, dict) else {})

    @property
    def rows_json(self) -> list:
        v = _loads(self.rows_text)
        return v if isinstance(v, list) else []

    @rows_json.setter
    def rows_json(self, v: Any) -> None:
        self.rows_text = _dumps(v if isinstance(v, list) else [])

    @property
    def meta_json(self) -> dict:
        v = _loads(self.meta_text)
        return v if isinstance(v, dict) else {}

    @meta_json.setter
    def meta_json(self, v: Any) -> None:
        self.meta_text = _dumps(v if isinstance(v, dict) else {})

    def __str__(self) -> str:
        return f"CatalogDataJob#{self.pk} {self.kind} {self.status}"
