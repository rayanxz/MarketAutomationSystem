# app/debts/views.py
from __future__ import annotations
import json
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import date as _date_cls

from django.http import JsonResponse, HttpRequest, HttpResponse
from django.shortcuts import render, redirect
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from django.contrib.auth import get_user_model

from accounts.models import AccountProfile
from accounts.decorators import role_required

from debts.models import (
    DebtorDebt as DebtorEntry,
    CreditorDebt as CreditorEntry,
    DebtReminder,
    DebtRecord,
    DebtDirection,
    DebtCauseType,
    DebtStatus,
    OtherPartyType,
)
from debts.cause_refs import cause_ref_for_ui
from debts.source_identity import source_identity_base
from financials.models import Receipt, ReceiptKind, MoneyContainer
from financials import services as FinSV
from core.date_filters import parse_filter_date
from core.formatters import round_money
from . import selectors as S
from .serializers import debtor_row, creditor_row, central_debt_row
from debts import services as SV
from billing.models import Provider
from pos.models import CustomerProfile

DEC0 = Decimal("0")
DEC2 = Decimal("0.01")


def _ui_2dp(x: Decimal | None) -> Decimal:
    """
    Display-only quantization to 2 decimals (half-up).
    Keep DB precision unchanged.
    """
    try:
        d = Decimal(str(x if x is not None else DEC0))
    except (InvalidOperation, TypeError, ValueError):
        return DEC0
    return d.quantize(DEC2, rounding=ROUND_HALF_UP)


def _dec_or_zero(x: Decimal | None) -> Decimal:
    try:
        return Decimal(str(x if x is not None else DEC0))
    except (InvalidOperation, TypeError, ValueError):
        return DEC0


def _positive_fx_or_none(x: Decimal | None) -> Decimal | None:
    try:
        d = Decimal(str(x))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if d <= DEC0:
        return None
    return d


def _build_settlement_ui(*, debt: DebtRecord) -> dict[str, object]:
    rem_syp = _dec_or_zero(debt.remaining_syp)
    rem_usd = _dec_or_zero(debt.remaining_usd)
    try:
        fx = _positive_fx_or_none(FinSV.get_current_fx_syp_per_usd())
    except Exception:
        fx = None

    total_syp: Decimal | None
    total_usd: Decimal | None

    if fx is None:
        total_syp = rem_syp if rem_usd <= DEC0 else None
        total_usd = rem_usd if rem_syp <= DEC0 else None
    else:
        total_syp = rem_syp + (rem_usd * fx)
        total_usd = rem_usd + (rem_syp / fx)

    if rem_syp > DEC0 and rem_usd <= DEC0:
        default_currency = "SYP"
    elif rem_usd > DEC0 and rem_syp <= DEC0:
        default_currency = "USD"
    else:
        default_currency = "SYP"

    currency_options = ["SYP", "USD"]

    if default_currency not in currency_options:
        default_currency = currency_options[0]

    return {
        "default_currency": default_currency,
        "currency_options": currency_options,
        "total_syp": (_ui_2dp(total_syp) if total_syp is not None else None),
        "total_usd": (_ui_2dp(total_usd) if total_usd is not None else None),
        "fx_syp_per_usd_current": (_ui_2dp(fx) if fx is not None else None),
    }


def _dec_to_str(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value, "f")


def _central_settlement_row_payload(settlement) -> dict[str, object]:
    return {
        "created_at": settlement.created_at.isoformat() if settlement.created_at else None,
        "payment_syp": _dec_to_str(settlement.payment_syp),
        "payment_usd": _dec_to_str(settlement.payment_usd),
        "applied_syp": _dec_to_str(settlement.applied_syp),
        "applied_usd": _dec_to_str(settlement.applied_usd),
        "money_container_name": settlement.money_container.name if settlement.money_container_id else "",
        "receipt_serial": settlement.receipt.serial if settlement.receipt_id else "",
        "actor_username": settlement.actor_username or "",
    }

# ---------- Pages ----------
@role_required(AccountProfile.Role.MANAGER)
def debts_page(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "debts/debts_list.html",
        {
            "money_containers": _allowed_containers(request.user),
            "cause_type_choices": list(DebtCauseType.choices),
            "other_party_type_choices": list(OtherPartyType.choices),
        },
    )

@role_required(AccountProfile.Role.MANAGER)
def creditors_page(request: HttpRequest) -> HttpResponse:
    return redirect("debts_page")

@role_required(AccountProfile.Role.MANAGER)
def add_debt(request: HttpRequest) -> HttpResponse:
    return render(request, "debts/add_debt.html", {"money_containers": _allowed_containers(request.user)})

@role_required(AccountProfile.Role.MANAGER)
def view_debt(request: HttpRequest, direction: str, entry_id: int) -> HttpResponse:
    """Read-only details page for a single debt (debtor|creditor)."""
    direction = (direction or "").lower().strip()
    if direction not in {"debtor", "creditor"}:
        return HttpResponse(status=404)
    return render(
        request,
        "debts/view_debt.html",
        {
            "direction": direction,
            "entry_id": entry_id,
            "money_containers": _allowed_containers(request.user),
        },
    )


@role_required(AccountProfile.Role.MANAGER)
def view_central_debt(request: HttpRequest, debt_ref: str) -> HttpResponse:
    ref = (debt_ref or "").strip()
    qs = DebtRecord.objects.select_related("provider", "customer")

    debt = qs.filter(public_id__iexact=ref).first()
    if debt is None:
        return HttpResponse(status=404)

    source_url = _source_url_for_central_debt(debt)
    view_direction, view_entry_id = _resolve_central_view_target(debt)
    legacy_view_url = ""
    if view_direction and view_entry_id:
        legacy_view_url = f"/manager/debts/view/{view_direction}/{view_entry_id}/"

    settlements = list(
        debt.settlements.select_related("receipt", "money_container").order_by("-created_at")
    )
    settlement_ui = _build_settlement_ui(debt=debt)
    return render(
        request,
        "debts/view_central_debt.html",
        {
            "debt": debt,
            "debt_ui": {
                "total_syp": _ui_2dp(debt.total_syp),
                "total_usd": _ui_2dp(debt.total_usd),
                "remaining_syp": _ui_2dp(debt.remaining_syp),
                "remaining_usd": _ui_2dp(debt.remaining_usd),
                "fx_syp_per_usd_at_creation": (
                    _ui_2dp(debt.fx_syp_per_usd_at_creation)
                    if debt.fx_syp_per_usd_at_creation is not None
                    else None
                ),
            },
            "settlement_ui": settlement_ui,
            "source_url": source_url,
            "legacy_view_url": legacy_view_url,
            "settlements": settlements,
            "money_containers": _allowed_containers(request.user),
        },
    )


# ---------- Helpers ----------
def _bad(msg: str, status: int = 400) -> JsonResponse:
    return JsonResponse({"ok": False, "error": msg}, status=status)

def _dec(val, default="0") -> Decimal:
    try:
        return Decimal(str((val if val is not None else default)).replace(",", "."))
    except Exception:
        return Decimal(default)


def _money_has_more_than_2_decimals(value: Decimal) -> bool:
    return Decimal(value).as_tuple().exponent < -2


def _parse_money_input(val, default: str = "0", *, field_name: str = "amount") -> Decimal:
    amount = _dec(val, default)
    if not amount.is_finite():
        raise ValueError(f"Invalid {field_name}")
    if _money_has_more_than_2_decimals(amount):
        raise ValueError(f"{field_name} supports at most 2 decimal digits")
    return round_money(amount)

def _date(val):
    return parse_filter_date(val)
    
    
def _int_or_none(s):
    try:
        if s is None:
            return None
        if isinstance(s, str):
            s = s.strip()
            return int(s) if s else None
        return int(s)
    except Exception:
        return None


def _allowed_containers(user):
    return list(
        FinSV.money_containers_for_user_qs(user=user).order_by("name")
    )


def _source_url_for_central_debt(debt: DebtRecord) -> str:
    cause_type = (debt.cause_type or "").strip().lower()
    cause_id = cause_ref_for_ui(
        cause_type=debt.cause_type,
        cause_id=debt.cause_id,
    )
    if not cause_id:
        return ""
    if cause_type == DebtCauseType.PURCHASE_BILL:
        return reverse("billing_bill_view", kwargs={"bill_id": cause_id})
    if cause_type == DebtCauseType.PROVIDER_RETURN:
        return reverse("billing_return_view", kwargs={"ret_id": cause_id})
    if cause_type == DebtCauseType.POS_BILL:
        return reverse("pos:pos_manager_bill_detail", kwargs={"bill_id": cause_id})
    return ""


def _resolve_central_view_target(debt: DebtRecord) -> tuple[str, int] | tuple[None, None]:
    direction = (debt.direction or "").strip().lower()
    cause_type = (debt.cause_type or "").strip().lower()
    cause_id = str(debt.cause_id or "").strip()

    if not cause_id:
        return None, None

    if cause_type == DebtCauseType.MANUAL:
        try:
            entry_id = int(cause_id)
        except Exception:
            return None, None
        if direction == DebtDirection.PAYABLE:
            return "debtor", entry_id
        if direction == DebtDirection.RECEIVABLE:
            return "creditor", entry_id
        return None, None

    if direction == DebtDirection.PAYABLE:
        entry = None
        if cause_type == DebtCauseType.PURCHASE_BILL:
            entry = SV.resolve_debtor_entry_for_source(
                source_app="billing",
                source_model="Bill",
                source_id=cause_id,
            )
        elif cause_type == DebtCauseType.POS_BILL:
            entry = SV.resolve_debtor_entry_for_source(
                source_app="pos",
                source_model="SalesBill",
                source_id=cause_id,
            )
        if entry:
            return "debtor", int(entry.id)
        return None, None

    if direction == DebtDirection.RECEIVABLE:
        entry = None
        if cause_type == DebtCauseType.PROVIDER_RETURN:
            entry = SV.resolve_creditor_entry_for_source(
                source_app="billing",
                source_model="ProviderReturn",
                source_id=cause_id,
            )
        if entry:
            return "creditor", int(entry.id)
        return None, None

    return None, None


def _central_row_with_links(debt: DebtRecord) -> dict:
    row = central_debt_row(debt)
    view_direction, view_entry_id = _resolve_central_view_target(debt)
    row["view_direction"] = view_direction or ""
    row["view_entry_id"] = view_entry_id
    row["legacy_view_url"] = f"/manager/debts/view/{view_direction}/{view_entry_id}/" if (view_direction and view_entry_id) else ""
    row["debt_view_url"] = f"/manager/debts/view/record/{debt.public_id}/"
    return row


def _entry_details(direction: str, entry_id: int) -> dict:
    """
    Return full details for one entry including:
    core fields, provider, payments/receipts, current reminder, reminders history.
    """
    def _receipt_source_id(src_id: str, legacy_id: str) -> str:
        return source_identity_base(source_id=src_id, legacy_source_id=legacy_id)

    def _exposure_receipts(source_app: str, source_model: str, source_id: str):
        if not source_app or not source_model or not source_id:
            return []
        qs = (
            Receipt.objects
            .filter(
                source_app=source_app,
                source_model=source_model,
                source_id=str(source_id),
                kind=ReceiptKind.COUNTERPARTY_INC,
            )
            .order_by("-id")
        )
        out = []
        for r in qs:
            cur = None
            for ln in r.lines.all():
                cur = ln.currency.code
                break
            out.append(
                {
                    "id": r.id,
                    "serial": r.serial,
                    "status": r.status,
                    "kind": r.kind,
                    "currency_code": cur or "SYP",
                }
            )
        return out

    if direction == "debtor":
        e = DebtorEntry.objects.select_related("provider").get(pk=entry_id)
        payments = []
        for p in e.payments.select_related("receipt", "money_container").order_by("id"):
            rcpt = p.receipt
            payments.append(
                {
                    "id": p.id,
                    "created_at": p.created_at,
                    "amount": str(p.amount),
                    "currency_code": (getattr(p, "currency_code", None) or getattr(e, "currency_code", None) or "SYP"),
                    "receipt_id": rcpt.id if rcpt else None,
                    "receipt_serial": rcpt.serial if rcpt else None,
                    "receipt_status": rcpt.status if rcpt else None,
                    "container_id": p.money_container_id,
                    "container_name": p.money_container.name if p.money_container_id else "",
                    "fx_syp_per_usd_used": str(p.fx_syp_per_usd_used) if p.fx_syp_per_usd_used else None,
                    "journal_entry_id": getattr(p, "journal_entry_id", None),
                }
            )
        paid = e.paid_amount or 0
        remaining = (e.total or 0) - paid
        rem_qs = DebtReminder.objects.filter(direction="debtor", debtor=e).order_by("-set_at")
        current_rem = rem_qs.first()
        history = [{"id": r.id, "set_at": r.set_at, "due_date": r.due_date} for r in rem_qs]
        exposure_receipts = _exposure_receipts(
            e.source_app,
            e.source_model,
            _receipt_source_id(e.source_id, getattr(e, "legacy_source_id", "")),
        )
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
            "customer": {"id": getattr(e, "customer_id", None), "name": e.customer.name if getattr(e, "customer", None) else ""},
            "currency_code": (getattr(e, "currency_code", None) or "SYP"),
            "status": e.status,
            "total": str(e.total),
            "paid_amount": str(paid),
            "remaining": str(remaining),
            "payments": payments,
            "exposure_receipts": exposure_receipts,
            "current_reminder": (
                {"id": current_rem.id, "set_at": current_rem.set_at, "due_date": current_rem.due_date}
                if current_rem else None
            ),
            "reminders_history": history,
            "manual": (e.source_model == "ManualDebt"),
        }

    # creditor
    c = CreditorEntry.objects.select_related("provider").get(pk=entry_id)
    receipts = []
    for r in c.receipts.select_related("receipt", "money_container").order_by("id"):
        rcpt = r.receipt
        receipts.append(
            {
                "id": r.id,
                "created_at": r.created_at,
                "amount": str(r.amount),
                "currency_code": (getattr(r, "currency_code", None) or getattr(c, "currency_code", None) or "SYP"),
                "receipt_id": rcpt.id if rcpt else None,
                "receipt_serial": rcpt.serial if rcpt else None,
                "receipt_status": rcpt.status if rcpt else None,
                "container_id": r.money_container_id,
                "container_name": r.money_container.name if r.money_container_id else "",
                "fx_syp_per_usd_used": str(r.fx_syp_per_usd_used) if r.fx_syp_per_usd_used else None,
                "journal_entry_id": getattr(r, "journal_entry_id", None),
            }
        )
    collected = c.collected or 0
    remaining = (c.total or 0) - collected
    rem_qs = DebtReminder.objects.filter(direction="creditor", creditor=c).order_by("-set_at")
    current_rem = rem_qs.first()
    history = [{"id": r.id, "set_at": r.set_at, "due_date": r.due_date} for r in rem_qs]
    exposure_receipts = _exposure_receipts(
        c.source_app,
        c.source_model,
        _receipt_source_id(c.source_id, getattr(c, "legacy_source_id", "")),
    )
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
        "customer": {"id": getattr(c, "customer_id", None), "name": c.customer.name if getattr(c, "customer", None) else ""},
        "currency_code": (getattr(c, "currency_code", None) or "SYP"),
        "status": c.status,
        "total": str(c.total),
        "paid_amount": str(collected),   # keep API shape same as list rows
        "remaining": str(remaining),
        "receipts": receipts,
        "exposure_receipts": exposure_receipts,
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
    cursor_raw = request.GET.get("cursor")
    try:
        cursor = int(cursor_raw) if cursor_raw not in (None, "") else None
    except ValueError:
        cursor = None

    try:
        page_size = min(max(int(request.GET.get("page_size", "30")), 1), 100)
    except ValueError:
        page_size = 30

    serial = _int_or_none(request.GET.get("serial"))
    date_from = _date(request.GET.get("date_from"))
    date_to   = _date(request.GET.get("date_to"))

    qs = S.debtors_list(
        q=q,
        status=status,
        cursor=cursor,
        page_size=page_size,
        serial=serial,
        date_from=date_from,
        date_to=date_to,
    )
    items = list(qs)
    nxt = items[-1].id if items else None
    return JsonResponse({"ok": True, "items": [debtor_row(d) for d in items], "next_cursor": nxt})

@require_GET
@role_required(AccountProfile.Role.MANAGER)
def api_creditors_list(request: HttpRequest) -> JsonResponse:
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

    cursor_raw = request.GET.get("cursor")
    try:
        cursor = int(cursor_raw) if cursor_raw not in (None, "") else None
    except ValueError:
        cursor = None

    try:
        page_size = min(max(int(request.GET.get("page_size", "30")), 1), 100)
    except ValueError:
        page_size = 30

    serial = _int_or_none(request.GET.get("serial"))
    date_from = _date(request.GET.get("date_from"))
    date_to   = _date(request.GET.get("date_to"))

    qs = S.creditors_list(
        q=q,
        status=status,
        cursor=cursor,
        page_size=page_size,
        serial=serial,
        date_from=date_from,
        date_to=date_to,
    )
    items = list(qs)
    nxt = items[-1].id if items else None
    return JsonResponse({"ok": True, "items": [creditor_row(c) for c in items], "next_cursor": nxt})


@require_GET
@role_required(AccountProfile.Role.MANAGER)
def api_central_debts_list(request: HttpRequest) -> JsonResponse:
    cursor_raw = request.GET.get("cursor")
    try:
        cursor = int(cursor_raw) if cursor_raw not in (None, "") else None
    except ValueError:
        cursor = None

    try:
        page_size = min(max(int(request.GET.get("page_size", "30")), 1), 100)
    except ValueError:
        page_size = 30

    status = (request.GET.get("status") or "").strip().lower()
    debt_type = (request.GET.get("debt_type") or "").strip().lower()
    cause_type = (request.GET.get("cause_type") or "").strip().lower()
    cause_id = (request.GET.get("cause_id") or "").strip()
    other_party_type = (request.GET.get("other_party_type") or "").strip().lower()
    other_party_name = (request.GET.get("other_party_name") or "").strip()
    other_party_id = (request.GET.get("other_party_id") or "").strip()
    debt_id = (request.GET.get("debt_id") or "").strip()
    date_from = _date(request.GET.get("date_from"))
    date_to = _date(request.GET.get("date_to"))

    qs = S.central_debts_list(
        cursor=cursor,
        page_size=page_size,
        status=status,
        debt_type=debt_type,
        cause_type=cause_type,
        cause_id=cause_id,
        other_party_type=other_party_type,
        other_party_name=other_party_name,
        other_party_id=other_party_id,
        debt_id=debt_id,
        date_from=date_from,
        date_to=date_to,
    )
    items = list(qs)
    nxt = items[-1].id if items else None
    return JsonResponse({"ok": True, "items": [_central_row_with_links(d) for d in items], "next_cursor": nxt})


@require_GET
@role_required(AccountProfile.Role.MANAGER)
def api_other_party_suggest(request: HttpRequest) -> JsonResponse:
    ptype = (request.GET.get("other_party_type") or "").strip().lower()
    q = (request.GET.get("q") or "").strip()
    try:
        limit = min(max(int(request.GET.get("limit", "8")), 1), 20)
    except ValueError:
        limit = 8

    if not q:
        return JsonResponse({"ok": True, "items": []})

    if ptype == OtherPartyType.PROVIDER:
        rows = list(
            Provider.objects.filter(name__icontains=q)
            .order_by("name")
            .values("id", "name")[:limit]
        )
        return JsonResponse({"ok": True, "items": rows})

    if ptype == OtherPartyType.CUSTOMER:
        rows = list(
            CustomerProfile.objects.filter(name__icontains=q)
            .order_by("name")
            .values("id", "name")[:limit]
        )
        return JsonResponse({"ok": True, "items": rows})

    if ptype == OtherPartyType.SYSTEM_USER:
        User = get_user_model()
        rows = []
        for u in User.objects.filter(username__icontains=q).order_by("username")[:limit]:
            rows.append({"id": str(u.id), "name": u.username})
        return JsonResponse({"ok": True, "items": rows})

    if ptype == OtherPartyType.OTHER:
        rows = []
        for value in (
            DebtRecord.objects.filter(other_party_type=OtherPartyType.OTHER, other_party_id__icontains=q)
            .exclude(other_party_id="")
            .values_list("other_party_id", flat=True)
            .distinct()[:limit]
        ):
            rows.append({"id": value, "name": value})
        return JsonResponse({"ok": True, "items": rows})

    return JsonResponse({"ok": True, "items": []})


@require_POST
@role_required(AccountProfile.Role.MANAGER)
def api_central_debt_settle(request: HttpRequest, debt_ref: str) -> JsonResponse:
    ref = (debt_ref or "").strip()
    debt = (
        DebtRecord.objects
        .select_related("provider", "customer")
        .filter(public_id__iexact=ref)
        .first()
    )
    if debt is None:
        return _bad("not found", 404)

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except Exception:
        return _bad("bad json")

    cover_type = (payload.get("cover_type") or "").strip().lower()
    payment_method = (payload.get("payment_method") or "").strip().lower()
    if cover_type not in {"full", "partial"}:
        return _bad("invalid cover type")
    if payment_method not in {"syp_only", "usd_only", "mixed", "separate"}:
        return _bad("invalid payment method")
    if cover_type == "partial" and payment_method == "separate":
        return _bad("separate payment mode is only allowed for full settlement")

    money_container_id = _int_or_none(payload.get("money_container_id"))
    if money_container_id is None:
        return _bad("money_container_id is required")

    try:
        paid_syp = _parse_money_input(payload.get("paid_syp"), "0", field_name="paid_syp")
        paid_usd = _parse_money_input(payload.get("paid_usd"), "0", field_name="paid_usd")
    except ValueError as ve:
        return _bad(str(ve), 400)
    if paid_syp < DEC0 or paid_usd < DEC0:
        return _bad("payment amounts cannot be negative")

    if debt.status == DebtStatus.CLOSED:
        return _bad("debt is already closed")

    try:
        debt, settlement = SV.settle_central_debt(
            actor=request.user,
            debt_id=debt.id,
            full=(cover_type == "full"),
            money_container_id=money_container_id,
            payment_syp=paid_syp,
            payment_usd=paid_usd,
            payment_method=payment_method,
            note=(payload.get("note") or f"Central debt settlement {debt.public_id}"),
        )
    except ValueError as e:
        return _bad(str(e), 400)
    except MoneyContainer.DoesNotExist:
        return _bad("invalid money container", 400)
    except Exception:
        return _bad("server error", 500)

    settlement_ui = _build_settlement_ui(debt=debt)
    return JsonResponse(
        {
            "ok": True,
            "debt": {
                "public_id": debt.public_id,
                "status": debt.status,
                "remaining_syp": _dec_to_str(debt.remaining_syp),
                "remaining_usd": _dec_to_str(debt.remaining_usd),
            },
            "settlement_ui": {
                "default_currency": settlement_ui["default_currency"],
                "currency_options": settlement_ui["currency_options"],
                "total_syp": _dec_to_str(settlement_ui["total_syp"]),
                "total_usd": _dec_to_str(settlement_ui["total_usd"]),
                "fx_syp_per_usd_current": _dec_to_str(settlement_ui["fx_syp_per_usd_current"]),
            },
            "settlement": _central_settlement_row_payload(settlement),
        }
    )


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

    try:
        amount = _parse_money_input(payload.get("amount"), "0", field_name="amount")
        initial_payment = _parse_money_input(
            payload.get("initial_payment"),
            "0",
            field_name="initial_payment",
        )
    except ValueError as ve:
        return _bad(str(ve), 400)
    currency_code = (payload.get("currency_code") or "SYP").strip().upper()
    money_container_id = _int_or_none(payload.get("money_container_id"))
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
            currency_code=currency_code,
            initial_payment=initial_payment if initial_payment and initial_payment > 0 else None,
            money_container_id=money_container_id,
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
        money_container_id = _int_or_none(request.POST.get("money_container_id"))
        currency_code = (request.POST.get("currency_code") or "").strip().upper() or None
        SV.pay_debt(
            actor=request.user,
            entry_id=entry_id,
            full=True,
            money_container_id=money_container_id,
            currency_code=currency_code,
        )
        return JsonResponse({"ok": True})
    except DebtorEntry.DoesNotExist:
        return _bad("not found", 404)
    except ValueError as e:
        return _bad(str(e), 400)

@require_POST
@role_required(AccountProfile.Role.MANAGER)
def api_manual_debt_pay_batch(request: HttpRequest, entry_id: int) -> JsonResponse:
    try:
        amount = _parse_money_input(request.POST.get("amount"), "0", field_name="amount")
    except ValueError:
        return _bad("invalid amount", 400)

    if amount <= 0:
        return _bad("Enter a positive amount.", 400)

    try:
        money_container_id = _int_or_none(request.POST.get("money_container_id"))
        currency_code = (request.POST.get("currency_code") or "").strip().upper() or None
        SV.pay_debt(
            actor=request.user,
            entry_id=entry_id,
            amount=amount,
            full=False,
            money_container_id=money_container_id,
            currency_code=currency_code,
        )
        return JsonResponse({"ok": True})
    except ValueError as e:
        return _bad(str(e), 400)
    except DebtorEntry.DoesNotExist:
        return _bad("not found", 404)
    except Exception:
        return _bad("server error", 500)


@require_POST
@role_required(AccountProfile.Role.MANAGER)
def api_manual_creditor_collect_full(request: HttpRequest, entry_id: int) -> JsonResponse:
    try:
        money_container_id = _int_or_none(request.POST.get("money_container_id"))
        currency_code = (request.POST.get("currency_code") or "").strip().upper() or None
        SV.collect_debt(
            actor=request.user,
            entry_id=entry_id,
            full=True,
            money_container_id=money_container_id,
            currency_code=currency_code,
        )
        return JsonResponse({"ok": True})
    except CreditorEntry.DoesNotExist:
        return _bad("not found", 404)
    except ValueError as e:
        return _bad(str(e), 400)

@require_POST
@role_required(AccountProfile.Role.MANAGER)
def api_manual_creditor_collect_batch(request: HttpRequest, entry_id: int) -> JsonResponse:
    try:
        amount = _parse_money_input(request.POST.get("amount"), "0", field_name="amount")
    except ValueError:
        return _bad("invalid amount")
    if amount <= 0:
        return _bad("Enter a positive amount." , 400)
    try:
        money_container_id = _int_or_none(request.POST.get("money_container_id"))
        currency_code = (request.POST.get("currency_code") or "").strip().upper() or None
        SV.collect_debt(
            actor=request.user,
            entry_id=entry_id,
            amount=amount,
            full=False,
            money_container_id=money_container_id,
            currency_code=currency_code,
        )
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
        money_container_id = _int_or_none(request.POST.get("money_container_id"))
        currency_code = (request.POST.get("currency_code") or "").strip().upper() or None
        if direction == "debtor":
            SV.pay_debt(
                actor=request.user,
                entry_id=entry_id,
                full=True,
                money_container_id=money_container_id,
                currency_code=currency_code,
            )
        elif direction == "creditor":
            SV.collect_debt(
                actor=request.user,
                entry_id=entry_id,
                full=True,
                money_container_id=money_container_id,
                currency_code=currency_code,
            )
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
        amount = _parse_money_input(request.POST.get("amount"), "0", field_name="amount")
    except ValueError:
        return _bad("invalid amount", 400)
    if amount <= 0:
        return _bad("Enter a positive amount.", 400)
    try:
        money_container_id = _int_or_none(request.POST.get("money_container_id"))
        currency_code = (request.POST.get("currency_code") or "").strip().upper() or None
        if direction == "debtor":
            SV.pay_debt(
                actor=request.user,
                entry_id=entry_id,
                amount=amount,
                full=False,
                money_container_id=money_container_id,
                currency_code=currency_code,
            )
        elif direction == "creditor":
            SV.collect_debt(
                actor=request.user,
                entry_id=entry_id,
                amount=amount,
                full=False,
                money_container_id=money_container_id,
                currency_code=currency_code,
            )
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
