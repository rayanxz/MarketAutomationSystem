# app/debts/views.py
from __future__ import annotations
import json
from decimal import Decimal
from datetime import date as _date_cls

from django.http import JsonResponse, HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from accounts.models import AccountProfile
from catalog.views import role_required

from debts.models import DebtorDebt as DebtorEntry, CreditorDebt as CreditorEntry, DebtReminder
from . import selectors as S
from .serializers import debtor_row, creditor_row
from debts import services as SV

# ---------- Pages ----------
@role_required(AccountProfile.Role.MANAGER)
def debts_page(request: HttpRequest) -> HttpResponse:
    return render(request, "debts/debts_list.html")

@role_required(AccountProfile.Role.MANAGER)
def creditors_page(request: HttpRequest) -> HttpResponse:
    return render(request, "debts/creditors_list.html")

@role_required(AccountProfile.Role.MANAGER)
def add_debt(request: HttpRequest) -> HttpResponse:
    return render(request, "debts/add_debt.html")

@role_required(AccountProfile.Role.MANAGER)
def view_debt(request: HttpRequest, direction: str, entry_id: int) -> HttpResponse:
    """Read-only details page for a single debt (debtor|creditor)."""
    direction = (direction or "").lower().strip()
    if direction not in {"debtor", "creditor"}:
        return render(request, "404.html", status=404)
    return render(request, "debts/view_debt.html", {"direction": direction, "entry_id": entry_id})

# ---------- Helpers ----------
def _bad(msg: str, status: int = 400) -> JsonResponse:
    return JsonResponse({"ok": False, "error": msg}, status=status)

def _dec(val, default="0") -> Decimal:
    try:
        return Decimal(str((val if val is not None else default)).replace(",", "."))
    except Exception:
        return Decimal(default)

def _date(val):
    from datetime import date
    s = (val or "").strip()
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except Exception:
        return None

def _entry_details(direction: str, entry_id: int) -> dict:
    """
    Return full details for one entry including:
    core fields, provider, payments/receipts, current reminder, reminders history.
    """
    if direction == "debtor":
        e = DebtorEntry.objects.select_related("provider").get(pk=entry_id)
        payments = list(e.payments.order_by("id").values("id", "created_at", "amount", "journal_entry_id"))
        paid = e.paid_amount or 0
        remaining = (e.total or 0) - paid
        rem_qs = DebtReminder.objects.filter(direction="debtor", debtor=e).order_by("-set_at")
        current_rem = rem_qs.first()
        history = [{"id": r.id, "set_at": r.set_at, "due_date": r.due_date} for r in rem_qs]
        return {
            "id": e.id,
            "direction": "debtor",
            "source_app": e.source_app,
            "source_model": e.source_model,
            "source_id": e.source_id,
            "doc_serial": e.doc_serial,
            "created_at": e.created_at,
            "party_type": getattr(e, "party_type", "provider"),
            "party_name": getattr(e, "party_name", "") or (e.provider.name if e.provider_id else ""),
            "provider": {"id": e.provider_id, "name": e.provider.name if e.provider_id else ""},
            "status": e.status,
            "total": str(e.total),
            "paid_amount": str(paid),
            "remaining": str(remaining),
            "payments": payments,
            "current_reminder": (
                {"id": current_rem.id, "set_at": current_rem.set_at, "due_date": current_rem.due_date}
                if current_rem else None
            ),
            "reminders_history": history,
            "manual": (e.source_model == "ManualDebt"),
        }

    # creditor
    c = CreditorEntry.objects.select_related("provider").get(pk=entry_id)
    receipts = list(c.receipts.order_by("id").values("id", "created_at", "amount", "journal_entry_id"))
    collected = c.collected or 0
    remaining = (c.total or 0) - collected
    rem_qs = DebtReminder.objects.filter(direction="creditor", creditor=c).order_by("-set_at")
    current_rem = rem_qs.first()
    history = [{"id": r.id, "set_at": r.set_at, "due_date": r.due_date} for r in rem_qs]
    return {
        "id": c.id,
        "direction": "creditor",
        "source_app": c.source_app,
        "source_model": c.source_model,
        "source_id": c.source_id,
        "doc_serial": c.doc_serial,
        "created_at": c.created_at,
        "party_type": getattr(c, "party_type", "provider"),
        "party_name": getattr(c, "party_name", "") or (c.provider.name if c.provider_id else ""),
        "provider": {"id": c.provider_id, "name": c.provider.name if c.provider_id else ""},
        "status": c.status,
        "total": str(c.total),
        "paid_amount": str(collected),   # keep API shape same as list rows
        "remaining": str(remaining),
        "receipts": receipts,
        "current_reminder": (
            {"id": current_rem.id, "set_at": current_rem.set_at, "due_date": current_rem.due_date}
            if current_rem else None
        ),
        "reminders_history": history,
        "manual": (c.source_model == "ManualDebt"),
    }

# ---------- APIs ----------
@require_GET
@role_required(AccountProfile.Role.MANAGER)
def api_debts_list(request: HttpRequest) -> JsonResponse:
    q = (request.GET.get("q") or "").strip()
    raw_status = (request.GET.get("status") or "").lower().strip()
    if raw_status in {"unpaid", "partial"}:
        status = "open"
    elif raw_status == "paid":
        status = "closed"
    elif raw_status in {"open", "closed", ""}:
        status = raw_status
    else:
        status = ""
    cursor = request.GET.get("cursor")
    try:
        page_size = min(max(int(request.GET.get("page_size", "30")), 1), 100)
    except ValueError:
        page_size = 30

    qs = S.debtors_list(q=q, status=status, cursor=cursor, page_size=page_size)
    items = list(qs)
    nxt = items[-1].id if items else None
    return JsonResponse({"ok": True, "items": [debtor_row(d) for d in items], "next_cursor": nxt})

@require_GET
@role_required(AccountProfile.Role.MANAGER)
def api_creditors_list(request: HttpRequest) -> JsonResponse:
    q = (request.GET.get("q") or "").strip()
    status = (request.GET.get("status") or "").lower()
    cursor = request.GET.get("cursor")
    try:
        page_size = min(max(int(request.GET.get("page_size", "30")), 1), 100)
    except ValueError:
        page_size = 30

    qs = S.creditors_list(q=q, status=status, cursor=cursor, page_size=page_size)
    items = list(qs)
    nxt = items[-1].id if items else None
    return JsonResponse({"ok": True, "items": [creditor_row(c) for c in items], "next_cursor": nxt})

@require_POST
@role_required(AccountProfile.Role.MANAGER)
def api_manual_debt_save(request: HttpRequest) -> JsonResponse:
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except Exception:
        return _bad("bad json")

    direction  = (payload.get("direction") or "").strip().lower()
    party_type = (payload.get("party_type") or "provider").strip().lower()

    party      = payload.get("party") or {}
    party_id   = party.get("id")
    party_name = (party.get("name") or "").strip()
    # Fallback to flat fields
    if not party_id and payload.get("provider_id") is not None:
        party_id = payload.get("provider_id")
    if not party_name and payload.get("party_name"):
        party_name = (payload.get("party_name") or "").strip()

    amount   = _dec(payload.get("amount"), "0")
    due_date = _date(payload.get("due_date"))

    if party_type != "provider":
        return _bad("party_type not supported yet", 422)
    if not party_id:
        return _bad("provider must be selected", 400)

    try:
        entry = SV.create_manual_debt(
            actor=request.user,
            direction=direction,
            party_type=party_type,
            provider_id=int(party_id) if party_id else None,
            party_name=party_name,
            amount=amount,
            due_date=due_date,
        )
    except ValueError as ve:
        return _bad(str(ve))
    except Exception:
        return _bad("save failed", 500)

    if direction == "debtor":
        return JsonResponse({"ok": True, "item": debtor_row(entry)})
    return JsonResponse({"ok": True, "item": creditor_row(entry)})

@require_POST
@role_required(AccountProfile.Role.MANAGER)
def api_manual_debt_pay_full(request: HttpRequest, entry_id: int) -> JsonResponse:
    try:
        SV.pay_debt(actor=request.user, entry_id=entry_id, full=True)
        return JsonResponse({"ok": True})
    except DebtorEntry.DoesNotExist:
        return _bad("not found", 404)

@require_POST
@role_required(AccountProfile.Role.MANAGER)
def api_manual_debt_pay_batch(request: HttpRequest, entry_id: int) -> JsonResponse:
    try:
        amount = _dec(request.POST.get("amount"), "0")
    except Exception:
        return _bad("invalid amount")
    try:
        SV.pay_debt(actor=request.user, entry_id=entry_id, amount=amount, full=False)
        return JsonResponse({"ok": True})
    except ValueError as e:
        return _bad(str(e), 400)
    except Exception:
        return _bad("server error", 500)

@require_POST
@role_required(AccountProfile.Role.MANAGER)
def api_manual_creditor_collect_full(request: HttpRequest, entry_id: int) -> JsonResponse:
    try:
        SV.collect_debt(actor=request.user, entry_id=entry_id, full=True)
        return JsonResponse({"ok": True})
    except CreditorEntry.DoesNotExist:
        return _bad("not found", 404)

@require_POST
@role_required(AccountProfile.Role.MANAGER)
def api_manual_creditor_collect_batch(request: HttpRequest, entry_id: int) -> JsonResponse:
    try:
        amount = _dec(request.POST.get("amount"), "0")
    except Exception:
        return _bad("invalid amount")
    if amount <= 0:
        return _bad("Enter a positive amount.")
    try:
        SV.collect_debt(actor=request.user, entry_id=entry_id, amount=amount, full=False)
        return JsonResponse({"ok": True})
    except ValueError as e:
        return _bad(str(e), 400)
    except Exception:
        return _bad("server error", 500)

@require_GET
@role_required(AccountProfile.Role.MANAGER)
def api_debt_details(request: HttpRequest, direction: str, entry_id: int) -> JsonResponse:
    direction = (direction or "").lower().strip()
    if direction not in {"debtor", "creditor"}:
        return _bad("bad direction", 400)
    try:
        data = _entry_details(direction, entry_id)
        return JsonResponse({"ok": True, "item": data})
    except (DebtorEntry.DoesNotExist, CreditorEntry.DoesNotExist):
        return _bad("not found", 404)

@require_POST
@role_required(AccountProfile.Role.MANAGER)
def api_entry_pay_full(request: HttpRequest, direction: str, entry_id: int) -> JsonResponse:
    direction = (direction or "").lower().strip()
    try:
        if direction == "debtor":
            SV.pay_debt(actor=request.user, entry_id=entry_id, full=True)
        elif direction == "creditor":
            SV.collect_debt(actor=request.user, entry_id=entry_id, full=True)
        else:
            return _bad("bad direction", 400)
        return JsonResponse({"ok": True})
    except (DebtorEntry.DoesNotExist, CreditorEntry.DoesNotExist):
        return _bad("not found", 404)
    except ValueError as e:
        return _bad(str(e), 400)
    except Exception:
        return _bad("server error", 500)

@require_POST
@role_required(AccountProfile.Role.MANAGER)
def api_entry_pay_batch(request: HttpRequest, direction: str, entry_id: int) -> JsonResponse:
    direction = (direction or "").lower().strip()
    try:
        amount = _dec(request.POST.get("amount"), "0")
    except Exception:
        return _bad("invalid amount", 400)
    if amount <= 0:
        return _bad("Enter a positive amount.", 400)
    try:
        if direction == "debtor":
            SV.pay_debt(actor=request.user, entry_id=entry_id, amount=amount, full=False)
        elif direction == "creditor":
            SV.collect_debt(actor=request.user, entry_id=entry_id, amount=amount, full=False)
        else:
            return _bad("bad direction", 400)
        return JsonResponse({"ok": True})
    except (DebtorEntry.DoesNotExist, CreditorEntry.DoesNotExist):
        return _bad("not found", 404)
    except ValueError as e:
        return _bad(str(e), 400)
    except Exception:
        return _bad("server error", 500)

@require_http_methods(["POST"])
@role_required(AccountProfile.Role.MANAGER)
def api_entry_set_reminder(request: HttpRequest, direction: str, entry_id: int) -> JsonResponse:
    direction = (direction or "").lower().strip()
    raw = (request.POST.get("due_date") or "").strip()
    try:
        due_date = _date_cls.fromisoformat(raw[:10])
    except Exception:
        return _bad("invalid date", 400)

    if direction not in {"debtor", "creditor"}:
        return _bad("bad direction", 400)
    try:
        rem = SV.set_reminder(debt_id=entry_id, direction=direction, reminder_date=due_date)
        return JsonResponse({"ok": True, "reminder": {"id": rem.id, "set_at": rem.set_at, "due_date": rem.due_date}})
    except (DebtorEntry.DoesNotExist, CreditorEntry.DoesNotExist):
        return _bad("not found", 404)
    except Exception:
        return _bad("server error", 500)
