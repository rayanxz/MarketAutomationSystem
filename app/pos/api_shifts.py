# app/pos/api_shifts.py
from __future__ import annotations

import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpRequest
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.db import transaction

from .models import PosShift
from . import services as POSSV

from audit_log import services as AuditSV
from audit_log.models import AuditAction


@login_required
@require_POST
def api_shift_start(request: HttpRequest):
    """
    Start a POS shift for the current user.
    If there is already an open shift for this user, we just return it.
    """
    now = timezone.now()

    with transaction.atomic():
        # Make sure we are attached to a POS work day + login session
        session = POSSV.get_or_create_login_session(request.user, now=now)
        day = session.day if session is not None else POSSV.get_or_create_work_day(now)

        # Only one open shift per user at a time
        shift = (
            PosShift.objects
            .select_for_update()
            .filter(user=request.user, ended_at__isnull=True)
            .order_by("-started_at")
            .first()
        )

        created = False
        if shift is None:
            shift = PosShift.objects.create(
                user=request.user,
                day=day,
                login_session=session,
                started_at=now,
                title="",  # can be extended later
            )
            created = True

            # ✅ Audit ONLY when we actually created a new shift
            meta = {
                "kind": "pos.shift_started",
                "shift": {
                    "id": shift.id,
                    "day": str(day.date) if day else "",
                    "login_session_id": shift.login_session_id,
                    "started_at": shift.started_at.isoformat() if shift.started_at else "",
                },
            }
            transaction.on_commit(lambda: AuditSV.log_event(
                action=AuditAction.INFO,
                actor=request.user,
                request=request,
                target=shift,
                title="POS shift started",
                message="POS shift started",
                meta=meta,
            ))

    return JsonResponse({
        "ok": True,
        "id": shift.id,
        "created": created,
        "started_at": shift.started_at.isoformat(),
        "day": str(day.date),
    })


@login_required
@require_POST
def api_shift_end(request: HttpRequest):
    """
    End the current shift for this user (by id).
    Body JSON: {"id": <shift_id>}
    """
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except Exception:
        payload = {}

    shift_id = payload.get("id")
    if not shift_id:
        return JsonResponse({"ok": False, "error": "MISSING_ID"}, status=400)

    with transaction.atomic():
        try:
            shift = PosShift.objects.select_for_update().get(pk=shift_id)
        except PosShift.DoesNotExist:
            return JsonResponse({"ok": False, "error": "NOT_FOUND"}, status=404)

        # Only the owner or superuser can end a shift
        if shift.user_id and shift.user_id != request.user.id and not request.user.is_superuser:
            return JsonResponse({"ok": False, "error": "PERMISSION_DENIED"}, status=403)

        ended_now = False
        if shift.ended_at is None:
            shift.ended_at = timezone.now()
            shift.save(update_fields=["ended_at"])
            ended_now = True

        # ✅ Audit ONLY if we actually ended it now (no double logs)
        if ended_now:
            meta = {
                "kind": "pos.shift_ended",
                "shift": {
                    "id": shift.id,
                    "day": str(shift.day.date) if shift.day_id else "",
                    "login_session_id": shift.login_session_id,
                    "started_at": shift.started_at.isoformat() if shift.started_at else "",
                    "ended_at": shift.ended_at.isoformat() if shift.ended_at else "",
                },
            }
            transaction.on_commit(lambda: AuditSV.log_event(
                action=AuditAction.INFO,
                actor=request.user,
                request=request,
                target=shift,
                title="POS shift ended",
                message="POS shift ended",
                meta=meta,
            ))

    return JsonResponse({
        "ok": True,
        "id": shift.id,
        "ended_at": shift.ended_at.isoformat(),
    })
