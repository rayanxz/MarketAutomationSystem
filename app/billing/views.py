# app/billing/views.py
from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation

from django.http import JsonResponse, HttpRequest, HttpResponse
from django.shortcuts import redirect, render, get_object_or_404
from django.views.decorators.http import require_GET, require_POST
from django.utils import timezone
from django.db.models import Q  # needed for products search filters

from django.contrib.auth.decorators import login_required

from billing import services as BillingSV

from inventory.models import DEC0 , q3 , ProductMovement

DEC2 = Decimal("0.01")


def _fmt2(x: Decimal | None) -> str:
    """
    Format a Decimal with 2 decimal places for display.
    """
    q = (x if x is not None else DEC0).quantize(DEC2)
    return f"{q:.2f}"

from django.db.models import Sum
from stock.models import StockFifoLayer

from accounts.models import AccountProfile
from catalog.views import role_required
from catalog.models import Product

from billing.models import Provider, Bill, ProviderReturn
from debts.models import DebtorDebt as DebtorEntry, CreditorDebt as CreditorEntry

from . import selectors as S
from . import services as SV
from .serializers import provider_row, bill_row, return_row

import logging
logger = logging.getLogger(__name__)

from datetime import date

from typing import Any

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
def providers_list(request: HttpRequest) -> HttpResponse:
    return render(request, "billing/providers_list.html")



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


def _build_return_items_from_form(request: HttpRequest, bill: Bill, left_map: dict[int, Decimal]):
    """
    Read POST fields return_qty_<item_id> and return_cost_<item_id>,
    validate against remaining FIFO qty, and build items payload for SV.create_return().
    """
    items_payload = []

    for it in bill.items.all():
        field_qty = f"return_qty_{it.id}"
        raw_qty = (request.POST.get(field_qty) or "").strip()
        if not raw_qty:
            continue

        try:
            qty = Decimal(raw_qty)
        except Exception:
            raise ValueError(f"الكمية المدخلة للمنتج '{it.product.name}' غير صحيحة.")

        if qty <= 0:
            continue  # ignore zeros / negatives

        left_allowed = left_map.get(it.id, DEC0)
        if qty > left_allowed:
            raise ValueError(
                f"الكمية المرتجعة للمنتج '{it.product.name}' أكبر من الكمية المتبقية ({left_allowed})."
            )

        field_cost = f"return_cost_{it.id}"
        raw_cost = (request.POST.get(field_cost) or "").strip()
        try:
            cost = Decimal(raw_cost) if raw_cost else (it.cost or Decimal("0"))
        except Exception:
            raise ValueError(f"كلفة المرتجع للمنتج '{it.product.name}' غير صحيحة.")

        if cost < 0:
            cost = -cost

        # We treat the quantity as primary-unit qty_raw (unit_index = 1)
        items_payload.append(
            {
                "product_id": it.product_id,
                "unit_index": 1,
                "qty_raw": str(qty),
                "cost": str(cost),
            }
        )

    if not items_payload:
        raise ValueError("لم يتم إدخال أي كميات مرتجعة.")

    return items_payload


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

    # ---- Container handling ----
    # Accept several shapes:
    #   payload["container_code"] = "store"
    #   payload["container"] = {"code": "store"}
    #   payload["container"] = "store"
    container_code = (
        (payload.get("container_code") or "")
        or (payload.get("container") or {}).get("code", "") if isinstance(payload.get("container"), dict) else payload.get("container", "")
    )

    if isinstance(container_code, str):
        container_code = container_code.strip()
    else:
        container_code = ""

    # Default to 'store' if nothing sent
    if not container_code:
        container_code = "store"

    from stock.models import ProductContainer  # local import to avoid touching global imports

    try:
        container = ProductContainer.objects.get(code=container_code)
    except ProductContainer.DoesNotExist:
        return _bad("invalid container", 400)

    # ---- Pay section ----
    pay = payload.get("pay") or {}
    status = (pay.get("status") or "unpaid").lower()
    if status not in {"paid", "unpaid", "partial"}:
        status = "unpaid"
    paid_amount = _dec(pay.get("paid_amount"), "0")

    update_defaults = bool(payload.get("update_product_defaults") or False)

    try:
        # Try with container kwarg (new signature)
        try:
            bill = SV.create_bill(
                actor=request.user,
                provider_id=int(pid),
                status=status,
                paid_amount=paid_amount,
                items=items,
                update_product_defaults=update_defaults,
                container=container,
            )
        except TypeError:
            # Fallback for old create_bill without container param
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

    qs = S.bills_list_filters(
        S.bills_base(),
        q,
        serial,
        status,
        date_from,
        date_to,
        cursor,
        page_size,
    )
    qs = qs.order_by("-id")[:page_size]
    items = list(qs)

    # Optional status filter at Python-level (since status is now a property)
    if status in {"paid", "unpaid", "partial"}:
        items = [b for b in items if (b.status or "").lower() == status]

    nxt = items[-1].id if items else None

    # ---- HERE is the FIFO flag logic for each bill ----
    payload_items = []
    for b in items:
        # existing serializer
        row = bill_row(b)

        # left_map not needed here, just flags
        _left_map, is_closed, untouched = BillingSV.get_bill_status_flags(b)

        row["is_closed"] = is_closed
        row["can_delete"] = untouched   # untouched → no qty used yet → allowed to delete

        payload_items.append(row)

    return JsonResponse(
        {
            "ok": True,
            "items": payload_items,
            "next_cursor": nxt,
        }
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
@login_required
def bill_view(request, bill_id: int):
    bill = get_object_or_404(
        Bill.objects.prefetch_related(
            "items__product",
            "provider",
            "items__product__barcodes",
            "items__product__unit_ids",
        ),
        pk=bill_id,
    )

    # ===== FIFO: left quantity per BillItem + per-container breakdown =====
    item_qs = bill.items.all().select_related("product")
    item_ids = [it.id for it in item_qs]

    left_map: dict[int, Decimal] = {it.id: DEC0 for it in item_qs}
    stock_by_item: dict[int, list[dict]] = {it.id: [] for it in item_qs}

    if item_ids:
        qty_field_candidates = (
            "qty_left_primary",
            "qty_left",
            "qty_remaining",
            "qty_primary_left",
        )

        layers = (
            StockFifoLayer.objects
            .filter(
                source_app="billing",
                source_model="BillItem",
                source_id__in=[str(i) for i in item_ids],
            )
            .select_related("container")
        )

        for layer in layers:
            try:
                iid = int(layer.source_id or "0")
            except (TypeError, ValueError):
                continue
            if iid not in left_map:
                continue

            qty_raw = None
            for fname in qty_field_candidates:
                if hasattr(layer, fname):
                    qty_raw = getattr(layer, fname)
                    break

            qty = q3(qty_raw or DEC0)
            if qty <= DEC0:
                continue

            left_map[iid] = q3(left_map.get(iid, DEC0) + qty)

            cont = getattr(layer, "container", None)
            code = getattr(cont, "code", "") or ""
            name = getattr(cont, "name", "") or ""

            lst = stock_by_item.setdefault(iid, [])
            existing = None
            for c in lst:
                if c["code"] == code and c["name"] == name:
                    existing = c
                    break

            if existing is not None:
                existing["left_qty"] = q3((existing["left_qty"] or DEC0) + qty)
            else:
                lst.append(
                    {
                        "code": code,
                        "name": name,
                        "left_qty": qty,
                    }
                )

    total_original = sum((it.qty_primary or DEC0) for it in item_qs)
    total_left_now = sum(left_map.values(), DEC0)

    untouched = (total_left_now == total_original)
    is_closed = (total_left_now <= DEC0)

    # ===== TOTAL RETURNED PER PRODUCT for this bill =====
    from billing.models import ProviderReturnItem

    product_ids = list({it.product_id for it in item_qs if it.product_id})
    returned_by_product: dict[int, Decimal] = {}
    if product_ids and bill.serial:
        ret_rows = (
            ProviderReturnItem.objects
            .filter(
                ret__source_bill_serial=bill.serial,
                product_id__in=product_ids,
            )
            .values("product_id")
            .annotate(total_ret=Sum("qty_primary"))
        )
        for rr in ret_rows:
            pid = rr["product_id"]
            returned_by_product[pid] = q3(rr["total_ret"] or DEC0)

    # original container map (kept if you ever need it)
    origin_by_item: dict[int, ProductMovement] = {}
    if item_ids:
        mv_rows = (
            ProductMovement.objects
            .filter(
                source_app="billing",
                source_model="BillItem",
                source_id__in=item_ids,
            )
            .select_related("container")
            .order_by("id")
        )
        for mv in mv_rows:
            iid = int(mv.source_id)
            if iid not in origin_by_item and mv.container_id:
                origin_by_item[iid] = mv

    error_msg = None

    # NOTE: old inline-return POST kept as-is (no form now, so practically unused)
    if request.method == "POST":
        error_msg = "هذه الصفحة تستخدم الآن معالج المرتجعات الجديد."

    # ===== Build rows for template =====
    items_rows = []
    for it in item_qs:
        prod = it.product
        if not prod:
            continue

        unit1_label = prod.get_unit_primary_display() or ""
        unit2_label = (
            prod.get_unit_secondary_display()
            if getattr(prod, "unit_secondary", None)
            else ""
        )

        qty_primary = q3(it.qty_primary or DEC0)
        cf = getattr(prod, "conversion_factor", None)
        try:
            cf_val = Decimal(str(cf)) if cf else None
        except Exception:
            cf_val = None

        qty_u1 = qty_primary

        if cf_val and cf_val != 0:
            try:
                qty_u2 = q3(qty_primary / cf_val)
            except Exception:
                qty_u2 = DEC0
        else:
            qty_u2 = DEC0

        highlight_unit = 1 if int(it.unit_index) == 1 else 2
        left_qty = q3(left_map.get(it.id, DEC0))
        total_returned = q3(returned_by_product.get(it.product_id, DEC0))
        has_returns = total_returned > DEC0

        containers = stock_by_item.get(it.id, [])
        store_qty = DEC0
        wh1_qty = DEC0
        wh2_qty = DEC0
        for c in containers:
            code = (c.get("code") or "").lower()
            q_left = q3(c.get("left_qty") or DEC0)
            if code == "store":
                store_qty = q3(store_qty + q_left)
            elif code == "wh1":
                wh1_qty = q3(wh1_qty + q_left)
            elif code == "wh2":
                wh2_qty = q3(wh2_qty + q_left)

        sold_qty = q3(qty_primary - left_qty - total_returned)
        if sold_qty < DEC0:
            sold_qty = DEC0

        # product identifiers for search
        prod_code = getattr(prod, "product_number", "") or ""
        barcode_val = ""
        try:
            for b in getattr(prod, "barcodes", []).all():
                val = getattr(b, "barcode", None) or getattr(b, "code", None)
                if val:
                    barcode_val = str(val)
                    break
        except Exception:
            barcode_val = ""

        unit_id_val = ""
        try:
            for uid in getattr(prod, "unit_ids", []).all():
                val = getattr(uid, "value", "") or ""
                if val:
                    unit_id_val = str(val)
                    break
        except Exception:
            unit_id_val = ""

        items_rows.append(
            {
                "item_id": it.id,
                "product_name": getattr(prod, "name", "") or "",
                "unit1_label": unit1_label,
                "unit2_label": unit2_label,
                "cost": it.cost,
                "price": it.price,
                "qty_u1": qty_u1,
                "qty_u1_str": f"{_fmt2(qty_u1)} {unit1_label}",
                "qty_u2": qty_u2,
                "qty_u2_str": f"{_fmt2(qty_u2)} {unit2_label}" if unit2_label else "",
                "highlight_unit": highlight_unit,
                "line_total": it.line_total,
                "left_qty": left_qty,
                "left_qty_str": f"{_fmt2(left_qty)} {unit1_label}",
                "returned_qty": total_returned,
                "returned_qty_str": f"{_fmt2(total_returned)} {unit1_label}",
                "has_returns": has_returns,
                "can_return": left_qty > DEC0,
                "store_qty": store_qty,
                "store_qty_str": f"{_fmt2(store_qty)} {unit1_label}",
                "wh1_qty": wh1_qty,
                "wh1_qty_str": f"{_fmt2(wh1_qty)} {unit1_label}",
                "wh2_qty": wh2_qty,
                "wh2_qty_str": f"{_fmt2(wh2_qty)} {unit1_label}",
                "sold_qty": sold_qty,
                "sold_qty_str": f"{_fmt2(sold_qty)} {unit1_label}",
                # for search
                "code": prod_code,
                "barcode": barcode_val,
                "unit_id": unit_id_val,
            }
        )

    has_debt_now = False
    try:
        if bill.serial:
            has_debt_now = DebtorEntry.objects.filter(doc_serial=bill.serial).exists()
    except Exception:
        has_debt_now = False

    # parse selected items (when coming back from wizard with ?items=1,2,3)
    raw_sel = (request.GET.get("items") or "").strip()
    selected_items: list[int] = []
    for part in raw_sel.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            selected_items.append(int(part))
        except ValueError:
            continue

    # initial status at creation (if field exists, otherwise use current)
    initial_status = getattr(bill, "initial_status", None) or (bill.status or "")

    ctx = {
        "bill": bill,
        "items_rows": items_rows,
        "is_closed": is_closed,
        "untouched": untouched,
        "can_delete": untouched,
        "can_return": not is_closed,
        "has_debt_now": has_debt_now,
        "error_msg": error_msg,
        "selected_items": selected_items,
        "initial_status": initial_status,
    }
    return render(request, "billing/bill_view.html", ctx)




@role_required(AccountProfile.Role.MANAGER)
@login_required
def bill_return_wizard(request: HttpRequest, bill_id: int) -> HttpResponse:
    """
    Second page: choose per-container returned qty + cost for selected items
    from a purchase bill.
    """
    bill = get_object_or_404(
        Bill.objects.prefetch_related("items__product", "provider"),
        pk=bill_id,
    )

    # ----- base queryset -----
    item_qs = bill.items.all().select_related("product")
    item_ids = [it.id for it in item_qs]

    # ===== FIFO: left qty per BillItem + per-container breakdown =====
    left_map: dict[int, Decimal] = {it.id: DEC0 for it in item_qs}
    stock_by_item: dict[int, list[dict]] = {it.id: [] for it in item_qs}

    if item_ids:
        qty_field_candidates = (
            "qty_left_primary",
            "qty_left",
            "qty_remaining",
            "qty_primary_left",
        )

        layers = (
            StockFifoLayer.objects
            .filter(
                source_app="billing",
                source_model="BillItem",
                source_id__in=[str(i) for i in item_ids],
            )
            .select_related("container")
        )

        for layer in layers:
            try:
                iid = int(layer.source_id or "0")
            except (TypeError, ValueError):
                continue
            if iid not in left_map:
                continue

            qty_raw = None
            for fname in qty_field_candidates:
                if hasattr(layer, fname):
                    qty_raw = getattr(layer, fname)
                    break

            qty = q3(qty_raw or DEC0)
            if qty <= DEC0:
                continue

            left_map[iid] = q3(left_map.get(iid, DEC0) + qty)

            cont = getattr(layer, "container", None)
            code = getattr(cont, "code", "") or ""
            name = getattr(cont, "name", "") or ""

            lst = stock_by_item.setdefault(iid, [])
            existing = None
            for c in lst:
                if c["code"] == code and c["name"] == name:
                    existing = c
                    break
            if existing is not None:
                existing["left_qty"] = q3((existing["left_qty"] or DEC0) + qty)
            else:
                lst.append(
                    {
                        "code": code,
                        "name": name,
                        "left_qty": qty,
                    }
                )

    # check bill closed
    total_left_now = sum(left_map.values(), DEC0)
    is_closed = (total_left_now <= DEC0)
    if is_closed:
        # nothing to return, go back
        return redirect("billing_bill_view", bill_id=bill.id)

    # parse selected items (GET or POST)
    if request.method == "POST":
        raw_items = (request.POST.get("items_ids") or "").strip()
    else:
        raw_items = (request.GET.get("items") or "").strip()

    selected_ids: set[int] = set()
    for part in raw_items.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            selected_ids.add(int(part))
        except ValueError:
            continue

    # filter items to selected with remaining qty
    item_qs = [it for it in item_qs if it.id in selected_ids and left_map.get(it.id, DEC0) > DEC0]
    if not item_qs:
        return redirect("billing_bill_view", bill_id=bill.id)

    # ===== build simple rows =====
    rows = []
    for it in item_qs:
        prod = it.product
        if not prod:
            continue

        unit1_label = prod.get_unit_primary_display() or ""

        left_qty = q3(left_map.get(it.id, DEC0))

        containers = stock_by_item.get(it.id, [])
        store_qty = DEC0
        wh1_qty = DEC0
        wh2_qty = DEC0
        for c in containers:
            code = (c.get("code") or "").lower()
            q_left = q3(c.get("left_qty") or DEC0)
            if code == "store":
                store_qty = q3(store_qty + q_left)
            elif code == "wh1":
                wh1_qty = q3(wh1_qty + q_left)
            elif code == "wh2":
                wh2_qty = q3(wh2_qty + q_left)

        rows.append(
            {
                "item_id": it.id,
                "product_name": getattr(prod, "name", "") or "",
                "unit1_label": unit1_label,
                "cost": it.cost,
                "left_qty": left_qty,
                "left_qty_str": f"{_fmt2(left_qty)} {unit1_label}",
                "store_qty": store_qty,
                "store_qty_str": f"{_fmt2(store_qty)} {unit1_label}",
                "wh1_qty": wh1_qty,
                "wh1_qty_str": f"{_fmt2(wh1_qty)} {unit1_label}",
                "wh2_qty": wh2_qty,
                "wh2_qty_str": f"{_fmt2(wh2_qty)} {unit1_label}",
            }
        )
#=========================from here ================================================================
    #<-- this vertical 
    error_msg: str | None = None

    # keep what user selected for status / amount (so we can re-fill on error)
    if request.method == "POST":
        return_status_selected = (request.POST.get("return_status") or "").lower().strip()
        return_paid_amount_raw = (request.POST.get("return_paid_amount") or "").strip()
    else:
        # initial GET → nothing chosen, box empty
        return_status_selected = ""
        return_paid_amount_raw = ""

    if request.method == "POST":
        # 1) copy all raw inputs from POST into rows so we can re-render them on error
        for r in rows:
            iid = r["item_id"]
            key_store = f"ret_store_{iid}"
            key_wh1 = f"ret_wh1_{iid}"
            key_wh2 = f"ret_wh2_{iid}"
            key_cost = f"ret_cost_{iid}"

            r["ret_store_raw"] = (request.POST.get(key_store) or "").strip()
            r["ret_wh1_raw"] = (request.POST.get(key_wh1) or "").strip()
            r["ret_wh2_raw"] = (request.POST.get(key_wh2) or "").strip()
            r["ret_cost_raw"] = (request.POST.get(key_cost) or "").strip()

        # 2) build payload + validate
        items_payload: list[dict[str, Any]] = []
        total_return_cost = DEC0  # إجمالي قيمة المرتجع (كل الصفوف)

        try:
            for r in rows:
                iid = r["item_id"]
                it = next(i for i in item_qs if i.id == iid)
                prod = it.product

                key_store = f"ret_store_{iid}"
                key_wh1 = f"ret_wh1_{iid}"
                key_wh2 = f"ret_wh2_{iid}"
                key_cost = f"ret_cost_{iid}"

                # use the raw values we already copied into row
                q_store = _dec(r.get("ret_store_raw"), "0")
                q_wh1 = _dec(r.get("ret_wh1_raw"), "0")
                q_wh2 = _dec(r.get("ret_wh2_raw"), "0")

                if q_store < DEC0 or q_wh1 < DEC0 or q_wh2 < DEC0:
                    raise ValueError("لا يمكن إدخال كميات سالبة للمرتجع.")

                qty_total = q3(q_store + q_wh1 + q_wh2)
                if qty_total <= DEC0:
                    # no return for this row, skip
                    continue

                # check against available per container
                if q_store > r["store_qty"]:
                    raise ValueError(f"الكمية المرتجعة من المتجر للمنتج '{prod.name}' أكبر من المتاح.")
                if q_wh1 > r["wh1_qty"]:
                    raise ValueError(f"الكمية المرتجعة من مستودع 1 للمنتج '{prod.name}' أكبر من المتاح.")
                if q_wh2 > r["wh2_qty"]:
                    raise ValueError(f"الكمية المرتجعة من مستودع 2 للمنتج '{prod.name}' أكبر من المتاح.")

                # also total vs left
                if qty_total > r["left_qty"]:
                    raise ValueError(f"إجمالي الكمية المرتجعة للمنتج '{prod.name}' أكبر من الكمية المتبقية.")

                # cost: use raw if provided, otherwise default to item cost
                raw_cost = r.get("ret_cost_raw") or str(it.cost or "0")
                cost = _dec(raw_cost, str(it.cost or "0"))

                # accumulate total return cost (cost * qty_total)
                total_return_cost = q3(total_return_cost + (cost * qty_total))

                container_splits: list[dict[str, str]] = []
                if q_store > DEC0:
                    container_splits.append(
                        {"code": "store", "qty_primary": str(q_store)}
                    )
                if q_wh1 > DEC0:
                    container_splits.append(
                        {"code": "wh1", "qty_primary": str(q_wh1)}
                    )
                if q_wh2 > DEC0:
                    container_splits.append(
                        {"code": "wh2", "qty_primary": str(q_wh2)}
                    )

                items_payload.append(
                    {
                        "product_id": it.product_id,
                        "unit_index": 1,
                        "qty_primary": str(qty_total),
                        "cost": str(cost),
                        "container_splits": container_splits,
                    }
                )

            if not items_payload:
                raise ValueError("لم يتم إدخال أي كميات مرتجعة.")

            # 3) pay status + amount validation
            raw_status = (return_status_selected or "").lower()
            if raw_status not in {"paid", "unpaid", "partial"}:
                # user didn’t pick any radio
                raise ValueError("يجب اختيار حالة دفع للمرتجع.")

            paid_amount = _dec(return_paid_amount_raw or "0", "0")

            if paid_amount < DEC0:
                paid_amount = -paid_amount

            status = raw_status

            if status == "partial":
                # partial + no amount → treat as unpaid
                if paid_amount <= DEC0:
                    status = "unpaid"
                    paid_amount = DEC0
                else:
                    if total_return_cost <= DEC0:
                        raise ValueError("لا يمكن تحديد حالة الدفع جزئية مع إجمالي مرتجع صفري.")
                    if paid_amount > total_return_cost:
                        raise ValueError("المبلغ المدفوع لا يمكن أن يتجاوز إجمالي قيمة المرتجع.")
                    if paid_amount == total_return_cost:
                        status = "paid"

            elif status == "paid":
                if total_return_cost <= DEC0:
                    raise ValueError("لا يمكن تحديد حالة الدفع مدفوعة بالكامل مع إجمالي مرتجع صفري.")
                if paid_amount == DEC0:
                    # if manager leaves box empty with 'paid', assume full amount
                    paid_amount = total_return_cost
                elif paid_amount > total_return_cost:
                    raise ValueError("المبلغ المدفوع لا يمكن أن يتجاوز إجمالي قيمة المرتجع.")

            else:  # unpaid
                paid_amount = DEC0

            # 4) create ProviderReturn
            pret = SV.create_return(
                actor=request.user,
                provider_id=bill.provider_id,
                status=status,
                paid_amount=paid_amount,
                items=items_payload,
                container=None,  # using per-item container_splits
                source_bill_serial=bill.serial,
            )

            return redirect("billing_returns_list")

        except ValueError as ve:
            error_msg = str(ve)
        except Exception:
            logger.exception("bill_return_wizard: failed to create ProviderReturn")
            error_msg = "فشل حفظ المرتجع، حدث خطأ غير متوقع."


    selected_ids_str = ",".join(str(r["item_id"]) for r in rows)

    ctx = {
        "bill": bill,
        "rows": rows,
        "error_msg": error_msg,
        "items_ids": selected_ids_str,
        "return_status_selected": return_status_selected,
        "return_paid_amount_raw": return_paid_amount_raw,
    }

    return render(request, "billing/bill_return_wizard.html", ctx)




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
def providers_returns_list_page(request: HttpRequest) -> HttpResponse:
    return render(request, "billing/providers_returns_list.html")


@require_GET
@role_required(AccountProfile.Role.MANAGER)
def api_returns_list(request: HttpRequest) -> JsonResponse:
    q = (request.GET.get("q") or "").strip()
    serial = request.GET.get("serial")
    rid = request.GET.get("id")
    bill_serial = request.GET.get("bill_serial")
    date_from = (request.GET.get("date_from") or "").strip()
    date_to = (request.GET.get("date_to") or "").strip()
    status = (request.GET.get("status") or "").lower()  # property-based
    cursor = request.GET.get("cursor")
    try:
        page_size = min(max(int(request.GET.get("page_size", "30")), 1), 100)
    except ValueError:
        page_size = 30

    qs = S.returns_list_filters(
        q,
        serial,
        rid,
        bill_serial,
        status,
        date_from,
        date_to,
        cursor,
        page_size,
    )

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
