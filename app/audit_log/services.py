# app/audit_log/services.py
from __future__ import annotations

import json
from typing import Optional

from django.db import transaction

from .models import AuditLog, AuditAction, AuditSession


def _dumps(d):
    if d is None:
        return None
    return json.dumps(d, ensure_ascii=False, default=str)


def _model_identity(obj) -> tuple[str, str, str]:
    if obj is None:
        return "", "", ""
    meta = obj._meta
    return meta.app_label, meta.model_name, str(getattr(obj, "pk", "") or "")


@transaction.atomic
def log_event(
    *,
    actor=None,
    action: str,
    target=None,
    title: str = "",
    message: str = "",
    before: Optional[dict] = None,
    after: Optional[dict] = None,
    meta: Optional[dict] = None,
    request=None,
    session: AuditSession | None = None,
) -> AuditLog:
    """
    Create an audit log row.

    - If request is provided and middleware ran:
      - ip/ua/path/method are auto-filled
      - actor defaults to request.user
      - session defaults to request.audit_session (if present)
    """
    target_app, target_model, target_id = _model_identity(target)

    ip = None
    ua = ""
    path = ""
    method = ""

    if request is not None:
        ctx = getattr(request, "audit_ctx", None) or {}
        ip = ctx.get("ip")
        ua = ctx.get("user_agent", "")
        path = ctx.get("path", "")
        method = ctx.get("method", "")

        if actor is None and getattr(request, "user", None) is not None:
            if getattr(request.user, "is_authenticated", False):
                actor = request.user

        if session is None:
            session = getattr(request, "audit_session", None)

    return AuditLog.objects.create(
        actor=actor if getattr(actor, "is_authenticated", False) else actor,
        session=session,
        action=action,
        target_app=target_app,
        target_model=target_model,
        target_id=target_id,
        title=title[:255],
        message=message,
        before_json=_dumps(before),
        after_json=_dumps(after),
        meta_json=_dumps(meta),
        ip=ip,
        user_agent=ua,
        path=path,
        method=method,
    )


def log_create(**kwargs): return log_event(action=AuditAction.CREATE, **kwargs)
def log_update(**kwargs): return log_event(action=AuditAction.UPDATE, **kwargs)
def log_delete(**kwargs): return log_event(action=AuditAction.DELETE, **kwargs)
def log_info(**kwargs):   return log_event(action=AuditAction.INFO, **kwargs)
def log_error(**kwargs):  return log_event(action=AuditAction.ERROR, **kwargs)


def snap_instance(obj, fields: list[str] | None = None) -> dict:
    """
    Small safe snapshot for audit. Avoids dumping the whole object / relations.
    """
    if obj is None:
        return {}
    data = {"id": str(getattr(obj, "pk", "") or "")}
    if fields:
        for f in fields:
            data[f] = getattr(obj, f, None)
    return data
