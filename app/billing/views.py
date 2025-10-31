# app/billing/views.py
from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation

from django.http import JsonResponse, HttpRequest, HttpResponse
from django.shortcuts import redirect, render, get_object_or_404
from django.views.decorators.http import require_GET, require_POST
from django.utils import timezone
from django.db.models import Q  # needed for products search filters

from accounts.models import AccountProfile
from catalog.views import role_required
from catalog.models import Product
from billing.models import (
    Provider, Bill, ProviderReturn,
    DebtorEntry, CreditorEntry, PartyType
    
)

from . import selectors as S
from . import services as SV
from .serializers import provider_row, bill_row, debtor_row, creditor_row, return_row

import logging
logger = logging.getLogger(__name__)

from datetime import date


# ---------- Page views ----------

@role_required(AccountProfile.Role.MANAGER)
def billing_home(request: HttpRequest) -> HttpResponse:
    return redirect("billing_list")


@role_required(AccountProfile.Role.MANAGER)
def bills_list(request: HttpRequest) -> HttpResponse:
    return render(request, "billing/bills_list.html")


@role_required(AccountProfile.Role.MANAGER)
def add_bill(request: HttpRequest) -> HttpResponse:
    return render(request, "billing/add_bill.html")


@role_required(AccountProfile.Role.MANAGER)
def debts_list(request: HttpRequest) -> HttpResponse:
    return render(request, "billing/debts_list.html")


@role_required(AccountProfile.Role.MANAGER)
def providers_list(request: HttpRequest) -> HttpResponse:
    return render(request, "billing/providers_list.html")


@role_required(AccountProfile.Role.MANAGER)
def debts_page(request: HttpRequest) -> HttpResponse:
    return render(request, "billing/debts_list.html")

@role_required(AccountProfile.Role.MANAGER)
def creditors_page(request: HttpRequest) -> HttpResponse:
    # قائمة الدائن (providers owe store)
    return render(request, "billing/creditors_list.html")


@role_required(AccountProfile.Role.MANAGER)
def return_view(request: HttpRequest, ret_id: int) -> HttpResponse:
    """Read-only details page for a ProviderReturn."""
    pret = (
        ProviderReturn.objects
        .select_related("provider")
        .prefetch_related("items", "items__product")
        .get(pk=ret_id)
    )

    created_status = pret.initial_status
    created_paid = pret.initial_paid

    ctx = {
        "pret": pret,
        "created_status": created_status,
        "created_paid": created_paid,
        "has_credit_now": pret.remaining > 0,
    }
    return render(request, "billing/return_view.html", ctx)

# ---------- Helpers ----------

def _dec(val, default: str = "0") -> Decimal:
    try:
        return Decimal(str((val if val is not None else default)).replace(",", "."))
    except (InvalidOperation, ValueError):
        return Decimal(default)


def _date(val) -> "date | None":
    s = (val or "").strip()
    if not s:
        return None
    try:
        # accept YYYY-MM-DD
        return date.fromisoformat(s[:10])
    except Exception:
        return None



def _bad(msg: str, status: int = 400) -> JsonResponse:
    return JsonResponse({"ok": False, "error": msg}, status=status)


# ---------- Providers APIs ----------

@require_GET
@role_required(AccountProfile.Role.MANAGER)
def api_providers_list(request: HttpRequest) -> JsonResponse:
    q = (request.GET.get("q") or "").strip()
    cursor_raw = request.GET.get("cursor")
    try:
        cursor = int(cursor_raw) if cursor_raw not in (None, "") else None
    except ValueError:
        cursor = None
    try:
        page_size = min(max(int(request.GET.get("page_size", "30")), 1), 100)
    except ValueError:
        page_size = 30
    include_all = (request.GET.get("include_all") == "1")

    qs = S.providers_with_stats(q, include_all, cursor, page_size)
    items = list(qs)
    nxt = items[-1].id if items else None
    return JsonResponse({"ok": True, "items": [provider_row(p) for p in items], "next_cursor": nxt})


@require_POST
@role_required(AccountProfile.Role.MANAGER)
def api_provider_create(request: HttpRequest) -> JsonResponse:
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except Exception:
        return _bad("bad json")
    name = (payload.get("name") or "").strip()
    if not name:
        return _bad("name required")
    # uses ActiveProviderManager
    if Provider.active.filter(name__iexact=name).exists():
        return _bad("الاسم موجود مسبقا", 409)
    p = Provider.objects.create(
        name=name,
        phone=(payload.get("phone") or "").strip(),
        notes=(payload.get("notes") or "").strip(),
        is_active=True,
    )
    return JsonResponse({"ok": True, "provider": {"id": p.id, "name": p.name}})


@require_POST
@role_required(AccountProfile.Role.MANAGER)
def api_provider_delete(request: HttpRequest, pid: int) -> JsonResponse:
    p = get_object_or_404(Provider, pk=pid)

    # Block deletion if there are any OPEN debtor or creditor entries
    has_open_payables = DebtorEntry.objects.filter(provider=p, status=DebtorEntry.Status.OPEN).exists()
    has_open_receivables = CreditorEntry.objects.filter(provider=p, status=CreditorEntry.Status.OPEN).exists()
    if has_open_payables or has_open_receivables:
        return _bad("cannot delete: outstanding balances exist")

    if not p.is_active:
        return JsonResponse({"ok": True})
    p.is_active = False
    p.deleted_at = timezone.now()
    p.save(update_fields=["is_active", "deleted_at"])
    return JsonResponse({"ok": True})


@require_GET
@role_required(AccountProfile.Role.MANAGER)
def api_providers_ac(request: HttpRequest) -> JsonResponse:
    q = (request.GET.get("q") or "").strip()
    return JsonResponse({"ok": True, "items": list(S.providers_ac(q))})



@require_POST
@role_required(AccountProfile.Role.MANAGER)
def api_manual_debt_save(request: HttpRequest) -> JsonResponse:
    """
    POST JSON:
    {
      "direction": "debtor" | "creditor",
      "party_type": "provider" | "customer" | "worker",   # UI: only provider enabled now
      "party": {"id": 123, "name": "..."},                # for provider: id required
      "amount": "123.456",
      "due_date": "2025-11-10"                            # optional
    }
    Returns a row compatible with debtor/creditor list item.
    """
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except Exception:
        return _bad("bad json")

    direction = (payload.get("direction") or "").strip().lower()
    party_type = (payload.get("party_type") or PartyType.PROVIDER).strip().lower()
    party = payload.get("party") or {}
    party_id = party.get("id")
    party_name = (party.get("name") or "").strip()
    amount = _dec(payload.get("amount"), "0")
    due_date = _date(payload.get("due_date"))

    # only provider allowed for now (enforced both server & UI)
    if party_type != PartyType.PROVIDER:
        return _bad("party_type not supported yet", 422)

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
    except Exception as e:
        logger.exception("api_manual_debt_save failed")
        return _bad("save failed", 500)

    # serialize according to list kind
    if direction == "debtor":
        return JsonResponse({"ok": True, "item": debtor_row(entry)})
    else:
        return JsonResponse({"ok": True, "item": creditor_row(entry)})


# ---------- Products search (for Add Bill) ----------

@require_GET
@role_required(AccountProfile.Role.MANAGER)
def api_products_search(request: HttpRequest) -> JsonResponse:
    """
    GET /manager/billing/api/products/search/?q=...&mode=name|code|barcode|id
    """
    from django.core.exceptions import FieldError

    q = (request.GET.get("q") or "").strip()
    mode = (request.GET.get("mode") or "name").lower().strip()
    if not q:
        return JsonResponse({"ok": True, "items": []})

    qs = (
        Product.objects
        .select_related("set", "set__collection")
        .prefetch_related("barcodes", "unit_ids")
        .all()
    )

    def filter_barcode(qs_, v):
        try:
            return qs_.filter(barcodes__code__iexact=v)
        except FieldError:
            return qs_.filter(barcodes__barcode__iexact=v)

    if mode == "id":
        qs = qs.filter(unit_ids__value__iexact=q)
    elif mode == "barcode":
        qs = filter_barcode(qs, q)
    elif mode == "code":
        qs = qs.filter(product_number__icontains=q)
    else:
        qs = qs.filter(Q(name__icontains=q) | Q(product_number__icontains=q))

    qs = qs.order_by("name")[:20]

    items = []
    for p in qs:
        col = getattr(getattr(p, "set", None), "collection", None)
        setobj = getattr(p, "set", None)
        matched_unit = None

        if mode == "id":
            for uid in getattr(p, "unit_ids", []).all():
                if (uid.value or "").lower() == q.lower():
                    matched_unit = int(uid.unit_index)
                    break
        elif mode == "barcode":
            for b in getattr(p, "barcodes", []).all():
                val = getattr(b, "barcode", None) or getattr(b, "code", None)
                if (val or "").lower() == q.lower():
                    matched_unit = int(b.unit_index)
                    break

        items.append({
            "id": p.id,
            "name": p.name,
            "code": getattr(p, "product_number", "") or "",
            "col_name": getattr(col, "name", "") or "",
            "col_code": getattr(col, "code", "") or "",
            "set_name": getattr(setobj, "name", "") or "",
            "set_code": getattr(setobj, "code", "") or "",
            "unit_primary_label": p.get_unit_primary_display() or "الوحدة الأولى",
            "unit_secondary_label": (p.get_unit_secondary_display() if p.unit_secondary else "") or "الوحدة الثانية",
            "unit_secondary": getattr(p, "unit_secondary", "") or "",
            "conversion_factor": getattr(p, "conversion_factor", 0) or 0,
            "price": getattr(p, "price", None),
            "cost": getattr(p, "cost", None),
            "matched_unit": matched_unit,
        })

    return JsonResponse({"ok": True, "items": items})


# ---------- Bills APIs ----------
# - Bills: next serial (preview) -
from django.db.models import Max

@require_GET
@role_required(AccountProfile.Role.MANAGER)
def api_bill_next_serial(request: HttpRequest) -> JsonResponse:
    from django.db.models import Max
    m_bill = Bill.objects.aggregate(m=Max("serial"))["m"] or 0
    m_debt = DebtorEntry.objects.aggregate(m=Max("doc_serial"))["m"] or 0
    return JsonResponse({"ok": True, "next_serial": int(max(int(m_bill or 0), int(m_debt or 0))) + 1})

@require_POST
@role_required(AccountProfile.Role.MANAGER)
def api_bill_save(request: HttpRequest) -> JsonResponse:
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except Exception:
        return _bad("bad json")

    items = payload.get("items") or []
    if not items:
        return _bad("no items")

    provider = payload.get("provider") or {}
    pid = provider.get("id")
    if not pid:
        return _bad("provider must be selected from list")

    pay = payload.get("pay") or {}
    status = (pay.get("status") or "unpaid").lower()
    if status not in {"paid", "unpaid", "partial"}:
        status = "unpaid"
    paid_amount = _dec(pay.get("paid_amount"), "0")

    update_defaults = bool(payload.get("update_product_defaults") or False)

    try:
        bill = SV.create_bill(
            actor=request.user,
            provider_id=int(pid),
            status=status,
            paid_amount=paid_amount,
            items=items,
            update_product_defaults=update_defaults,
        )
        return JsonResponse({"ok": True, "bill": bill_row(bill)})
    except Exception as e:
        logger.exception("api_bill_save failed")
        return _bad("save failed", 500)


@require_GET
@role_required(AccountProfile.Role.MANAGER)
def api_bills_list(request: HttpRequest) -> JsonResponse:
    q = (request.GET.get("q") or "").strip()
    serial = request.GET.get("serial")
    date_from = (request.GET.get("date_from") or "").strip()
    date_to = (request.GET.get("date_to") or "").strip()
    status = (request.GET.get("status") or "").lower()  # NOTE: evaluated at Python-level via properties
    cursor = request.GET.get("cursor")

    try:
        page_size = min(max(int(request.GET.get("page_size", "30")), 1), 100)
    except ValueError:
        page_size = 30

    qs = S.bills_list_filters(S.bills_base(), q, serial, status, date_from, date_to, cursor, page_size)
    qs = qs.order_by("-id")[:page_size]
    items = list(qs)

    # Optional status filter at Python-level (since status is now a property)
    if status in {"paid", "unpaid", "partial"}:
        items = [b for b in items if (b.status or "").lower() == status]

    nxt = items[-1].id if items else None
    return JsonResponse({"ok": True, "items": [bill_row(b) for b in items], "next_cursor": nxt})


@require_GET
@role_required(AccountProfile.Role.MANAGER)
def api_debts_list(request: HttpRequest) -> JsonResponse:
    """
    Debtor (payables) list backed by DebtorEntry.
    Accepts status as: 'open'/'closed' OR 'unpaid'/'partial'/'paid'.
    """
    q = (request.GET.get("q") or "").strip()

    # Normalize UI status -> sub-ledger status
    raw_status = (request.GET.get("status") or "").lower().strip()
    if raw_status in {"unpaid", "partial"}:
        status = "open"
    elif raw_status == "paid":
        status = "closed"
    elif raw_status in {"open", "closed", ""}:
        status = raw_status
    else:
        status = ""  # unknown -> no filter

    cursor = request.GET.get("cursor")
    try:
        page_size = min(max(int(request.GET.get("page_size", "30")), 1), 100)
    except ValueError:
        page_size = 30

    qs = S.debtors_list(q=q, status=status, cursor=cursor, page_size=page_size)
    items = list(qs)
    nxt = items[-1].id if items else None

    return JsonResponse(
        {"ok": True, "items": [debtor_row(d) for d in items], "next_cursor": nxt}
    )



@require_POST
@role_required(AccountProfile.Role.MANAGER)
def api_bill_delete(request: HttpRequest, bill_id: int) -> JsonResponse:
    try:
        SV.delete_bill(actor=request.user, bill_id=bill_id)
        return JsonResponse({"ok": True})
    except Bill.DoesNotExist:
        return _bad("not found", 404)
    except ValueError as ve:
        return _bad(str(ve), 409)
    except Exception:
        return _bad("delete failed", 500)


@role_required(AccountProfile.Role.MANAGER)
def bill_view(request: HttpRequest, bill_id: int) -> HttpResponse:
    bill = (
        Bill.objects
        .select_related("provider")
        .prefetch_related("items", "items__product")
        .get(pk=bill_id)
    )

    # With the new model, "creation-time" snapshot lives only in GL / subledger;
    # we show current derived status & paid from DebtorEntry.
    created_status = bill.status
    created_paid = bill.paid_amount

    ctx = {
        "bill": bill,
        "created_status": created_status,
        "created_paid": created_paid,
        "has_debt_now": bill.remaining > 0,
    }
    return render(request, "billing/bill_view.html", ctx)


# ---------- Payments (payables) ----------

@require_POST
@role_required(AccountProfile.Role.MANAGER)
def pay_debt_full(request: HttpRequest, bill_id: int) -> JsonResponse:
    try:
        SV.pay_full(actor=request.user, bill_id=bill_id)
        return JsonResponse({"ok": True, "remaining": "0"})
    except Bill.DoesNotExist:
        return _bad("not found", 404)


@require_POST
@role_required(AccountProfile.Role.MANAGER)
def pay_debt_batch(request: HttpRequest, bill_id: int) -> JsonResponse:
    amount_raw = (request.POST.get("amount") or "").strip()
    try:
        amount = Decimal(amount_raw)
    except Exception:
        return _bad("Enter a positive amount.")
    if amount <= 0:
        return _bad("Enter a positive amount.")
    try:
        bill = SV.pay_partial(actor=request.user, bill_id=bill_id, amount=amount)
        return JsonResponse({"ok": True, "remaining": str(bill.remaining)})
    except ValueError as ve:
        return _bad(str(ve))
    except Bill.DoesNotExist:
        return _bad("not found", 404)


# ----------- Provider Returns (receivables) ---------------

@role_required(AccountProfile.Role.MANAGER)
def providers_returns_page(request: HttpRequest) -> HttpResponse:
    return render(request, "billing/providers_returns.html")


@require_GET
@role_required(AccountProfile.Role.MANAGER)
def api_return_next_serial(request: HttpRequest) -> JsonResponse:
    from django.db.models import Max
    m_ret  = ProviderReturn.objects.aggregate(m=Max("serial"))["m"] or 0
    m_cred = CreditorEntry.objects.aggregate(m=Max("doc_serial"))["m"] or 0
    return JsonResponse({"ok": True, "next_serial": int(max(int(m_ret or 0), int(m_cred or 0))) + 1})



@require_POST
@role_required(AccountProfile.Role.MANAGER)
def api_return_save(request: HttpRequest) -> JsonResponse:
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except Exception:
        return _bad("bad json")

    items = payload.get("items") or []
    if not items:
        return _bad("no items")

    provider = payload.get("provider") or {}
    pid = provider.get("id")
    if not pid:
        return _bad("provider must be selected from list")

    pay = payload.get("pay") or {}
    status = (pay.get("status") or "unpaid").lower()
    if status not in {"paid", "unpaid", "partial"}:
        status = "unpaid"
    paid_amount = _dec(pay.get("paid_amount"), "0")

    try:
        pret = SV.create_return(
            actor=request.user,
            provider_id=int(pid),
            status=status,
            paid_amount=paid_amount,
            items=items,
        )
        return JsonResponse({"ok": True, "ret": return_row(pret)})
    except ValueError as ve:
        return _bad(str(ve))
    except Product.DoesNotExist:
        return _bad("product not found", 404)
    except Exception as e:
        logger.exception("api_return_save failed")
        return _bad(f"save failed: {e.__class__.__name__}: {e}", 500)


@role_required(AccountProfile.Role.MANAGER)
def providers_returns_list_page(request: HttpRequest) -> HttpResponse:
    return render(request, "billing/providers_returns_list.html")


@require_GET
@role_required(AccountProfile.Role.MANAGER)
def api_returns_list(request: HttpRequest) -> JsonResponse:
    q = (request.GET.get("q") or "").strip()
    serial = request.GET.get("serial")
    rid = request.GET.get("id")
    date_from = (request.GET.get("date_from") or "").strip()
    date_to = (request.GET.get("date_to") or "").strip()
    status = (request.GET.get("status") or "").lower()  # property-based
    cursor = request.GET.get("cursor")
    try:
        page_size = min(max(int(request.GET.get("page_size", "30")), 1), 100)
    except ValueError:
        page_size = 30

    qs = S.returns_list_filters(q, serial, rid, status, date_from, date_to, cursor, page_size)
    items = list(qs)

    # Python-level status filter (since status is property now)
    if status in {"paid", "partial", "unpaid"}:
        items = [r for r in items if (r.status or "").lower() == status]

    nxt = items[-1].id if items else None
    return JsonResponse({"ok": True, "items": [return_row(r) for r in items], "next_cursor": nxt})


# Payments (collections) for provider debts-to-store:

@require_POST
@role_required(AccountProfile.Role.MANAGER)
def collect_return_full(request: HttpRequest, ret_id: int) -> JsonResponse:
    try:
        SV.collect_full(actor=request.user, return_id=ret_id)
        return JsonResponse({"ok": True, "remaining": "0"})
    except ProviderReturn.DoesNotExist:
        return _bad("not found", 404)



@require_POST
@role_required(AccountProfile.Role.MANAGER)
def api_manual_debt_pay_full(request: HttpRequest, entry_id: int) -> JsonResponse:
    try:
        SV.pay_manual_debt_full(actor=request.user, entry_id=entry_id)
        return JsonResponse({"ok": True})
    except DebtorEntry.DoesNotExist:
        return _bad("not found", 404)
    except Exception as e:
        logger.exception("manual_debt_pay_full failed")
        return _bad(str(e), 500)


@require_POST
@role_required(AccountProfile.Role.MANAGER)
def api_manual_debt_pay_batch(request: HttpRequest, entry_id: int) -> JsonResponse:
    amount_raw = (request.POST.get("amount") or "").strip()
    try:
        amount = Decimal(amount_raw)
    except Exception:
        return _bad("invalid amount")
    try:
        SV.pay_manual_debt_partial(actor=request.user, entry_id=entry_id, amount=amount)
        return JsonResponse({"ok": True})
    except Exception as e:
        logger.exception("manual_debt_pay_batch failed")
        return _bad(str(e), 500)


@require_POST
@role_required(AccountProfile.Role.MANAGER)
def api_manual_creditor_collect_full(request: HttpRequest, entry_id: int) -> JsonResponse:
    try:
        SV.collect_manual_debt_full(actor=request.user, entry_id=entry_id)
        return JsonResponse({"ok": True})
    except CreditorEntry.DoesNotExist:
        return _bad("not found", 404)
    except Exception as e:
        logger.exception("manual_creditor_collect_full failed")
        return _bad(str(e), 500)

@require_POST
@role_required(AccountProfile.Role.MANAGER)
def api_manual_creditor_collect_batch(request: HttpRequest, entry_id: int) -> JsonResponse:
    amount_raw = (request.POST.get("amount") or "").strip()
    try:
        amount = Decimal(amount_raw)
    except Exception:
        return _bad("invalid amount")
    if amount <= 0:
        return _bad("Enter a positive amount.")

    try:
        SV.collect_manual_debt_partial(actor=request.user, entry_id=entry_id, amount=amount)
        return JsonResponse({"ok": True})
    except CreditorEntry.DoesNotExist:
        return _bad("not found", 404)
    except ValueError as ve:
        return _bad(str(ve))
    except Exception as e:
        logger.exception("manual_creditor_collect_batch failed")
        return _bad(str(e), 500)

@require_POST
@role_required(AccountProfile.Role.MANAGER)
def collect_return_batch(request: HttpRequest, ret_id: int) -> JsonResponse:
    amount_raw = (request.POST.get("amount") or "").strip()
    try:
        amount = Decimal(amount_raw)
    except Exception:
        return _bad("Enter a positive amount.")
    if amount <= 0:
        return _bad("Enter a positive amount.")
    try:
        pret = SV.collect_partial(actor=request.user, return_id=ret_id, amount=amount)
        return JsonResponse({"ok": True, "remaining": str(pret.remaining)})
    except ValueError as ve:
        return _bad(str(ve))
    except ProviderReturn.DoesNotExist:
        return _bad("not found", 404)


# --- page: add debt ---
@role_required(AccountProfile.Role.MANAGER)
def add_debt(request: HttpRequest) -> HttpResponse:
    return render(request, "billing/add_debt.html")

# --- API: save manual debt ---
@require_POST
@role_required(AccountProfile.Role.MANAGER)
def api_debt_save(request: HttpRequest) -> JsonResponse:
    import json
    from decimal import Decimal
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except Exception:
        return _bad("bad json")

    direction   = (payload.get("direction") or "").strip().lower()   # debtor|creditor
    party_type  = (payload.get("party_type") or "").strip().lower()  # provider|customer|worker
    provider_id = payload.get("provider_id")
    party_name  = (payload.get("party_name") or "").strip()
    amount_raw  = payload.get("amount")
    due_date    = payload.get("due_date") or None

    try:
        amount = Decimal(str(amount_raw or "0"))
    except Exception:
        return _bad("invalid amount")

    # for now only provider is allowed via UI; keep backend tolerant:
    if party_type != "provider":
        return _bad("unsupported party type for now")

    if not provider_id:
        return _bad("provider must be selected from list")

    # Parse date (optional)
    from datetime import date
    d_due = None
    if due_date:
        try:
            # YYYY-MM-DD
            y, m, d = [int(x) for x in str(due_date).split("-")]
            d_due = date(y, m, d)
        except Exception:
            return _bad("bad due date")

    try:
        entry = SV.create_manual_debt(
            actor=request.user,
            direction=direction,
            party_type=party_type,
            provider_id=int(provider_id),
            party_name=party_name,
            amount=amount,
            due_date=d_due,
        )
        return JsonResponse({"ok": True, "id": entry.id})
    except ValueError as ve:
        return _bad(str(ve))
    except Exception as e:
        logger.exception("api_debt_save failed")
        return _bad("save failed", 500)



# ----------- Creditors (receivables) list API ---------------

@require_GET
@role_required(AccountProfile.Role.MANAGER)
def api_creditors_list(request: HttpRequest) -> JsonResponse:
    """List receivables from CreditorEntry (OPEN by default)."""
    q = (request.GET.get("q") or "").strip()
    status = (request.GET.get("status") or "").lower()  # "open"/"closed"/""
    cursor = request.GET.get("cursor")
    try:
        page_size = min(max(int(request.GET.get("page_size", "30")), 1), 100)
    except ValueError:
        page_size = 30

    qs = S.creditors_list(q=q, status=status, cursor=cursor, page_size=page_size)
    items = list(qs)
    nxt = items[-1].id if items else None
    return JsonResponse({"ok": True, "items": [creditor_row(c) for c in items], "next_cursor": nxt})
