# app/billing/views.py
from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.db import transaction
from django.db.models.functions import Lower
from django.http import JsonResponse, HttpRequest, HttpResponse , HttpResponseBadRequest
from django.shortcuts import redirect, render, get_object_or_404
from django.views.decorators.http import require_GET, require_POST
from django.core.exceptions import FieldError

from accounts.models import AccountProfile
from catalog.views import role_required
from catalog.models import Product
from billing.models import Provider, Bill, BillItem

from django.db.models import (
    Sum, F, Q, Value,
    DecimalField, IntegerField, Case, When, ExpressionWrapper
)
from django.db.models.functions import Coalesce


from django.utils import timezone


# ---------- Pages ----------

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
    # Renders the global debts page (filters + list rendered by JS via API)
    return render(request, "billing/debts_list.html")


# ---------- Utilities ----------

_DEC0 = Decimal("0")
_DEC3 = Decimal("0.001")
_DEC4 = Decimal("0.0001")

def _to_decimal(val, default: str = "0") -> Decimal:
    """
    Convert inputs to Decimal. Accepts "12,34" and "12.34".
    Returns Decimal(default) if invalid.
    """
    if val is None:
        val = default
    if isinstance(val, (int, float, Decimal)):
        return Decimal(str(val))
    s = (str(val).strip().replace(",", ".") or default)
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return Decimal(default)

def _q3(x: Decimal) -> Decimal:
    # Quantize to 3 decimals (qty/line totals)
    return (x or _DEC0).quantize(_DEC3, rounding=ROUND_HALF_UP)

def _q4(x: Decimal) -> Decimal:
    # Quantize to 4 decimals (unit costs/prices)
    return (x or _DEC0).quantize(_DEC4, rounding=ROUND_HALF_UP)

def _err(message: str, status: int = 400) -> JsonResponse:
    return JsonResponse({"ok": False, "error": message}, status=status)


# ---------- APIs (providers) ----------

# ===========================================
@require_GET
@role_required(AccountProfile.Role.MANAGER)
def api_providers_list(request: HttpRequest) -> JsonResponse:
    """
    GET:
      q               : name icontains
      page_size       : default 30 (max 100)
      cursor          : id__lt keyset
      include_all=1   : if provided, show all providers (except deleted)
                        otherwise show only providers with activity (>=1 bill OR >=1 unpaid/partial)
    Always excludes soft-deleted providers (is_active=False).
    """
    # Base: exclude deleted
    qs = Provider.objects.filter(is_active=True).order_by("-id")

    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(name__icontains=q)

    cursor = request.GET.get("cursor")
    if cursor:
        try:
            qs = qs.filter(id__lt=int(cursor))
        except ValueError:
            pass

    try:
        page_size = min(max(int(request.GET.get("page_size", "30")), 1), 100)
    except ValueError:
        page_size = 30

    include_all = (request.GET.get("include_all") == "1")

    # Annotate activity counts and debt
    debt_expr = Coalesce(
        Sum(
            (F("bills__total") - F("bills__paid_amount")),
            filter=Q(bills__status__in=[Bill.Status.UNPAID, Bill.Status.PARTIAL]),
            output_field=DecimalField(max_digits=14, decimal_places=3),
        ),
        Value(0, output_field=DecimalField(max_digits=14, decimal_places=3))
    )
    qs = qs.annotate(
        bills_count=Coalesce(Sum(Value(1), filter=Q(bills__id__isnull=False)), Value(0)),
        unpaid_bills_count=Coalesce(Sum(Value(1), filter=Q(bills__status__in=[Bill.Status.UNPAID, Bill.Status.PARTIAL])), Value(0)),
        total_debt=debt_expr,
    ).only("id", "name", "phone", "is_active")

    if not include_all:
        # Show only providers with activity
        qs = qs.filter(Q(bills_count__gt=0) | Q(unpaid_bills_count__gt=0))

    items = list(qs[:page_size])

    def row(p: Provider):
        return {
            "id": p.id,
            "name": p.name,
            "phone": p.phone or "",
            "is_active": True,  # by construction
            "bills_count": int(p.bills_count or 0),
            "unpaid_bills_count": int(p.unpaid_bills_count or 0),
            "total_debt": str(getattr(p, "total_debt", 0) or 0),
        }

    next_cursor = items[-1].id if items else None
    return JsonResponse({"ok": True, "items": [row(p) for p in items], "next_cursor": next_cursor})



@require_POST
@role_required(AccountProfile.Role.MANAGER)
def api_provider_create(request: HttpRequest) -> JsonResponse:
    """
    Body: { "name": "...", "phone": "", "notes": "" }
    Name must be unique among active providers (case-insensitive).
    """
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except Exception:
        return JsonResponse({"ok": False, "error": "bad json"}, status=400)

    name = (payload.get("name") or "").strip()
    phone = (payload.get("phone") or "").strip()
    notes = (payload.get("notes") or "").strip()

    if not name:
        return JsonResponse({"ok": False, "error": "name required"}, status=400)

    # Enforce uniqueness among active
    if Provider.objects.filter(is_active=True, name__iexact=name).exists():
        return JsonResponse({"ok": False, "error": "الاسم موجود مسبقا"}, status=409)

    p = Provider.objects.create(name=name, phone=phone, notes=notes, is_active=True)
    return JsonResponse({"ok": True, "provider": {"id": p.id, "name": p.name}})


@require_POST
@role_required(AccountProfile.Role.MANAGER)
def api_provider_delete(request: HttpRequest, pid: int) -> JsonResponse:
    """
    Archive provider (soft delete) if there are no unpaid/partial bills.
    Bills remain intact and keep their provider FK.
    Archived providers are excluded from autocomplete and lists by default.
    """
    try:
        p = Provider.objects.get(pk=pid)
    except Provider.DoesNotExist:
        return JsonResponse({"ok": False, "error": "not found"}, status=404)

    # Disallow if any debts remain (unpaid/partial bills)
    has_debts = Bill.objects.filter(provider=p, status__in=[Bill.Status.UNPAID, Bill.Status.PARTIAL]).exists()
    if has_debts:
        return JsonResponse({"ok": False, "error": "cannot delete: unpaid debts exist"}, status=400)

    if not p.is_active:
        return JsonResponse({"ok": True})  # already archived

    p.is_active = False
    p.deleted_at = timezone.now()
    p.save(update_fields=["is_active", "deleted_at"])
    return JsonResponse({"ok": True})
#============================================
@require_GET
@role_required(AccountProfile.Role.MANAGER)
def api_providers_ac(request: HttpRequest) -> JsonResponse:
    """
    Autocomplete for providers (ACTIVE ONLY).
    GET ?q=...
    """
    q = (request.GET.get("q") or "").strip()
    if not q:
        return JsonResponse({"ok": True, "items": []})
    items = (
        Provider.objects
        .filter(is_active=True, name__icontains=q)
        .order_by(Lower("name"))
        .values("id", "name")[:8]
    )
    return JsonResponse({"ok": True, "items": list(items)})


# add near the other @require_GET APIs
@require_GET
@role_required(AccountProfile.Role.MANAGER)
def api_bill_next_serial(request: HttpRequest) -> JsonResponse:
    """
    Returns the next bill serial WITHOUT reserving it.
    This is purely informational; the actual serial is assigned on save().
    """
    last = (
        Bill.objects
        .order_by("-serial")
        .values_list("serial", flat=True)
        .first()
    )
    nxt = 1 if last in (None, 0) else int(last) + 1
    return JsonResponse({"ok": True, "next_serial": nxt})


@require_GET
def api_products_search(request):
    """
    GET /manager/billing/api/products/search/?q=...&mode=name|code|barcode|id
    - name:        name icontains
    - code:        product_number icontains   (your Product has 'product_number', not 'code')
    - barcode:     exact/iexact match against ProductBarcode.* (field name can be 'code' or 'barcode')
    - id:          numeric id match
    """
    q = (request.GET.get("q") or "").strip()
    mode = (request.GET.get("mode") or "name").lower().strip()

    if not q:
        return JsonResponse({"ok": True, "items": []})

    # Pull related for display (set + its collection, if you have that FK chain)
    qs = (
        Product.objects
        .select_related("set", "set__collection")
        .prefetch_related("barcodes", "unit_ids")
        .all()
    )

    def filter_barcode(queryset, value):
        """Try both common field names on ProductBarcode without crashing."""
        try:
            return queryset.filter(Q(barcodes__code__iexact=value))
        except FieldError:
            # If ProductBarcode uses 'barcode' instead of 'code'
            return queryset.filter(Q(barcodes__barcode__iexact=value))

    # Apply filter by mode
    if mode == "id":
        # Search by unit IDs (either unit 1 or 2). Exact, case-insensitive.
        qs = qs.filter(unit_ids__value__iexact=q)

    elif mode == "barcode":
        qs = filter_barcode(qs, q)

    elif mode == "code":
        # Your model calls it 'product_number'
        qs = qs.filter(product_number__icontains=q)

    else:
        # name (default) — also accept product_number here to be helpful
        qs = qs.filter(Q(name__icontains=q) | Q(product_number__icontains=q))

    # Limit results
    qs = qs.order_by("name")[:20]

    # Serializer matching what the JS renders
    items = []
    for p in qs:
        col = getattr(getattr(p, "set", None), "collection", None)
        setobj = getattr(p, "set", None)
        matched_unit = None
        if mode == "id":
            # which unit_id matched?
            for uid in getattr(p, "unit_ids", []).all():
                if uid.value.lower() == q.lower():
                    matched_unit = int(uid.unit_index)
                    break
        elif mode == "barcode":
            # if you want to lock the unit for a barcode hit, too
            for b in getattr(p, "barcodes", []).all():
                val = getattr(b, "barcode", None) or getattr(b, "code", None)
                if (val or "").lower() == q.lower():
                    matched_unit = int(b.unit_index)
                    break

        items.append({
            "id": p.id,
            "name": p.name,
            # Frontend expects 'code' (we’ll put product_number here)
            "code": getattr(p, "product_number", "") or "",
            "col_name": getattr(col, "name", "") or "",
            "col_code": getattr(col, "code", "") or "",
            "set_name": getattr(setobj, "name", "") or "",
            "set_code": getattr(setobj, "code", "") or "",
            "unit_primary_label": p.get_unit_primary_display() or "الوحدة الأولى",
            "unit_secondary_label": (p.get_unit_secondary_display() if p.unit_secondary else "") or "الوحدة الثانية",
            "unit_secondary": getattr(p, "unit_secondary", "") or "",
            "conversion_factor": getattr(p, "conversion_factor", 0) or 0,
            # Keep these in case you want to prefill price/cost in the row
            "price": getattr(p, "price", None),
            "cost": getattr(p, "cost", None),
            "matched_unit": matched_unit,  # 1 or 2 (lets the UI lock/select the right unit)
        })

    return JsonResponse({"ok": True, "items": items})

# ---------- APIs (bills) ----------

@require_POST
@role_required(AccountProfile.Role.MANAGER)
def api_bill_save(request: HttpRequest) -> JsonResponse:
    """
    Create a Bill with BillItems and update stock.
    - Provider MUST be existing (provider.id required).
    - Optional manual serial (positive int, unique).
    - Does NOT overwrite Product cost/price unless update_product_defaults=true.

    JSON body:
    {
      "serial": 1234,        # optional, positive integer, unique
      "provider": {"id": 12},
      "items": [
        {
          "product_id": 1,
          "unit_index": 1|2,
          "qty_raw": "2.5",
          "cost": "12.3",     # per PRIMARY unit
          "price": "15.0",    # per PRIMARY unit (snapshot only)
          "total_cost": "30"  # optional override for line_total
        }, ...
      ],
      "pay": {"status": "paid"|"unpaid"|"partial", "paid_amount": "0"},
      "update_product_defaults": false
    }
    """
    # Parse payload
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except Exception:
        return _err("bad json", 400)

    prov_in = payload.get("provider") or {}
    items_in = payload.get("items") or []
    pay_in = payload.get("pay") or {}
    update_defaults = bool(payload.get("update_product_defaults") or False)

    if not items_in:
        return _err("no items", 400)

    # Provider: require existing by id
    pid = prov_in.get("id")
    if not pid:
        return _err("provider must be selected from list", 400)
    provider = Provider.objects.filter(id=pid).first()
    if not provider:
        return _err("provider not found", 404)

    # Optional manual serial
    serial_in = payload.get("serial")
    serial_val = None
    if serial_in not in (None, ""):
        try:
            serial_val = int(serial_in)
            if serial_val <= 0:
                return _err("serial must be a positive number", 400)
        except (TypeError, ValueError):
            return _err("serial must be numbers only", 400)
        if Bill.objects.filter(serial=serial_val).exists():
            return _err("serial already exists", 409)

    # Payment
    status_raw = (pay_in.get("status") or "unpaid").lower()
    if status_raw not in {"paid", "unpaid", "partial"}:
        status_raw = "unpaid"
    paid_amount = _q3(_to_decimal(pay_in.get("paid_amount"), "0"))

    # Save atomically
    try:
        with transaction.atomic():
            bill = Bill(provider=provider, status=status_raw, paid_amount=paid_amount, total=_DEC0)
            if serial_val is not None:
                bill.serial = serial_val  # honor user-provided serial if valid/unique
            bill.save()  # assigns serial if empty

            grand = _DEC0

            for idx, row in enumerate(items_in, start=1):
                # Validate row payload
                try:
                    prod_id = int(row.get("product_id"))
                except (TypeError, ValueError):
                    return _err(f"invalid product_id at row {idx}", 400)

                unit_idx = int(row.get("unit_index") or 1)
                if unit_idx not in (1, 2):
                    unit_idx = 1

                qty_raw = _to_decimal(row.get("qty_raw"), "0")
                if qty_raw <= _DEC0:
                    return _err(f"qty must be > 0 at row {idx}", 400)

                cost_u1 = _to_decimal(row.get("cost"), "0")
                price_u1 = _to_decimal(row.get("price"), "0")
                if cost_u1 < _DEC0 or price_u1 < _DEC0:
                    return _err(f"negative cost/price not allowed at row {idx}", 400)

                total_override = row.get("total_cost")
                total_override = _to_decimal(total_override, "0") if total_override not in (None, "") else None

                # Lock product for stock update
                product = get_object_or_404(Product.objects.select_for_update(), pk=prod_id)

                # Convert to PRIMARY units if user picked secondary
                qty_primary = qty_raw
                cf = getattr(product, "conversion_factor", None)
                if unit_idx == 2 and cf:
                    qty_primary = qty_raw * Decimal(str(cf))

                # Quantize
                qty_primary = _q3(qty_primary)
                cost_u1 = _q4(cost_u1)
                price_u1 = _q4(price_u1)

                # Compute line total
                if total_override is not None and total_override > _DEC0:
                    line_total = _q3(total_override)
                else:
                    line_total = _q3(cost_u1 * qty_primary)

                # Persist BillItem (snapshot pricing)
                BillItem.objects.create(
                    bill=bill,
                    product=product,
                    unit_index=unit_idx,
                    qty_primary=qty_primary,
                    cost=cost_u1,
                    price=price_u1,
                    line_total=line_total,
                )

                # Update stock only
                product.stock_qty = (getattr(product, "stock_qty", None) or _DEC0) + qty_primary
                update_fields = ["stock_qty"]
                if hasattr(product, "updated_at"):
                    from django.utils import timezone as _tz
                    product.updated_at = _tz.now()
                    update_fields.append("updated_at")
                if update_defaults:
                    if hasattr(product, "cost"):
                        product.cost = cost_u1
                        update_fields.append("cost")
                    if hasattr(product, "price"):
                        product.price = price_u1
                        update_fields.append("price")
                product.save(update_fields=list(dict.fromkeys(update_fields)))

                grand += line_total

            bill.total = _q3(grand)
            bill.save(update_fields=["total"])

            return JsonResponse({
                "ok": True,
                "bill": {
                    "id": bill.id,
                    "serial": bill.serial,
                    "total": str(bill.total),
                    "provider": {"id": provider.id, "name": provider.name},
                    "status": bill.status,
                    "paid_amount": str(bill.paid_amount),
                    "created_at": bill.created_at.isoformat(),
                }
            })
    except Exception:
        return _err("save failed", 500)


@require_GET
@role_required(AccountProfile.Role.MANAGER)
def api_bills_list(request: HttpRequest) -> JsonResponse:
    """
    Keyset-paginated list with filters.
    GET params:
      cursor     : last seen bill id (for keyset pagination, descending by id)
      page_size  : default 30 (max 100)
      q          : provider name icontains
      serial     : exact bill serial (int)
      id         : exact bill id (int)
      date_from  : YYYY-MM-DD
      date_to    : YYYY-MM-DD (inclusive)
      status     : paid|unpaid|partial
    """
    qs = (
        Bill.objects
        .select_related("provider")
        .order_by("-id")
        .only("id", "serial", "total", "status", "created_at", "provider__id", "provider__name")
    )

    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(provider__name__icontains=q)

    serial = request.GET.get("serial")
    if serial:
        try:
            qs = qs.filter(serial=int(serial))
        except ValueError:
            return JsonResponse({"ok": True, "items": [], "next_cursor": None})

    bill_id = request.GET.get("id")
    if bill_id:
        try:
            qs = qs.filter(id=int(bill_id))
        except ValueError:
            return JsonResponse({"ok": True, "items": [], "next_cursor": None})

    status = (request.GET.get("status") or "").lower()
    if status in {"paid", "unpaid", "partial"}:
        qs = qs.filter(status=status)

    date_from = (request.GET.get("date_from") or "").strip()
    date_to = (request.GET.get("date_to") or "").strip()
    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)

    cursor = request.GET.get("cursor")
    if cursor:
        try:
            qs = qs.filter(id__lt=int(cursor))
        except ValueError:
            pass

    try:
        page_size = min(max(int(request.GET.get("page_size", "30")), 1), 100)
    except ValueError:
        page_size = 30

    items = list(qs[:page_size])

    def _row(b: Bill):
        return {
            "id": b.id,
            "serial": b.serial,
            "provider": {"id": b.provider_id, "name": b.provider.name if b.provider_id else ""},
            "total": str(b.total),
            "status": b.status,
            "created_at": b.created_at.isoformat(),
        }

    next_cursor = items[-1].id if items else None
    return JsonResponse({"ok": True, "items": [_row(b) for b in items], "next_cursor": next_cursor})

@require_GET
@role_required(AccountProfile.Role.MANAGER)
def api_debts_list(request: HttpRequest) -> JsonResponse:
    """
    FAST keyset-paginated debts list (bills) with filters.
    Params:
      cursor     : last seen bill id (keyset pagination, id__lt)
      page_size  : default 30 (max 100)
      q          : provider name icontains
      serial     : exact serial (int)
      id         : exact bill id (int)
      date_from  : YYYY-MM-DD
      date_to    : YYYY-MM-DD
      status     : paid|unpaid|partial
    Default ordering: unpaid → partial → paid, then newest first.
    """
    qs = (
        Bill.objects
        .select_related("provider")
            .annotate(
        # make all numeric math explicitly Decimal(…, 3)
                paid_amount_co=Coalesce(
                    F("paid_amount"),
                    Value(0, output_field=DecimalField(max_digits=14, decimal_places=3)),
                    output_field=DecimalField(max_digits=14, decimal_places=3),
                ),
                remaining=ExpressionWrapper(
                    F("total") - Coalesce(
                        F("paid_amount"),
                        Value(0, output_field=DecimalField(max_digits=14, decimal_places=3))
                    ),
                    output_field=DecimalField(max_digits=14, decimal_places=3),
                ),
             )

        .only("id", "serial", "total", "paid_amount", "status", "created_at", "provider__id", "provider__name")
    )

    # Filters
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(provider__name__icontains=q)

    serial = request.GET.get("serial")
    if serial not in (None, ""):
        try:
            qs = qs.filter(serial=int(serial))
        except ValueError:
            return JsonResponse({"ok": True, "items": [], "next_cursor": None})

    bill_id = request.GET.get("id")
    if bill_id not in (None, ""):
        try:
            qs = qs.filter(id=int(bill_id))
        except ValueError:
            return JsonResponse({"ok": True, "items": [], "next_cursor": None})

    status = (request.GET.get("status") or "").lower()
    if status in {"paid", "unpaid", "partial"}:
        qs = qs.filter(status=status)

    date_from = (request.GET.get("date_from") or "").strip()
    date_to = (request.GET.get("date_to") or "").strip()
    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)

    # Cursor pagination
    cursor = request.GET.get("cursor")
    if cursor not in (None, ""):
        try:
            qs = qs.filter(id__lt=int(cursor))
        except ValueError:
            pass

    try:
        page_size = min(max(int(request.GET.get("page_size", "30")), 1), 100)
    except ValueError:
        page_size = 30

    # Order: unpaid → partial → paid, then newest first
    status_weight = Case(
        When(status=Bill.Status.UNPAID, then=Value(0)),
        When(status=Bill.Status.PARTIAL, then=Value(1)),
        When(status=Bill.Status.PAID,   then=Value(2)),
        default=Value(3),
        output_field=IntegerField(),
    )
    qs = qs.order_by(status_weight, "-created_at", "-id")

    items = list(qs[:page_size])

    def _row(b: Bill):
        remaining = (b.total or Decimal("0")) - (b.paid_amount or Decimal("0"))
        return {
            "id": b.id,
            "serial": b.serial,
            "provider": {"id": b.provider_id, "name": b.provider.name if b.provider_id else ""},
            "total": str(b.total),
            "paid_amount": str(b.paid_amount),
            "remaining": str(remaining),
            "status": b.status,
            "created_at": b.created_at.isoformat(),
        }

    next_cursor = items[-1].id if items else None
    return JsonResponse({"ok": True, "items": [_row(b) for b in items], "next_cursor": next_cursor})

@transaction.atomic
def pay_debt_full(request: HttpRequest, bill_id: int):
    """Mark the bill as fully paid."""
    if request.method != "POST":
        return HttpResponseBadRequest("POST required")

    bill = get_object_or_404(Bill.objects.select_for_update(), pk=bill_id)
    remaining = (bill.total or Decimal("0")) - (bill.paid_amount or Decimal("0"))
    if remaining <= 0:
        return JsonResponse({"ok": False, "error": "Bill already fully paid."}, status=400)

    bill.paid_amount = bill.total
    bill.status = Bill.Status.PAID
    bill.save(update_fields=["paid_amount", "status"])

    return JsonResponse({"ok": True, "remaining": "0"})


@transaction.atomic
def pay_debt_batch(request: HttpRequest, bill_id: int):
    """Apply a partial payment amount to the bill."""
    if request.method != "POST":
        return HttpResponseBadRequest("POST required")

    bill = get_object_or_404(Bill.objects.select_for_update(), pk=bill_id)

    amount_str = (request.POST.get("amount") or "").strip()
    try:
        amount = Decimal(amount_str)
    except Exception:
        return JsonResponse({"ok": False, "error": "Enter a positive amount."}, status=400)

    if amount <= 0:
        return JsonResponse({"ok": False, "error": "Enter a positive amount."}, status=400)

    remaining = (bill.total or Decimal("0")) - (bill.paid_amount or Decimal("0"))
    if amount > remaining:
        return JsonResponse({"ok": False, "error": "Amount exceeds remaining debt."}, status=400)

    bill.paid_amount = (bill.paid_amount or Decimal("0")) + amount
    bill.status = (Bill.Status.PAID
                   if bill.paid_amount >= (bill.total or Decimal("0"))
                   else Bill.Status.PARTIAL)
    bill.save(update_fields=["paid_amount", "status"])

    new_remaining = (bill.total or Decimal("0")) - (bill.paid_amount or Decimal("0"))
    return JsonResponse({"ok": True, "remaining": str(new_remaining)})

@require_POST
@role_required(AccountProfile.Role.MANAGER)
def api_bill_delete(request: HttpRequest, bill_id: int) -> JsonResponse:
    """
    Delete a bill and roll stock back by the same quantities added when the bill was saved.
    Does NOT touch Product.cost/price.
    """
    try:
        with transaction.atomic():
            bill = (
                Bill.objects.select_for_update()
                .select_related("provider")
                .prefetch_related("items")
                .get(pk=bill_id)
            )

            # Lock all affected products
            product_ids = list(bill.items.values_list("product_id", flat=True))
            products_by_id = {
                p.id: p for p in Product.objects.select_for_update().filter(id__in=product_ids)
            }

            # Roll stock back
            for it in bill.items.all():
                p = products_by_id.get(it.product_id)
                if not p:
                    continue
                current = getattr(p, "stock_qty", Decimal("0"))
                p.stock_qty = current - (it.qty_primary or Decimal("0"))
                update_fields = ["stock_qty"]
                if hasattr(p, "updated_at"):
                    from django.utils import timezone as _tz
                    p.updated_at = _tz.now()
                    update_fields.append("updated_at")
                p.save(update_fields=update_fields)

            bill.delete()  # cascades BillItem rows
            return JsonResponse({"ok": True})

    except Bill.DoesNotExist:
        return JsonResponse({"ok": False, "error": "not found"}, status=404)
    except Exception:
        return JsonResponse({"ok": False, "error": "delete failed"}, status=500)