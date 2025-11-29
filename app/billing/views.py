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

from inventory.models import DEC0


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
from stock.models import ProductContainer  # ⬅️ NEW


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
        Bill.objects.prefetch_related("items__product", "provider"),
        pk=bill_id,
    )

    # FIFO: left quantity per BillItem, and flags
    left_map, is_closed, untouched = BillingSV.get_bill_status_flags(bill)

    error_msg = None

    # ----- Handle POST: create ProviderReturn from this bill -----
    if request.method == "POST":
        if is_closed:
            error_msg = "لا يمكن إنشاء مرتجع: الفاتورة مغلقة (لا توجد كميات متبقية)."
        else:
            try:
                # 1) read and validate items from form
                items_payload = _build_return_items_from_form(request, bill, left_map)

                # 2) try to reuse same container as the original purchase
                from inventory.models import ProductMovement
                mv_container = None
                item_ids = list(bill.items.values_list("id", flat=True))
                if item_ids:
                    mv = (
                        ProductMovement.objects
                        .filter(
                            source_app="billing",
                            source_model="BillItem",
                            source_id__in=item_ids,
                        )
                        .select_related("container")
                        .first()
                    )
                    if mv and mv.container_id:
                        mv_container = mv.container

                # 3) create ProviderReturn (we treat it as UNPAID by default)
                pret = SV.create_return(
                    actor=request.user,
                    provider_id=bill.provider_id,
                    status="unpaid",
                    paid_amount=Decimal("0"),
                    items=items_payload,
                    container=mv_container,
                )

                # You can redirect to the return view if you have a URL name for it.
                # For now, go to the returns list.
                return redirect("billing_returns_list")
            except ValueError as ve:
                error_msg = str(ve)
            except Exception:
                logger.exception("bill_view: failed to create ProviderReturn from bill")
                error_msg = "فشل حفظ المرتجع، حدث خطأ غير متوقع."

        # if we fall through, we re-render the page with error_msg

    # Build rows for template (GET or POST with errors)
    items_rows = []
    for it in bill.items.all():
        prod = it.product
        left_qty = left_map.get(it.id, DEC0)
        can_return = left_qty > DEC0

        # qty_u1 is the stored primary qty
        qty_u1 = it.qty_primary

        # qty_u2 depends on conversion_factor and unit_index
        qty_u2 = None
        try:
            if prod and prod.conversion_factor and prod.conversion_factor > 0 and it.unit_index == 1:
                qty_u2 = it.qty_primary / prod.conversion_factor
        except Exception:
            qty_u2 = None

        items_rows.append(
            {
                "item_id": it.id,
                "product_name": getattr(prod, "name", "") or "",
                "unit1_label": prod.get_unit_primary_display() if prod else "",
                "unit2_label": (
                    prod.get_unit_secondary_display()
                    if (prod and getattr(prod, "unit_secondary", None))
                    else ""
                ),
                "cost": it.cost,
                "price": it.price,
                "qty_u1": qty_u1,
                "qty_u2": qty_u2,
                "line_total": it.line_total,
                "left_qty": left_qty,
                "can_return": can_return,
            }
        )

    # Existing "has_debt_now" logic – keep whatever you had before
    has_debt_now = False
    try:
        if bill.serial:
            has_debt_now = DebtorEntry.objects.filter(doc_serial=bill.serial).exists()
    except Exception:
        has_debt_now = False

    ctx = {
        "bill": bill,
        "items_rows": items_rows,
        "is_closed": is_closed,
        "untouched": untouched,
        "can_delete": untouched,       # bill can be deleted only if untouched
        "can_return": not is_closed,   # if closed → no more returns
        "has_debt_now": has_debt_now,
        "error_msg": error_msg,
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
    containers = (
        ProductContainer.objects
        .filter(is_active=True)
        .order_by("sort_order", "name")
    )
    ctx = {"containers": containers}
    return render(request, "billing/providers_returns.html", ctx)


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

    # ---- Container handling (same style as api_bill_save) ----
    # Accept:
    #   payload["container_code"] = "store"
    #   payload["container"] = {"code": "store"}
    #   payload["container"] = "store"
    raw_container = payload.get("container")
    container_code = (payload.get("container_code") or "")

    if not container_code:
        if isinstance(raw_container, dict):
            container_code = (raw_container.get("code") or "")
        else:
            container_code = (raw_container or "")

    if isinstance(container_code, str):
        container_code = container_code.strip()
    else:
        container_code = ""

    if not container_code:
        container_code = "store"

    from stock.models import ProductContainer  # local import

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

    try:
        # Try new signature with container kwarg
        try:
            pret = SV.create_return(
                actor=request.user,
                provider_id=int(pid),
                status=status,
                paid_amount=paid_amount,
                items=items,
                container=container,
            )
        except TypeError:
            # Fallback for older create_return without container parameter
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
