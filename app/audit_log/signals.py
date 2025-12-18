# app/audit_log/signals.py
from __future__ import annotations

from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.dispatch import receiver
from django.utils import timezone

from .models import AuditSession, AuditEntrypoint, AuditAction
from .services import log_event


SESSION_KEY = "audit_session_id"


def _entrypoint_from_request(request) -> str:
    # 1) if login page stored it explicitly, trust that
    ep = ""
    try:
        ep = (request.session.get("audit_entrypoint") or "").strip().lower()
    except Exception:
        ep = ""

    if ep in ("cashier", "pos"):
        return AuditEntrypoint.POS
    if ep == "manager":
        return AuditEntrypoint.MANAGER
    if ep == "owner":
        return AuditEntrypoint.OWNER

    # 2) fallback to path-based detection (useful for API token logins etc.)
    path = (getattr(request, "path", "") or "").lower()
    if path.startswith("/pos"):
        return AuditEntrypoint.POS
    if path.startswith("/manager"):
        return AuditEntrypoint.MANAGER
    if path.startswith("/owner"):
        return AuditEntrypoint.OWNER
    if path.startswith("/api"):
        return AuditEntrypoint.API
    return AuditEntrypoint.UNKNOWN


@receiver(user_logged_in)
def audit_on_login(sender, request, user, **kwargs):

    # Close any previous session id left hanging in this browser session
    old_id = request.session.get(SESSION_KEY)
    if old_id:
        AuditSession.objects.filter(pk=old_id, ended_at__isnull=True).update(
            ended_at=timezone.now(),
            logout_reason="relogin",
        )

    entrypoint = _entrypoint_from_request(request)
    ctx = getattr(request, "audit_ctx", {}) or {}

    sess = AuditSession.objects.create(
        actor=user,
        started_at=timezone.now(),
        entrypoint=entrypoint,
        ip=ctx.get("ip"),
        user_agent=ctx.get("user_agent", ""),
        login_path=ctx.get("path", ""),
        django_session_key=(getattr(request.session, "session_key", "") or ""),
    )

    request.session[SESSION_KEY] = sess.id
    request.session.pop("audit_entrypoint", None)
    request.audit_session = sess  # useful immediately in same request

    # Also log an event
    log_event(
        actor=user,
        action=AuditAction.LOGIN,
        title="User login",
        message=f"Login to {entrypoint}",
        meta={"entrypoint": entrypoint},
        request=request,
        session=sess,
    )


@receiver(user_logged_out)
def audit_on_logout(sender, request, user, **kwargs):
    
    if request is None:
        # still log it, but without request/session context
        log_event(
            actor=user,
            action=AuditAction.LOGOUT,
            title="User logout",
            message="Logout",
            meta={"entrypoint": AuditEntrypoint.UNKNOWN},
            request=None,
            session=None,
        )
        return
    sid = request.session.get(SESSION_KEY)

    sess = None
    if sid:
        sess = AuditSession.objects.filter(pk=sid).first()

    if sess and sess.ended_at is None:
        sess.ended_at = timezone.now()
        sess.logout_reason = "logout"
        sess.save(update_fields=["ended_at", "logout_reason"])

    # log logout event even if session missing
    log_event(
        actor=user,
        action=AuditAction.LOGOUT,
        title="User logout",
        message="Logout",
        meta={"entrypoint": getattr(sess, "entrypoint", AuditEntrypoint.UNKNOWN)},
        request=request,
        session=sess,
    )

    try:
        del request.session[SESSION_KEY]
    except Exception:
        pass
