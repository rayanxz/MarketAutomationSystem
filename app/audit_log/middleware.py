# app/audit_log/middleware.py
from __future__ import annotations

from django.utils.deprecation import MiddlewareMixin

from .models import AuditSession


class AuditRequestContextMiddleware(MiddlewareMixin):
    """
    Adds:
      - request.audit_ctx: ip/ua/path/method
      - request.audit_session: AuditSession instance if request.session has audit_session_id
    """
    SESSION_KEY = "audit_session_id"

    def process_request(self, request):
        ip = request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip() or request.META.get("REMOTE_ADDR")
        request.audit_ctx = {
            "ip": ip,
            "user_agent": request.META.get("HTTP_USER_AGENT", "")[:2000],
            "path": (getattr(request, "path", "") or "")[:512],
            "method": (getattr(request, "method", "") or "")[:16],
        }

        request.audit_session = None
        try:
            sid = request.session.get(self.SESSION_KEY)
        except Exception:
            sid = None

        if sid:
            # no lock needed here; it’s just reading
            request.audit_session = AuditSession.objects.filter(pk=sid).first()

        return None
