from __future__ import annotations
import json
from decimal import Decimal
from django.http import JsonResponse, HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST
from accounts.models import AccountProfile
from catalog.views import role_required

from debts.models import DebtorDebt as DebtorEntry, CreditorDebt as CreditorEntry
from . import selectors as S
from .serializers import debtor_row, creditor_row
from billing.services import (  # using your existing DebtSV wrappers via billing.services
    create_manual_debt as SV_create_manual_debt,
    pay_manual_debt_full as SV_pay_manual_debt_full,
    pay_manual_debt_partial as SV_pay_manual_debt_partial,
    collect_manual_debt_full as SV_collect_manual_debt_full,
    collect_manual_debt_partial as SV_collect_manual_debt_partial,
)

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
    if not s: return None
    try:
        return date.fromisoformat(s[:10])
    except Exception:
        return None

# ---------- APIs ----------
@require_GET
@role_required(AccountProfile.Role.MANAGER)
def api_debts_list(request: HttpRequest) -> JsonResponse:
    q = (request.GET.get("q") or "").strip()
    raw_status = (request.GET.get("status") or "").lower().strip()
    if raw_status in {"unpaid", "partial"}: status = "open"
    elif raw_status == "paid":               status = "closed"
    elif raw_status in {"open", "closed", ""}: status = raw_status
    else: status = ""
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
    amount     = _dec(payload.get("amount"), "0")
    due_date   = _date(payload.get("due_date"))

    if party_type != "provider":
        return _bad("party_type not supported yet", 422)
    try:
        entry = SV_create_manual_debt(
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
    else:
        return JsonResponse({"ok": True, "item": creditor_row(entry)})

@require_POST
@role_required(AccountProfile.Role.MANAGER)
def api_manual_debt_pay_full(request: HttpRequest, entry_id: int) -> JsonResponse:
    try:
        SV_pay_manual_debt_full(actor=request.user, entry_id=entry_id)
        return JsonResponse({"ok": True})
    except DebtorEntry.DoesNotExist:
        return _bad("not found", 404)
    

@require_POST
@role_required(AccountProfile.Role.MANAGER)
def api_manual_debt_pay_batch(request: HttpRequest, entry_id: int) -> JsonResponse:
    amount_raw = (request.POST.get("amount") or "").strip()
    try:
        amount = Decimal(amount_raw)
    except Exception:
        return _bad("invalid amount")  # 400
    try:
        SV_pay_manual_debt_partial(actor=request.user, entry_id=entry_id, amount=amount)
        return JsonResponse({"ok": True})
    except ValueError as e:
        return _bad(str(e), 400)       # <<< make it 400
    except Exception as e:
        return _bad("server error", 500)


@require_POST
@role_required(AccountProfile.Role.MANAGER)
def api_manual_creditor_collect_full(request: HttpRequest, entry_id: int) -> JsonResponse:
    try:
        SV_collect_manual_debt_full(actor=request.user, entry_id=entry_id)
        return JsonResponse({"ok": True})
    except CreditorEntry.DoesNotExist:
        return _bad("not found", 404)
    
@require_POST
@role_required(AccountProfile.Role.MANAGER)
def api_manual_creditor_collect_batch(request: HttpRequest, entry_id: int) -> JsonResponse:
    amount_raw = (request.POST.get("amount") or "").strip()
    try:
        amount = Decimal(amount_raw)
    except Exception:
        return _bad("invalid amount")  # 400
    if amount <= 0:
        return _bad("Enter a positive amount.")  # 400
    try:
        SV_collect_manual_debt_partial(actor=request.user, entry_id=entry_id, amount=amount)
        return JsonResponse({"ok": True})
    except ValueError as e:
        return _bad(str(e), 400)                 # <<< make it 400
    except Exception as e:
        return _bad("server error", 500)

