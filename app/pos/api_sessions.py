# app/pos/api_sessions.py
from __future__ import annotations
import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpRequest
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import services as POSSV

@login_required
@require_POST
def api_login_end(request: HttpRequest):
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except Exception:
        payload = {}

    reason = (payload.get("reason") or "logout").strip()[:32]
    now = timezone.now()

    closed = POSSV.close_login_session(request.user, now=now, reason=reason)

    return JsonResponse({"ok": True, "closed": closed, "reason": reason})
