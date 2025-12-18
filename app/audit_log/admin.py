# app/audit_log/admin.py
from __future__ import annotations

from django.contrib import admin
from .models import AuditLog, AuditSession


@admin.register(AuditSession)
class AuditSessionAdmin(admin.ModelAdmin):
    list_display = ("started_at", "ended_at", "actor", "entrypoint", "ip", "login_path")
    list_filter = ("entrypoint", "started_at", "ended_at")
    search_fields = ("actor__username", "actor__email", "ip", "login_path", "user_agent")
    readonly_fields = [f.name for f in AuditSession._meta.fields]
    ordering = ("-started_at", "-id")

    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "action", "actor", "session", "target_app", "target_model", "target_id", "title")
    list_filter = ("action", "target_app", "target_model", "created_at")
    search_fields = ("title", "message", "target_id", "actor__username", "actor__email", "path", "ip")
    readonly_fields = [f.name for f in AuditLog._meta.fields]
    ordering = ("-created_at", "-id")

    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
