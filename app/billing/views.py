# app/billing/views.py
from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation

from django.http import JsonResponse, HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect, render, get_object_or_404
from django.views.decorators.http import require_GET, require_POST
from django.utils import timezone
from django.db.models import Q  # needed for products search filters

from accounts.models import AccountProfile
from catalog.views import role_required
from catalog.models import Product
from billing.models import Provider, Bill

from . import selectors as S
from . import services as SV
from .serializers import provider_row, bill_row


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


# ---------- Helpers ----------

def _dec(val, default: str = "0") -> Decimal:
    try:
        return Decimal(str((val if val is not None else default)).replace(",", "."))
    except (InvalidOperation, ValueError):
        return Decimal(default)


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
    # uses your ActiveProviderManager
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
    has_debts = Bill.objects.filter(
        provider=p, status__in=[Bill.Status.UNPAID, Bill.Status.PARTIAL]
    ).exists()
    if has_debts:
        return _bad("cannot delete: unpaid debts exist")
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


@require_GET
@role_required(AccountProfile.Role.MANAGER)
def api_bill_next_serial(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"ok": True, "next_serial": S.next_bill_serial()})


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

    serial_raw = payload.get("serial")
    serial = None
    if serial_raw not in (None, ""):
        try:
            serial = int(serial_raw)
            if serial <= 0:
                return _bad("serial must be a positive number")
        except Exception:
            return _bad("serial must be numbers only")
        if Bill.objects.filter(serial=serial).exists():
            return _bad("serial already exists", 409)

    pay = payload.get("pay") or {}
    status = (pay.get("status") or "unpaid").lower()
    if status not in {"paid", "unpaid", "partial"}:
        status = "unpaid"
    paid_amount = _dec(pay.get("paid_amount"), "0")

    update_defaults = bool(payload.get("update_product_defaults") or False)

    try:
        bill = SV.create_bill(
            provider_id=int(pid),
            serial=serial,
            status=status,
            paid_amount=paid_amount,
            items=items,
            update_product_defaults=update_defaults,
        )
        return JsonResponse({"ok": True, "bill": bill_row(bill)})
    except Exception:
        return _bad("save failed", 500)


@require_GET
@role_required(AccountProfile.Role.MANAGER)
def api_bills_list(request: HttpRequest) -> JsonResponse:
    q = (request.GET.get("q") or "").strip()
    serial = request.GET.get("serial")
    bill_id = request.GET.get("id")
    date_from = (request.GET.get("date_from") or "").strip()
    date_to = (request.GET.get("date_to") or "").strip()
    status = (request.GET.get("status") or "").lower()
    cursor = request.GET.get("cursor")

    try:
        page_size = min(max(int(request.GET.get("page_size", "30")), 1), 100)
    except ValueError:
        page_size = 30

    qs = S.bills_list_filters(S.bills_base(), q, serial, bill_id, status, date_from, date_to, cursor, page_size)
    qs = qs.order_by("-id")[:page_size]
    items = list(qs)
    nxt = items[-1].id if items else None
    return JsonResponse({"ok": True, "items": [bill_row(b) for b in items], "next_cursor": nxt})


@require_GET
@role_required(AccountProfile.Role.MANAGER)
def api_debts_list(request: HttpRequest) -> JsonResponse:
    q = (request.GET.get("q") or "").strip()
    serial = request.GET.get("serial")
    bill_id = request.GET.get("id")
    date_from = (request.GET.get("date_from") or "").strip()
    date_to = (request.GET.get("date_to") or "").strip()
    status = (request.GET.get("status") or "").lower()
    cursor = request.GET.get("cursor")

    try:
        page_size = min(max(int(request.GET.get("page_size", "30")), 1), 100)
    except ValueError:
        page_size = 30

    qs = S.debts_list(q, serial, bill_id, status, date_from, date_to, cursor, page_size)
    items = list(qs)
    nxt = items[-1].id if items else None
    return JsonResponse({"ok": True, "items": [bill_row(b) for b in items], "next_cursor": nxt})


@require_POST
@role_required(AccountProfile.Role.MANAGER)
def api_bill_delete(request: HttpRequest, bill_id: int) -> JsonResponse:
    try:
        SV.delete_bill(bill_id)
        return JsonResponse({"ok": True})
    except Bill.DoesNotExist:
        return _bad("not found", 404)
    except Exception:
        return _bad("delete failed", 500)


# ---------- Payments ----------

@require_POST
@role_required(AccountProfile.Role.MANAGER)
def pay_debt_full(request: HttpRequest, bill_id: int) -> JsonResponse:
    try:
        SV.pay_full(bill_id)
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
        bill = SV.pay_partial(bill_id, amount)
        return JsonResponse({"ok": True, "remaining": str(bill.remaining)})
    except ValueError as ve:
        return _bad(str(ve))
    except Bill.DoesNotExist:
        return _bad("not found", 404)
