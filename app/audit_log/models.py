# app/audit_log/models.py
from __future__ import annotations

import json

from django.conf import settings
from django.db import models
from django.utils import timezone


def _loads(s):
    if not s:
        return None
    try:
        return json.loads(s)
    except Exception:
        return None


def _dumps(d):
    if d is None:
        return None
    try:
        return json.dumps(d, ensure_ascii=False, default=str)
    except Exception:
        return json.dumps({"_error": "json_dump_failed"}, ensure_ascii=False)


class AuditEntrypoint(models.TextChoices):
    UNKNOWN = "unknown", "Unknown"
    POS = "pos", "POS"
    MANAGER = "manager", "Manager"
    OWNER = "owner", "Owner"
    API = "api", "API"


class AuditAction(models.TextChoices):
    CREATE = "create", "Create"
    UPDATE = "update", "Update"
    DELETE = "delete", "Delete"
    LOGIN  = "login",  "Login"
    LOGOUT = "logout", "Logout"
    API    = "api",    "API"
    ERROR  = "error",  "Error"
    INFO   = "info",   "Info"


class AuditSession(models.Model):
    """
    One login session for a user (our own tracking, not django_session table).
    """
    started_at = models.DateTimeField(default=timezone.now, db_index=True)
    ended_at = models.DateTimeField(null=True, blank=True, db_index=True)

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_sessions",
    )

    entrypoint = models.CharField(
        max_length=16,
        choices=AuditEntrypoint.choices,
        default=AuditEntrypoint.UNKNOWN,
        db_index=True,
    )

    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True, default="")
    login_path = models.CharField(max_length=512, blank=True, default="")

    logout_reason = models.CharField(max_length=64, blank=True, default="")
    django_session_key = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        ordering = ["-started_at", "-id"]
        indexes = [
            models.Index(fields=["actor", "started_at"]),
            models.Index(fields=["entrypoint", "started_at"]),
            models.Index(fields=["ended_at"]),
        ]

    def __str__(self) -> str:
        who = getattr(self.actor, "username", None) or "—"
        return f"Session {self.id} {who} ({self.entrypoint})"


class AuditLog(models.Model):
    """
    One immutable audit entry (event stream).
    """

    @property
    def before(self):
        return _loads(self.before_json)

    @property
    def after(self):
        return _loads(self.after_json)

    @property
    def meta(self):
        return _loads(self.meta_json)

    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_logs",
    )

    # NEW: link event to an audit session (optional)
    session = models.ForeignKey(
        AuditSession,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="events",
    )

    action = models.CharField(max_length=16, choices=AuditAction.choices, db_index=True)

    target_app = models.CharField(max_length=64, blank=True, default="", db_index=True)
    target_model = models.CharField(max_length=64, blank=True, default="", db_index=True)
    target_id = models.CharField(max_length=64, blank=True, default="", db_index=True)

    title = models.CharField(max_length=255, blank=True, default="")
    message = models.TextField(blank=True, default="")

    before_json = models.TextField(null=True, blank=True, default=None)
    after_json  = models.TextField(null=True, blank=True, default=None)

    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True, default="")
    path = models.CharField(max_length=512, blank=True, default="")
    method = models.CharField(max_length=16, blank=True, default="")

    meta_json = models.TextField(null=True, blank=True, default=None)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["target_app", "target_model", "target_id"]),
            models.Index(fields=["actor", "created_at"]),
            models.Index(fields=["action", "created_at"]),
            models.Index(fields=["session", "created_at"]),
        ]

    def __str__(self) -> str:
        who = getattr(self.actor, "username", None) or "—"
        tgt = f"{self.target_app}.{self.target_model}#{self.target_id}" if self.target_model else "—"
        return f"[{self.created_at:%Y-%m-%d %H:%M}] {who} {self.action} {tgt}"
