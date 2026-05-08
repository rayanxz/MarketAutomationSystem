# app/billing/views.py
from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.http import JsonResponse, HttpRequest, HttpResponse
from django.shortcuts import redirect, render, get_object_or_404
from django.views.decorators.http import require_GET, require_POST
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.urls import reverse

from django.contrib.auth.decorators import login_required

from billing import services as BillingSV

from inventory.models import DEC0 , q3 , ProductMovement , q4

from financials.models import Currency, MoneyContainer

from financials import services as FinSV
from core.public_ids import peek_next_public_id
from core.date_filters import parse_filter_date
from core.formatters import format_quantity, round_money


DEC2 = Decimal("0.01")


def _fmt2(x: Decimal | None) -> str:
    """
    Format a Decimal with 2 decimal places for display.
    """
    q = (x if x is not None else DEC0).quantize(DEC2, rounding=ROUND_HALF_UP)
    return f"{q:.2f}"


def _ui_2dp(x: Decimal | None) -> Decimal:
    """
    Display-only quantization to 2 decimals (half-up).
    Storage precision remains unchanged.
    """
    try:
        d = Decimal(str(x if x is not None else DEC0))
    except (InvalidOperation, TypeError, ValueError):
        return DEC0
    return d.quantize(DEC2, rounding=ROUND_HALF_UP)

from django.db.models import Sum , Q
from stock.models import StockFifoLayer

from accounts.models import AccountProfile
from accounts.decorators import role_required
from catalog.models import Product

from billing.models import (
    Provider,
    Bill,
    ProviderReturn,
    BILL_PUBLIC_ID_PREFIX,
    PROVIDER_RETURN_PUBLIC_ID_PREFIX,
    BILL_PUBLIC_ID_SEQUENCE_KEY,
    PROVIDER_RETURN_PUBLIC_ID_SEQUENCE_KEY,
)
from debts.models import (
    DebtorDebt as DebtorEntry,
    CreditorDebt as CreditorEntry,
    DebtRecord,
    DebtDirection,
    DebtCauseType,
    DebtStatus,
)
from debts.source_identity import source_identity_lookup_q, source_identity_numeric_base

from . import selectors as S
from . import services as SV
from .serializers import provider_row, bill_row, return_row

import logging
logger = logging.getLogger(__name__)

from datetime import date

from typing import Any

from django.conf import settings

# ---------- Page views ----------

PURCHASE_BILLS_FEATURE_CODE = "purchase_bills"
PROVIDER_RETURNS_FEATURE_CODE = "provider_returns"


def _purchase_money_containers_qs_for_user(user):
    return (
        FinSV.money_containers_for_user_qs(
            user=user,
            feature_code=PURCHASE_BILLS_FEATURE_CODE,
        )
        .order_by("id")
    )


def _resolve_purchase_money_container_for_user(*, user, container_id: int):
    return FinSV.resolve_money_container_for_user(
        user=user,
        container_id=container_id,
        feature_code=PURCHASE_BILLS_FEATURE_CODE,
    )


def _normalize_public_ref(value: str | int | None) -> str:
    return str(value or "").strip()


def _is_valid_public_ref_for_prefix(*, token: str, prefix: str) -> bool:
    normalized = str(token or "").strip().upper()
    normalized_prefix = str(prefix or "").strip().upper()
    if not normalized or not normalized_prefix:
        return False
    pattern = rf"^{re.escape(normalized_prefix)}\d+$"
    return bool(re.fullmatch(pattern, normalized))


def _resolve_bill_from_ref(*, ref: str | int, for_update: bool = False):
    token = _normalize_public_ref(ref)
    if not _is_valid_public_ref_for_prefix(token=token, prefix=BILL_PUBLIC_ID_PREFIX):
        return None
    qs = Bill.objects
    if for_update:
        qs = qs.select_for_update()
    return qs.filter(public_id__iexact=token).first()


def _resolve_provider_return_from_ref(*, ref: str | int, for_update: bool = False):
    token = _normalize_public_ref(ref)
    if not _is_valid_public_ref_for_prefix(token=token, prefix=PROVIDER_RETURN_PUBLIC_ID_PREFIX):
        return None
    qs = ProviderReturn.objects
    if for_update:
        qs = qs.select_for_update()
    return qs.filter(public_id__iexact=token).first()

@role_required(AccountProfile.Role.MANAGER)
def billing_home(request: HttpRequest) -> HttpResponse:
    return redirect("billing_list")


@role_required(AccountProfile.Role.MANAGER)
def bills_list(request: HttpRequest) -> HttpResponse:
    return render(request, "billing/bills_list.html")


@role_required(AccountProfile.Role.MANAGER)
def add_bill(request: HttpRequest) -> HttpResponse:
    money_containers = _purchase_money_containers_qs_for_user(request.user)

    currencies = Currency.objects.filter(is_active=True).order_by("code")

    try:
        fx = FinSV.get_current_fx_syp_per_usd()
    except Exception:
        fx = None

    return render(
        request,
        "billing/add_bill.html",
        {
            "money_containers": money_containers,
            "currencies": currencies,
            "fx_syp_per_usd": fx,
        },
    )

@role_required(AccountProfile.Role.MANAGER)
def providers_list(request: HttpRequest) -> HttpResponse:
    return render(request, "billing/providers_list.html")



@role_required(AccountProfile.Role.MANAGER)
def return_view(request: HttpRequest, ret_id: str) -> HttpResponse:
    """
    تفاصيل مرتجع مورد:
    - بيانات الهيدر (المورد، السيريال، التاريخ)
    - جدول المنتجات مع توزيع الكميات على الحاويات
    - ملخص الدفع (عند الإنشاء / الوضع الحالي)
    """
    from collections import defaultdict

    # ----- حمل المرتجع مع العناصر والمورد -----
    pret_ref = _resolve_provider_return_from_ref(ref=ret_id)
    if pret_ref is None:
        return HttpResponse(status=404)
    pret = (
        ProviderReturn.objects
        .select_related("provider")
        .prefetch_related("items__product")
        .get(pk=pret_ref.id)
    )

    created_status = pret.initial_status
    created_paid = pret.initial_paid

    # ----- حاول ربط المرتجع بفاتورة الشراء الأصلية (إن وجدت) -----
    source_bill = None
    source_bill_ref = (getattr(pret, "source_bill_public_id", "") or "").strip()
    if source_bill_ref:
        source_bill = (
            Bill.objects
            .select_related("provider")
            .filter(public_id__iexact=source_bill_ref)
            .first()
        )
    if source_bill is None and pret.source_bill_serial:
        source_bill = (
            Bill.objects
            .select_related("provider")
            .filter(serial=pret.source_bill_serial)
            .first()
        )

    # ====== توزيع الكميات على الحاويات (store / wh1 / wh2 / other) ======
    per_prod_cont: dict[int, dict[str, Decimal]] = defaultdict(
        lambda: {"store": DEC0, "wh1": DEC0, "wh2": DEC0, "other": DEC0}
    )

    mv_qs = (
        ProductMovement.objects
        .filter(
            source_app="billing",
            source_model="ProviderReturn",
            source_id=str(pret.id),
        )
        .select_related("product", "container")
    )

    for mv in mv_qs:
        pid = mv.product_id
        code = (getattr(mv.container, "code", "") or "").lower()

        # الكمية في الـ ProductMovement تكون سالبة في المرتجع → نعرضها موجبة
        qty_raw = q3(getattr(mv, "qty_primary", DEC0) or DEC0)
        qty = q3(-qty_raw if qty_raw < DEC0 else qty_raw)

        if qty <= DEC0:
            continue

        bucket = code if code in ("store", "wh1", "wh2") else "other"
        per_prod_cont[pid][bucket] = q3(per_prod_cont[pid][bucket] + qty)

    # ====== بناء صفوف العناصر للـ template ======
    item_rows: list[dict[str, Any]] = []
    for it in pret.items.all():
        prod = it.product
        unit_label = (getattr(it, "unit_1_label_at_txn", "") or "").strip() or "—"

        cont = per_prod_cont.get(it.product_id, {})
        store_qty = cont.get("store", DEC0)
        wh1_qty   = cont.get("wh1", DEC0)
        wh2_qty   = cont.get("wh2", DEC0)
        other_qty = cont.get("other", DEC0)

        item_rows.append(
            {
                "item": it,
                "product_name": ((getattr(it, "product_name_at_txn", "") or "").strip() or "—"),
                "unit_label": unit_label,
                "store_qty": store_qty,
                "wh1_qty": wh1_qty,
                "wh2_qty": wh2_qty,
                "other_qty": other_qty,
            }
        )

    # ====== لابل الحالة الحالية (paid / partial / unpaid) ======
    status_code = (pret.status or "").lower()
    try:
        current_status_label = ProviderReturn.Status(status_code).label
    except Exception:
        # fallback لو كان في شيء غريب في الداتابيس
        current_status_label = status_code or "—"

    # ====== السياق للـ template ======
    ctx = {
        "pret": pret,
        "created_status": created_status,
        "created_paid": created_paid,
        "has_credit_now": (getattr(pret, "remaining_syp", DEC0) > 0) or (getattr(pret, "remaining_usd", DEC0) > 0),
        "item_rows": item_rows,
        "source_bill": source_bill,
        "current_status_label": current_status_label,
        "total_syp": getattr(pret, "total_syp", None),
        "total_usd": getattr(pret, "total_usd", None),
        "remaining_syp": getattr(pret, "remaining_syp", None),
        "remaining_usd": getattr(pret, "remaining_usd", None),
        "collected_syp": getattr(pret, "collected_syp", None),
        "collected_usd": getattr(pret, "collected_usd", None),
        "settlement_currency": getattr(pret, "settlement_currency", None),
        "fx_rate_used": getattr(pret, "fx_rate_used", None),
        "valuation_mode": getattr(pret, "valuation_mode", None),
    }
    return render(request, "billing/return_view.html", ctx)


# ---------- Helpers ----------

def _dec(val, default: str = "0") -> Decimal:
    try:
        d = Decimal(str((val if val is not None else default)).replace(",", "."))
        if not d.is_finite():
            raise InvalidOperation
        return d
    except (InvalidOperation, ValueError):
        return Decimal(default)


def _money_has_more_than_2_decimals(value: Decimal) -> bool:
    return Decimal(value).as_tuple().exponent < -2


def _qty_has_more_than_3_decimals(value: Decimal) -> bool:
    return Decimal(value).as_tuple().exponent < -3


def _parse_money_input(val, default: str = "0", *, field_name: str = "amount") -> Decimal:
    amount = _dec(val, default)
    if not amount.is_finite():
        raise ValueError(f"Invalid {field_name}")
    if _money_has_more_than_2_decimals(amount):
        raise ValueError(f"{field_name} supports at most 2 decimal digits")
    return round_money(amount)


def _parse_qty_input(val, default: str = "0", *, field_name: str = "quantity") -> Decimal:
    qty = _dec(val, default)
    if not qty.is_finite():
        raise ValueError(f"Invalid {field_name}")
    if _qty_has_more_than_3_decimals(qty):
        raise ValueError(f"{field_name} supports at most 3 decimal digits")
    return q3(qty)


def _q_money(currency_code: str, amount: Decimal) -> Decimal:
    return FinSV.q_money(amount=Decimal(amount or DEC0), currency_code=(currency_code or "SYP").upper())


def _q_fx(value: Decimal) -> Decimal:
    return FinSV.q_fx(Decimal(value))


def _date(val) -> "date | None":
    return parse_filter_date(val)



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
            raise ValueError(f"الكمية المدخلة للمنتج '{getattr(it, 'product_name_at_txn', '') or it.product.name}' غير صحيحة.")

        if qty <= 0:
            continue  # ignore zeros / negatives

        left_allowed = left_map.get(it.id, DEC0)
        if qty > left_allowed:
            raise ValueError(
                f"الكمية المرتجعة للمنتج '{getattr(it, 'product_name_at_txn', '') or it.product.name}' أكبر من الكمية المتبقية ({left_allowed})."
            )

        field_cost = f"return_cost_{it.id}"
        raw_cost = (request.POST.get(field_cost) or "").strip()
        try:
            cost = Decimal(raw_cost) if raw_cost else (it.cost or Decimal("0"))
        except Exception:
            raise ValueError(f"كلفة المرتجع للمنتج '{getattr(it, 'product_name_at_txn', '') or it.product.name}' غير صحيحة.")

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
    from billing.services_provider import create_provider

    p = create_provider(
        actor=request.user,
        name=name,
        phone=(payload.get("phone") or "").strip(),
        notes=(payload.get("notes") or "").strip(),
    )

    return JsonResponse({"ok": True, "provider": {"id": p.id, "name": p.name}})


@require_POST
@role_required(AccountProfile.Role.MANAGER)
def api_provider_delete(request: HttpRequest, pid: int) -> JsonResponse:
    p = get_object_or_404(Provider, pk=pid)

    # Block deletion if there are any OPEN debtor or creditor entries
    has_open_payables = DebtorEntry.objects.filter(provider=p, status=DebtorEntry.Status.OPEN).exists()
    has_open_receivables = CreditorEntry.objects.filter(provider=p, status=CreditorEntry.Status.OPEN).exists()
    has_open_central_debts = DebtRecord.objects.filter(
        provider=p,
        status=DebtStatus.OPEN,
    ).filter(
        Q(direction=DebtDirection.PAYABLE) | Q(direction=DebtDirection.RECEIVABLE)
    ).exists()
    if has_open_payables or has_open_receivables or has_open_central_debts:
        return _bad("cannot delete: outstanding balances exist")

    if not p.is_active:
        return JsonResponse({"ok": True})
    from billing.services_provider import delete_provider

    delete_provider(
        actor=request.user,
        provider=p,
    )

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
        .filter(is_active=True)
        .select_related("set", "set__collection")
        .prefetch_related("barcodes", "unit_ids")
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
        qs = qs.filter(id=int(q)) if q.isdigit() else qs.none()
    else:
        name_q = Q(name__icontains=q)
        qs = qs.filter(name_q | Q(id=int(q))) if q.isdigit() else qs.filter(name_q)

    qs = qs.order_by("name")[:20]

    items = []
    for p in qs:
        col = getattr(getattr(p, "set", None), "collection", None)
        setobj = getattr(p, "set", None)
        matched_unit = None
        single_unit = bool(getattr(p, "is_single_unit", False))

        if mode == "id":
            for uid in getattr(p, "unit_ids", []).all():
                if (uid.value or "").lower() == q.lower():
                    matched_unit = 1 if single_unit else int(uid.unit_index)
                    break
        elif mode == "barcode":
            for b in getattr(p, "barcodes", []).all():
                val = getattr(b, "barcode", None) or getattr(b, "code", None)
                if (val or "").lower() == q.lower():
                    matched_unit = 1 if single_unit else int(b.unit_index)
                    break
        effective_purchase_currency = p.get_effective_default_purchase_currency() if hasattr(p, "get_effective_default_purchase_currency") else (getattr(p, "default_purchase_currency", None) or "SYP")
        effective_sale_currency = p.get_effective_default_sale_currency() if hasattr(p, "get_effective_default_sale_currency") else (getattr(p, "default_sale_currency", None) or "SYP")
        effective_cost = p.get_default_cost_for_currency(effective_purchase_currency) if hasattr(p, "get_default_cost_for_currency") else Decimal("0")
        effective_price = p.get_default_price_for_currency(effective_sale_currency) if hasattr(p, "get_default_price_for_currency") else Decimal("0")

        items.append({
            "id": p.id,
            "name": p.name,
            "code": p.id,
            "col_name": getattr(col, "name", "") or "",
            "col_code": getattr(col, "code", "") or "",
            "set_name": getattr(setobj, "name", "") or "",
            "set_code": getattr(setobj, "code", "") or "",
            "unit_primary_label": p.get_unit_primary_display() or "الوحدة الأولى",
            "unit_secondary_label": "" if single_unit else ((p.get_unit_secondary_display() if p.unit_secondary else "") or "الوحدة الثانية"),
            "unit_secondary": "" if single_unit else (getattr(p, "unit_secondary", "") or ""),
            "conversion_factor": 1 if single_unit else (getattr(p, "conversion_factor", 0) or 0),
            "price": effective_price,
            "cost": effective_cost,
            "price_syp": getattr(p, "price_syp", None),
            "price_usd": getattr(p, "price_usd", None),
            "cost_syp": getattr(p, "cost_syp", None),
            "cost_usd": getattr(p, "cost_usd", None),
            "default_price_syp": getattr(p, "default_price_syp", None),
            "default_price_usd": getattr(p, "default_price_usd", None),
            "default_cost_syp": getattr(p, "default_cost_syp", None),
            "default_cost_usd": getattr(p, "default_cost_usd", None),
            "enable_syp": bool(getattr(p, "enable_syp", True)),
            "enable_usd": bool(getattr(p, "enable_usd", False)),
            "default_currency": getattr(p, "default_currency", None),
            "allow_syp_purchasing": bool(getattr(p, "allow_syp_purchasing", True)),
            "allow_usd_purchasing": bool(getattr(p, "allow_usd_purchasing", False)),
            "allow_syp_sales": bool(getattr(p, "allow_syp_sales", True)),
            "allow_usd_sales": bool(getattr(p, "allow_usd_sales", False)),
            "default_purchase_currency": getattr(p, "default_purchase_currency", None),
            "default_sale_currency": getattr(p, "default_sale_currency", None),
            "effective_default_purchase_currency": p.get_effective_default_purchase_currency() if hasattr(p, "get_effective_default_purchase_currency") else getattr(p, "default_currency", None),
            "effective_default_sale_currency": p.get_effective_default_sale_currency() if hasattr(p, "get_effective_default_sale_currency") else getattr(p, "default_currency", None),
            "matched_unit": matched_unit,
        })

    return JsonResponse({"ok": True, "items": items})


# ---------- Bills APIs ----------
# - Bills: next serial (preview) -

@require_GET
@role_required(AccountProfile.Role.MANAGER)
def api_bill_next_serial(request: HttpRequest) -> JsonResponse:
    from django.db.models import Max
    m_bill = Bill.objects.aggregate(m=Max("serial"))["m"] or 0
    m_debt = DebtorEntry.objects.aggregate(m=Max("doc_serial"))["m"] or 0
    next_serial = int(max(int(m_bill or 0), int(m_debt or 0))) + 1
    next_public_id = peek_next_public_id(
        sequence_key=BILL_PUBLIC_ID_SEQUENCE_KEY,
        prefix=BILL_PUBLIC_ID_PREFIX,
        model=Bill,
    )
    return JsonResponse(
        {
            "ok": True,
            "next_serial": next_serial,  # legacy compatibility only
            "next_public_id": next_public_id,
        }
    )


@require_GET
@role_required(AccountProfile.Role.MANAGER)
def api_return_next_serial(request: HttpRequest) -> JsonResponse:
    from django.db.models import Max
    m_ret = ProviderReturn.objects.aggregate(m=Max("serial"))["m"] or 0
    m_debt = CreditorEntry.objects.aggregate(m=Max("doc_serial"))["m"] or 0
    next_serial = int(max(int(m_ret or 0), int(m_debt or 0))) + 1
    next_public_id = peek_next_public_id(
        sequence_key=PROVIDER_RETURN_PUBLIC_ID_SEQUENCE_KEY,
        prefix=PROVIDER_RETURN_PUBLIC_ID_PREFIX,
        model=ProviderReturn,
    )
    return JsonResponse(
        {
            "ok": True,
            "next_serial": next_serial,  # legacy compatibility only
            "next_public_id": next_public_id,
            "prefix": PROVIDER_RETURN_PUBLIC_ID_PREFIX,
        }
    )

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
    try:
        provider_id = int(pid)
    except (TypeError, ValueError):
        return _bad("invalid provider", 400)

    # ---- Container handling ----
    # Accept several shapes:
    #   payload["container_code"] = "store"
    #   payload["container"] = {"code": "store"}
    #   payload["container"] = "store"
    container_code = (payload.get("container_code") or "").strip()

    if not container_code:
        c = payload.get("container")
        if isinstance(c, dict):
            container_code = (c.get("code") or "").strip()
        elif isinstance(c, str):
            container_code = c.strip()

    # Default to 'store' if nothing sent
    if not container_code:
        container_code = "store"

    from stock.models import ProductContainer  # local import to avoid touching global imports

    try:
        container = ProductContainer.objects.get(code=container_code)
    except ProductContainer.DoesNotExist:
        return _bad("invalid container", 400)
    
    money_container_id_raw = payload.get("money_container_id")
    money_container_id = None
    if money_container_id_raw not in (None, "", "null"):
        try:
            money_container_id = int(money_container_id_raw)
        except (TypeError, ValueError):
            return _bad("invalid money container", 400)

    settlement_currency = (payload.get("currency_code") or "SYP").strip().upper()
    if settlement_currency not in {"SYP", "USD"}:
        return _bad("invalid settlement currency", 400)

    # ---- Pay section ----
    pay = payload.get("pay") or {}
    status = (pay.get("status") or "unpaid").lower().strip()
    if status not in {"paid", "unpaid", "partial"}:
        return _bad("invalid payment status", 400)
    method = (pay.get("method") or "").lower().strip()
    legacy_pay_shape = (
        ("method" not in pay)
        and ("amount_syp" not in pay)
        and ("amount_usd" not in pay)
    )
    try:
        amount_syp = _parse_money_input(pay.get("amount_syp"), "0", field_name="amount_syp")
        amount_usd = _parse_money_input(pay.get("amount_usd"), "0", field_name="amount_usd")
        paid_amount = _parse_money_input(pay.get("paid_amount"), "0", field_name="paid_amount")
    except ValueError as ve:
        return _bad(str(ve), 400)
    fx_rate_raw = pay.get("fx_rate")
    if fx_rate_raw in (None, ""):
        fx_rate_raw = payload.get("fx_rate")
    fx_rate_snapshot = None
    if fx_rate_raw not in (None, ""):
        fx_rate_snapshot = _q_fx(_dec(fx_rate_raw, "0"))
        if fx_rate_snapshot <= DEC0:
            return _bad("invalid FX rate", 400)

    if paid_amount < DEC0:
        return _bad("paid amount must be >= 0", 400)

    if legacy_pay_shape:
        # Keep backward compatibility for older clients while enforcing unpaid safety.
        if status == "unpaid" and paid_amount != DEC0:
            return _bad("paid amount must be 0 when status is unpaid", 400)
    else:
        if amount_syp < DEC0 or amount_usd < DEC0:
            return _bad("payment amounts must be >= 0", 400)

        if status == "unpaid":
            if method not in {"", "none"}:
                return _bad("payment method is disabled when status is unpaid", 400)
            if amount_syp != DEC0 or amount_usd != DEC0 or paid_amount != DEC0:
                return _bad("payment amounts must be 0 when status is unpaid", 400)
            method = "none"
            paid_amount = DEC0
        else:
            allowed_methods = {"syp_only", "usd_only", "separate", "mixed"}
            if method not in allowed_methods:
                return _bad("invalid payment method", 400)
            if status == "partial" and method == "separate":
                return _bad("separate payment mode is only allowed for full payment", 400)
            if method == "syp_only" and amount_usd != DEC0:
                return _bad("USD amount must be 0 for SYP-only payment mode", 400)
            if method == "usd_only" and amount_syp != DEC0:
                return _bad("SYP amount must be 0 for USD-only payment mode", 400)
            if status == "paid":
                if method in {"syp_only", "usd_only"} and (amount_syp == DEC0 and amount_usd == DEC0):
                    return _bad("full payment requires a valid amount", 400)
                if method == "mixed" and amount_syp == DEC0 and amount_usd == DEC0:
                    return _bad("mixed full payment requires at least one amount", 400)

    from financials.models import MoneyContainerCurrency

    mc = None
    if status in {"paid", "partial"}:
        if not money_container_id:
            return _bad("money container is required when payment exists", 400)
        mc = _resolve_purchase_money_container_for_user(
            user=request.user,
            container_id=money_container_id,
        )
        if not mc:
            return _bad("money container is not allowed for purchase bills", 400)
        required_payment_currencies = set()
        if legacy_pay_shape:
            required_payment_currencies.add(settlement_currency)
        else:
            if amount_syp > DEC0:
                required_payment_currencies.add("SYP")
            if amount_usd > DEC0:
                required_payment_currencies.add("USD")
            if not required_payment_currencies:
                required_payment_currencies.add(settlement_currency)
        for cur_code in sorted(required_payment_currencies):
            if not MoneyContainerCurrency.objects.filter(
                container=mc,
                currency__code=cur_code,
                is_enabled=True,
            ).exists():
                return _bad(f"Currency {cur_code} is disabled for this money container", 400)
    update_defaults = bool(payload.get("update_product_defaults") or False)

    try:
        bill = SV.create_bill(
            actor=request.user,
            provider_id=provider_id,
            status=status,
            paid_amount=paid_amount,
            items=items,
            update_product_defaults=update_defaults,
            container=container,
            money_container_id=money_container_id,
            settlement_currency=settlement_currency,
            fx_usd_syp=fx_rate_snapshot,
            payment_status=status,
            payment_method=(None if legacy_pay_shape else method),
            paid_syp=(None if legacy_pay_shape else amount_syp),
            paid_usd=(None if legacy_pay_shape else amount_usd),
        )
        return JsonResponse({"ok": True, "bill": bill_row(bill)})
    except (ValidationError, ValueError, InvalidOperation) as e:
        msg = "; ".join(e.messages) if getattr(e, "messages", None) else str(e)
        logger.warning("api_bill_save validation failed: %s", msg)
        return _bad(msg or "validation failed", 400)
    except Exception as e:
        logger.exception("api_bill_save failed")
        if settings.DEBUG:
            return _bad(f"save failed: {e}", 500)
        return _bad("save failed", 500)


@require_GET
@role_required(AccountProfile.Role.MANAGER)
def api_bills_list(request: HttpRequest) -> JsonResponse:
    q = (request.GET.get("q") or "").strip()
    bill_public_id = (
        request.GET.get("bill_id")
        or request.GET.get("public_id")
        or ""
    ).strip()

    raw_from = (request.GET.get("date_from") or "").strip()
    raw_to   = (request.GET.get("date_to") or "").strip()
    date_from = _date(raw_from)
    date_to   = _date(raw_to)
    
    status = (request.GET.get("status") or "").lower()  # NOTE: evaluated at Python-level via properties
    status_filter = status if status in {"paid", "unpaid", "partial"} else ""
    cursor = request.GET.get("cursor")

    try:
        page_size = min(max(int(request.GET.get("page_size", "30")), 1), 100)
    except ValueError:
        page_size = 30

    def _attach_debtor_entries(batch: list[Bill]) -> None:
        if not batch:
            return
        bill_ids = [b.id for b in batch]
        debts = DebtorEntry.objects.filter(
            source_app="billing",
            source_model="Bill",
        ).filter(source_identity_lookup_q(source_ids=bill_ids))

        debt_map: dict[int, list[DebtorEntry]] = {}
        for d in debts:
            base = source_identity_numeric_base(
                source_id=d.source_id or "",
                legacy_source_id=getattr(d, "legacy_source_id", "") or "",
            )
            if base is None:
                continue
            debt_map.setdefault(base, []).append(d)

        for b in batch:
            b._debtor_entries_cached = debt_map.get(b.id, [])

    if status_filter:
        try:
            scan_cursor = int(cursor) if cursor not in (None, "") else None
        except ValueError:
            scan_cursor = None

        items: list[Bill] = []
        chunk_size = page_size
        while len(items) < page_size:
            batch_qs = S.bills_list_filters(
                S.bills_base(),
                q,
                bill_public_id,
                status_filter,
                date_from,
                date_to,
                scan_cursor,
                page_size,
            )
            batch = list(batch_qs.order_by("-id")[:chunk_size])
            if not batch:
                break
            _attach_debtor_entries(batch)
            for b in batch:
                if (b.status or "").lower() == status_filter:
                    items.append(b)
                    if len(items) >= page_size:
                        break
            scan_cursor = batch[-1].id
            if len(batch) < chunk_size:
                break
    else:
        qs = S.bills_list_filters(
            S.bills_base(),
            q,
            bill_public_id,
            status_filter,
            date_from,
            date_to,
            cursor,
            page_size,
        )
        items = list(qs.order_by("-id")[:page_size])
        _attach_debtor_entries(items)

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
def api_bill_delete(request: HttpRequest, bill_id: str) -> JsonResponse:
    bill = _resolve_bill_from_ref(ref=bill_id, for_update=True)
    if bill is None:
        return _bad("not found", 404)
    try:
        SV.delete_bill(actor=request.user, bill_id=bill.id)
        return JsonResponse({"ok": True})
    except Bill.DoesNotExist:
        return _bad("not found", 404)
    except ValidationError as ve:
        msg = ve.messages[0] if getattr(ve, "messages", None) else str(ve)
        return _bad(msg, 409)
    except ValueError as ve:
        return _bad(str(ve), 409)
    except Exception:
        return _bad("delete failed", 500)


@role_required(AccountProfile.Role.MANAGER)
@login_required
def bill_view(request, bill_id: str):
    bill_ref = _resolve_bill_from_ref(ref=bill_id)
    if bill_ref is None:
        return HttpResponse(status=404)
    bill = get_object_or_404(
        Bill.objects.prefetch_related(
            "items__product",
            "provider",
            "items__product__barcodes",
            "items__product__unit_ids",
        ),
        pk=bill_ref.id,
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

        item_ids_str = [str(i) for i in item_ids]

        mv_rows = (
            ProductMovement.objects
            .filter(
                source_app="billing",
                source_model="BillItem",
                source_id__in=item_ids_str,
            )
            .select_related("container")
            .order_by("id")
        )
        for mv in mv_rows:
            iid = int(mv.source_id)
            if iid not in origin_by_item and mv.container_id:
                origin_by_item[iid] = mv

    storage_locations: list[str] = []
    seen_container_ids: set[int] = set()
    for mv in origin_by_item.values():
        cont = getattr(mv, "container", None)
        if not cont:
            continue
        cid = getattr(cont, "id", None)
        if cid in seen_container_ids:
            continue
        if cid is not None:
            seen_container_ids.add(cid)
        label = (
            getattr(cont, "display_label", None)
            or getattr(cont, "name", None)
            or getattr(cont, "code", "")
        )
        if label:
            storage_locations.append(str(label))
    storage_location_label = "، ".join(storage_locations) if storage_locations else "—"

    error_msg = None
    from collections import defaultdict
    from inventory.models import SaleCostPart

    sold_by_item: dict[int, Decimal] = defaultdict(lambda: DEC0)

    if item_ids:
        item_ids_str = [str(i) for i in item_ids]

        sold_rows = (
            SaleCostPart.objects
            .filter(
                fifo_layer__source_app="billing",
                fifo_layer__source_model="BillItem",
                fifo_layer__source_id__in=item_ids_str,
            )
            .values("fifo_layer__source_id")
            .annotate(q=Sum("qty_primary"))
        )

        for r in sold_rows:
            try:
                iid = int(r["fifo_layer__source_id"] or "0")
            except (TypeError, ValueError):
                continue
            sold_by_item[iid] = q3(r["q"] or DEC0)

    # NOTE: old inline-return POST kept as-is (no form now, so practically unused)
    if request.method == "POST":
        error_msg = "هذه الصفحة تستخدم الآن معالج المرتجعات الجديد."

    # ===== Build rows for template =====
    items_rows = []
    for it in item_qs:
        prod = it.product

        unit1_label = (getattr(it, "unit_1_label_at_txn", "") or "").strip()
        unit2_label = (getattr(it, "unit_2_label_at_txn", "") or "").strip()
        if not unit1_label:
            unit1_label = "—"

        qty_primary = q3(it.qty_primary or DEC0)
        cf = getattr(it, "conv_factor_at_txn", None)
        try:
            cf_val = Decimal(str(cf)) if cf else None
        except Exception:
            cf_val = None
        if not cf_val or cf_val <= 0:
            cf_val = Decimal("1")

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
        total_remaining = q3(store_qty + wh1_qty + wh2_qty)

        # left_qty already reflects sales + provider returns (both consume FIFO)
        sold_qty = q3(sold_by_item.get(it.id, DEC0))

        # product identifiers for search
        prod_code = str(getattr(it, "product_id", "") or "")
        barcode_vals: list[str] = []
        try:
            seen_barcodes: set[str] = set()
            for b in getattr(prod, "barcodes", []).all():
                val = getattr(b, "barcode", None) or getattr(b, "code", None)
                if val:
                    sval = str(val).strip()
                    if sval and sval not in seen_barcodes:
                        seen_barcodes.add(sval)
                        barcode_vals.append(sval)
        except Exception:
            barcode_vals = []

        unit_code_vals: list[str] = []
        try:
            seen_unit_codes: set[str] = set()
            for uid in getattr(prod, "unit_ids", []).all():
                val = getattr(uid, "value", "") or ""
                if val:
                    sval = str(val).strip()
                    if sval and sval not in seen_unit_codes:
                        seen_unit_codes.add(sval)
                        unit_code_vals.append(sval)
        except Exception:
            unit_code_vals = []

        items_rows.append(
            {
                "item_id": it.id,
                "product_name": ((getattr(it, "product_name_at_txn", "") or "").strip() or "—"),
                "unit1_label": unit1_label,
                "unit2_label": unit2_label,
                "cost": it.cost,
                "currency": (getattr(it, "currency", None) or "SYP").upper(),
                "qty_u1": qty_u1,
                "qty_u1_ui": _ui_2dp(qty_u1),
                "qty_u1_str": format_quantity(qty_u1, unit1_label),
                "qty_u2": qty_u2,
                "qty_u2_ui": _ui_2dp(qty_u2),
                "qty_u2_str": format_quantity(qty_u2, unit2_label) if unit2_label else "",
                "highlight_unit": highlight_unit,
                "line_total": it.line_total,
                "left_qty": left_qty,
                "left_qty_str": format_quantity(left_qty, unit1_label),
                "returned_qty": total_returned,
                "returned_qty_ui": _ui_2dp(total_returned),
                "returned_qty_str": format_quantity(total_returned, unit1_label),
                "has_returns": has_returns,
                "can_return": left_qty > DEC0,
                "store_qty": store_qty,
                "store_qty_ui": _ui_2dp(store_qty),
                "store_qty_str": format_quantity(store_qty, unit1_label),
                "wh1_qty": wh1_qty,
                "wh1_qty_ui": _ui_2dp(wh1_qty),
                "wh1_qty_str": format_quantity(wh1_qty, unit1_label),
                "wh2_qty": wh2_qty,
                "wh2_qty_ui": _ui_2dp(wh2_qty),
                "wh2_qty_str": format_quantity(wh2_qty, unit1_label),
                "total_remaining": total_remaining,
                "total_remaining_ui": _ui_2dp(total_remaining),
                "total_remaining_str": format_quantity(total_remaining, unit1_label),
                "sold_qty": sold_qty,
                "sold_qty_ui": _ui_2dp(sold_qty),
                "sold_qty_str": format_quantity(sold_qty, unit1_label),
                # for search
                "product_id": prod_code,
                "code": prod_code,
                "barcode": (barcode_vals[0] if barcode_vals else ""),
                "unit_id": (unit_code_vals[0] if unit_code_vals else ""),
                "barcodes": barcode_vals,
                "unit_codes": unit_code_vals,
                "barcodes_join": "||".join(barcode_vals),
                "unit_codes_join": "||".join(unit_code_vals),
            }
        )

    has_debt_now = False
    bill_debt_view_url = ""
    try:
        if bill.serial:
            has_legacy_debt = DebtorEntry.objects.filter(
                source_app="billing",
                source_model="Bill",
                source_id=str(bill.id),
            ).exists()
            cause_refs: list[str] = [str(bill.id)]
            bill_public_ref = (getattr(bill, "public_id", "") or "").strip()
            if bill_public_ref:
                cause_refs.insert(0, bill_public_ref)
            central_debt = (
                DebtRecord.objects
                .filter(
                    direction=DebtDirection.PAYABLE,
                    cause_type=DebtCauseType.PURCHASE_BILL,
                    cause_id__in=cause_refs,
                )
                .only("public_id", "status")
                .order_by("id")
                .first()
            )
            has_central_debt = bool(
                central_debt and central_debt.status == DebtStatus.OPEN
            )
            if central_debt and (central_debt.public_id or "").strip():
                bill_debt_view_url = reverse(
                    "debts_view_central_debt",
                    kwargs={"debt_ref": central_debt.public_id},
                )
            has_debt_now = has_legacy_debt or has_central_debt

    except Exception:
        has_debt_now = False
        bill_debt_view_url = ""

    # ===== Read-only payment snapshot at creation time =====
    bill_total_syp = q4(getattr(bill, "total_syp", DEC0) or DEC0)
    bill_total_usd = q4(getattr(bill, "total_usd", DEC0) or DEC0)
    bill_fx_rate = getattr(bill, "fx_rate_usd_to_syp_used", None) or getattr(bill, "fx_usd_syp", None) or DEC0
    try:
        bill_fx_rate = Decimal(str(bill_fx_rate))
    except Exception:
        bill_fx_rate = DEC0
    bill_fx_rate = q4(bill_fx_rate)

    creation_status_raw = (getattr(bill, "creation_payment_status", None) or "").lower().strip()
    creation_method_raw = (getattr(bill, "creation_payment_method", None) or "").lower().strip()
    has_creation_snapshot = bool(
        creation_status_raw in {"paid", "partial", "unpaid"}
        and creation_method_raw in {"none", "syp_only", "usd_only", "separate", "mixed"}
    )

    created_paid_syp = DEC0
    created_paid_usd = DEC0
    created_payment_status_code = "unknown"
    created_payment_method_code = "UNKNOWN"

    if has_creation_snapshot:
        created_paid_syp = q4(getattr(bill, "creation_paid_syp", DEC0) or DEC0)
        created_paid_usd = q4(getattr(bill, "creation_paid_usd", DEC0) or DEC0)
        created_payment_status_code = {
            "paid": "fully_paid",
            "partial": "partially_paid",
            "unpaid": "not_paid",
        }.get(creation_status_raw, "not_paid")
        created_payment_method_code = {
            "syp_only": "SYP_ONLY",
            "usd_only": "USD_ONLY",
            "separate": "SEPARATE",
            "mixed": "MIXED",
            "none": "NONE",
            "": "NONE",
        }.get(creation_method_raw, "NONE")
    else:
        created_paid_syp = DEC0
        created_paid_usd = DEC0

    created_payment_status_label = {
        "fully_paid": "مدفوعة بالكامل",
        "partially_paid": "مدفوعة جزئياً",
        "not_paid": "غير مدفوعة",
        "unknown": "غير معروف (سجل قديم)",
    }.get(created_payment_status_code, "غير مدفوعة")

    created_payment_method_label = {
        "SYP_ONLY": "تم الدفع بالليرة السورية فقط",
        "USD_ONLY": "تم الدفع بالدولار فقط",
        "SEPARATE": "تم الدفع بعملتين منفصلتين",
        "MIXED": "تم الدفع بشكل مختلط",
        "NONE": "بدون دفع عند الإنشاء",
        "UNKNOWN": "غير معروف (سجل قديم)",
    }.get(created_payment_method_code, "تم الدفع بالليرة السورية فقط")

    # ===== Current debt/payment state (debt-aware if central debt exists) =====
    current_remaining_syp = q4(getattr(bill, "remaining_syp", DEC0) or DEC0)
    current_remaining_usd = q4(getattr(bill, "remaining_usd", DEC0) or DEC0)
    current_paid_syp = q4(getattr(bill, "paid_syp", DEC0) or DEC0)
    current_paid_usd = q4(getattr(bill, "paid_usd", DEC0) or DEC0)

    if current_remaining_syp <= DEC0 and current_remaining_usd <= DEC0:
        current_payment_status_code = "fully_paid"
    elif current_paid_syp > DEC0 or current_paid_usd > DEC0:
        current_payment_status_code = "partially_paid"
    else:
        current_payment_status_code = "not_paid"

    current_state_is_fully_paid = current_payment_status_code == "fully_paid"

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
        "bill_debt_view_url": bill_debt_view_url,
        "error_msg": error_msg,
        "selected_items": selected_items,
        "initial_status": initial_status,
        "storage_location_label": storage_location_label,
        "bill_total_syp_ui": _ui_2dp(bill_total_syp),
        "bill_total_usd_ui": _ui_2dp(bill_total_usd),
        "bill_fx_rate_ui": _ui_2dp(bill_fx_rate),
        "created_paid_syp_ui": _ui_2dp(created_paid_syp),
        "created_paid_usd_ui": _ui_2dp(created_paid_usd),
        "created_payment_status_code": created_payment_status_code,
        "created_payment_status_label": created_payment_status_label,
        "created_payment_method_code": created_payment_method_code,
        "created_payment_method_label": created_payment_method_label,
        "current_payment_status_code": current_payment_status_code,
        "current_state_is_fully_paid": current_state_is_fully_paid,
        "current_remaining_syp_ui": _ui_2dp(current_remaining_syp),
        "current_remaining_usd_ui": _ui_2dp(current_remaining_usd),
    }
    return render(request, "billing/bill_view.html", ctx)




@role_required(AccountProfile.Role.MANAGER)
@login_required
def bill_return_wizard(request: HttpRequest, bill_id: str) -> HttpResponse:
    """
    Second page: choose per-container returned qty + cost for selected items
    from a purchase bill.
    """
    bill_ref = _resolve_bill_from_ref(ref=bill_id)
    if bill_ref is None:
        return HttpResponse(status=404)
    bill = get_object_or_404(
        Bill.objects.prefetch_related("items__product", "provider"),
        pk=bill_ref.id,
    )

    money_containers = (
        FinSV.money_containers_for_user_qs(
            user=request.user,
            feature_code=PROVIDER_RETURNS_FEATURE_CODE,
        )
        .order_by("id")
    )
    money_container_ids = list(money_containers.values_list("id", flat=True))
    settlement_eligible_container_ids = set(
        MoneyContainer.objects.filter(
            id__in=money_container_ids,
            features__code=PURCHASE_BILLS_FEATURE_CODE,
            features__is_active=True,
        ).values_list("id", flat=True)
    )

    current_fx: Decimal | None
    try:
        fx_raw = FinSV.get_current_fx_syp_per_usd()
        fx_q = _q_fx(_dec(str(fx_raw), "0"))
        current_fx = fx_q if fx_q > DEC0 else None
    except Exception:
        current_fx = None

    source_bill_debt = (
        DebtRecord.objects
        .filter(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id__in=[(bill.public_id or "").strip(), str(bill.id)],
            status=DebtStatus.OPEN,
        )
        .only("id", "public_id", "remaining_syp", "remaining_usd")
        .order_by("id")
        .first()
    )
    source_debt_remaining_syp = _q_money(
        "SYP",
        getattr(source_bill_debt, "remaining_syp", DEC0) or DEC0,
    )
    source_debt_remaining_usd = _q_money(
        "USD",
        getattr(source_bill_debt, "remaining_usd", DEC0) or DEC0,
    )
    has_source_bill_payable_debt = bool(
        source_bill_debt
        and (source_debt_remaining_syp > DEC0 or source_debt_remaining_usd > DEC0)
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
        return redirect("billing_bill_view", bill_id=bill.public_id)

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
    items_by_id = {it.id: it for it in item_qs}

    if not item_qs:
        return redirect("billing_bill_view", bill_id=bill.public_id)

    # ===== build simple rows =====
    rows = []
    for it in item_qs:
        prod = it.product
        if not prod:
            continue

        unit1_label = (getattr(it, "unit_1_label_at_txn", "") or "").strip() or "—"

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
                "product_name": ((getattr(it, "product_name_at_txn", "") or "").strip() or "—"),
                "unit1_label": unit1_label,
                "cost": it.cost,
                "currency": getattr(it, "currency", None) or "SYP",
                "ret_cost_raw": "",
                "ret_currency_raw": "",
                "left_qty": left_qty,
                "left_qty_str": format_quantity(left_qty, unit1_label),
                "store_qty": store_qty,
                "store_qty_str": format_quantity(store_qty, unit1_label),
                "wh1_qty": wh1_qty,
                "wh1_qty_str": format_quantity(wh1_qty, unit1_label),
                "wh2_qty": wh2_qty,
                "wh2_qty_str": format_quantity(wh2_qty, unit1_label),
            }
        )
#=========================from here ================================================================
    #<-- this vertical 
    error_msg: str | None = None
    total_return_syp = DEC0
    total_return_usd = DEC0
    total_return_settlement = DEC0

    # keep user selections to re-fill on validation errors
    if request.method == "POST":
        return_status_selected = (request.POST.get("return_status") or "").lower().strip()
        return_paid_amount_raw = (request.POST.get("return_paid_amount") or "").strip()
        return_payment_method_selected = (request.POST.get("return_payment_method") or "").lower().strip()
        return_pay_syp_raw = (request.POST.get("return_pay_syp") or "").strip()
        return_pay_usd_raw = (request.POST.get("return_pay_usd") or "").strip()
        settlement_currency_selected = (request.POST.get("settlement_currency") or getattr(bill, "settlement_currency", "SYP")).upper().strip()
        money_container_id_raw = (request.POST.get("money_container_id") or "").strip()
        settle_purchase_debt_enabled = (request.POST.get("settle_purchase_debt") in {"1", "true", "on", "yes"})
        debt_settlement_amount_raw = (request.POST.get("debt_settlement_amount") or "").strip()
    else:
        return_status_selected = "unpaid"
        return_paid_amount_raw = "0"
        return_payment_method_selected = "syp_only"
        return_pay_syp_raw = ""
        return_pay_usd_raw = ""
        settlement_currency_selected = (getattr(bill, "settlement_currency", "SYP") or "SYP").upper()
        money_container_id_raw = ""
        settle_purchase_debt_enabled = False
        debt_settlement_amount_raw = ""

    if request.method == "POST":
        # 1) copy all raw inputs from POST into rows so we can re-render them on error
        for r in rows:
            iid = r["item_id"]
            key_store = f"ret_store_{iid}"
            key_wh1 = f"ret_wh1_{iid}"
            key_wh2 = f"ret_wh2_{iid}"
            key_cost = f"ret_cost_{iid}"
            key_currency = f"ret_currency_{iid}"

            r["ret_store_raw"] = (request.POST.get(key_store) or "").strip()
            r["ret_wh1_raw"] = (request.POST.get(key_wh1) or "").strip()
            r["ret_wh2_raw"] = (request.POST.get(key_wh2) or "").strip()
            r["ret_cost_raw"] = (request.POST.get(key_cost) or "").strip()
            r["ret_currency_raw"] = (request.POST.get(key_currency) or "").strip().upper()

        # 2) build payload + validate
        items_payload: list[dict[str, Any]] = []
        total_return_syp = DEC0
        total_return_usd = DEC0
        total_return_settlement = DEC0

        try:
            if settlement_currency_selected not in {"SYP", "USD"}:
                raise ValueError("Invalid settlement currency.")

            for r in rows:
                iid = r["item_id"]
                it = items_by_id[iid]
                prod = it.product

                # use the raw values we already copied into row
                q_store = _parse_qty_input(
                    r.get("ret_store_raw"),
                    "0",
                    field_name=f"store return quantity for '{prod.name}'",
                )
                q_wh1 = _parse_qty_input(
                    r.get("ret_wh1_raw"),
                    "0",
                    field_name=f"warehouse 1 return quantity for '{prod.name}'",
                )
                q_wh2 = _parse_qty_input(
                    r.get("ret_wh2_raw"),
                    "0",
                    field_name=f"warehouse 2 return quantity for '{prod.name}'",
                )

                if q_store < DEC0 or q_wh1 < DEC0 or q_wh2 < DEC0:
                    raise ValueError("Invalid negative return quantity.")

                qty_total = q3(q_store + q_wh1 + q_wh2)
                if qty_total <= DEC0:
                    # no return for this row, skip
                    continue

                # check against available per container
                if q_store > r["store_qty"]:
                    raise ValueError(f"Return qty exceeds available store qty for '{prod.name}'.")
                if q_wh1 > r["wh1_qty"]:
                    raise ValueError(f"Return qty exceeds available WH1 qty for '{prod.name}'.")
                if q_wh2 > r["wh2_qty"]:
                    raise ValueError(f"Return qty exceeds available WH2 qty for '{prod.name}'.")

                # also total vs left
                if qty_total > r["left_qty"]:
                    raise ValueError(f"Return qty exceeds remaining FIFO qty for '{prod.name}'.")

                purchase_currency = (getattr(it, "currency", None) or "SYP").upper()
                purchase_cost = q4(Decimal(str(it.cost or "0")))

                raw_currency = (r.get("ret_currency_raw") or "").strip().upper()
                item_currency = raw_currency or purchase_currency
                if item_currency not in {"SYP", "USD"}:
                    raise ValueError(f"Invalid return currency for '{prod.name}'.")

                raw_cost = (r.get("ret_cost_raw") or "").strip()
                if raw_cost:
                    cost = _parse_money_input(
                        raw_cost,
                        "0",
                        field_name=f"return cost for '{prod.name}'",
                    )
                else:
                    cost = purchase_cost
                if cost < DEC0:
                    raise ValueError(f"Invalid negative return cost for '{prod.name}'.")

                line_total_raw = round_money(cost * q3(qty_total))
                line_total = _q_money(item_currency, line_total_raw)
                if item_currency == "USD":
                    total_return_usd = _q_money("USD", total_return_usd + line_total)
                else:
                    total_return_syp = _q_money("SYP", total_return_syp + line_total)

                container_splits: list[dict[str, str]] = []
                if q_store > DEC0:
                    container_splits.append({"code": "store", "qty_primary": str(q_store)})
                if q_wh1 > DEC0:
                    container_splits.append({"code": "wh1", "qty_primary": str(q_wh1)})
                if q_wh2 > DEC0:
                    container_splits.append({"code": "wh2", "qty_primary": str(q_wh2)})

                items_payload.append(
                    {
                        "bill_item_id": it.id,
                        "product_id": it.product_id,
                        "unit_index": 1,
                        "qty_primary": str(qty_total),
                        "cost": str(cost),
                        "currency": item_currency,
                        "container_splits": container_splits,
                    }
                )

            if not items_payload:
                raise ValueError("No return items were selected.")

            fx_rate_used = current_fx
            if settlement_currency_selected == "SYP":
                settlement_total_raw = total_return_syp
                if total_return_usd > DEC0:
                    if fx_rate_used is None or fx_rate_used <= DEC0:
                        raise ValueError("FX rate is required to settle this return.")
                    settlement_total_raw = settlement_total_raw + (total_return_usd * fx_rate_used)
                total_return_settlement = _q_money("SYP", settlement_total_raw)
            else:
                settlement_total_raw = total_return_usd
                if total_return_syp > DEC0:
                    if fx_rate_used is None or fx_rate_used <= DEC0:
                        raise ValueError("FX rate is required to settle this return.")
                    settlement_total_raw = settlement_total_raw + (total_return_syp / fx_rate_used)
                total_return_settlement = _q_money("USD", settlement_total_raw)

            # 3) payment + optional debt settlement parsing (validated canonically in services)
            paid_amount_legacy = _parse_money_input(
                return_paid_amount_raw or "0",
                "0",
                field_name="return_paid_amount",
            )
            if paid_amount_legacy < DEC0:
                raise ValueError("return_paid_amount must be >= 0")
            paid_amount_legacy = _q_money(settlement_currency_selected, paid_amount_legacy)

            paid_syp = None
            if return_pay_syp_raw not in {"", None}:
                paid_syp = _parse_money_input(
                    return_pay_syp_raw,
                    "0",
                    field_name="return_pay_syp",
                )
                if paid_syp < DEC0:
                    raise ValueError("return_pay_syp must be >= 0")
                paid_syp = _q_money("SYP", paid_syp)

            paid_usd = None
            if return_pay_usd_raw not in {"", None}:
                paid_usd = _parse_money_input(
                    return_pay_usd_raw,
                    "0",
                    field_name="return_pay_usd",
                )
                if paid_usd < DEC0:
                    raise ValueError("return_pay_usd must be >= 0")
                paid_usd = _q_money("USD", paid_usd)

            debt_settlement_amount = None
            if debt_settlement_amount_raw not in {"", None}:
                debt_settlement_amount = _parse_money_input(
                    debt_settlement_amount_raw,
                    "0",
                    field_name="debt_settlement_amount",
                )
                if debt_settlement_amount < DEC0:
                    raise ValueError("debt_settlement_amount must be >= 0")
                debt_settlement_amount = _q_money(settlement_currency_selected, debt_settlement_amount)

            request_idempotency_key = SV.build_provider_return_idempotency_key(
                provider_id=bill.provider_id,
                source_bill_serial=bill.serial,
                source_bill_public_id=bill.public_id,
                status=return_status_selected or "unpaid",
                settlement_currency=settlement_currency_selected,
                valuation_mode="CURRENT_FX",
                payment_method=(return_payment_method_selected or None),
                paid_amount=paid_amount_legacy,
                paid_syp=paid_syp,
                paid_usd=paid_usd,
                settle_purchase_debt=bool(settle_purchase_debt_enabled),
                debt_settlement_amount=debt_settlement_amount,
                money_container_id=(
                    int(money_container_id_raw)
                    if money_container_id_raw
                    else None
                ),
                items=items_payload,
            )

            pret = SV.create_return(
                actor=request.user,
                provider_id=bill.provider_id,
                status=return_status_selected or "unpaid",
                paid_amount=paid_amount_legacy,
                items=items_payload,
                container=None,  # using per-item container_splits
                source_bill_serial=bill.serial,
                source_bill_public_id=bill.public_id,
                money_container_id=(
                    int(money_container_id_raw)
                    if money_container_id_raw
                    else None
                ),
                currency_code=settlement_currency_selected,
                valuation_mode="CURRENT_FX",
                payment_method=(return_payment_method_selected or None),
                paid_syp=paid_syp,
                paid_usd=paid_usd,
                settle_purchase_debt=bool(settle_purchase_debt_enabled),
                debt_settlement_amount=debt_settlement_amount,
                idempotency_key=request_idempotency_key,
            )

            return redirect("billing_returns_list")

        except ValidationError as ve:
            msg = "; ".join(getattr(ve, "messages", []) or [])
            error_msg = msg or str(ve)
        except ValueError as ve:
            error_msg = str(ve)
        except Exception:
            logger.exception("bill_return_wizard: failed to create ProviderReturn")
            error_msg = "فشل حفظ المرتجع، حدث خطأ غير متوقع."


    selected_ids_str = ",".join(str(r["item_id"]) for r in rows)

    return_public_id_preview = ""
    try:
        return_public_id_preview = peek_next_public_id(
            sequence_key=PROVIDER_RETURN_PUBLIC_ID_SEQUENCE_KEY,
            prefix=PROVIDER_RETURN_PUBLIC_ID_PREFIX,
            model=ProviderReturn,
        )
    except Exception:
        return_public_id_preview = ""

    ctx = {
        "bill": bill,
        "rows": rows,
        "error_msg": error_msg,
        "items_ids": selected_ids_str,
        "return_status_selected": return_status_selected,
        "return_payment_method_selected": return_payment_method_selected,
        "return_pay_syp_raw": return_pay_syp_raw,
        "return_pay_usd_raw": return_pay_usd_raw,
        "return_paid_amount_raw": return_paid_amount_raw,
        "settlement_currency_selected": settlement_currency_selected,
        "money_container_id_raw": money_container_id_raw,
        "settle_purchase_debt_enabled": settle_purchase_debt_enabled,
        "debt_settlement_amount_raw": debt_settlement_amount_raw,
        "total_return_syp": total_return_syp,
        "total_return_usd": total_return_usd,
        "total_return_settlement": total_return_settlement,
        "has_source_bill_payable_debt": has_source_bill_payable_debt,
        "source_debt_public_id": (getattr(source_bill_debt, "public_id", "") or ""),
        "source_debt_remaining_syp": source_debt_remaining_syp,
        "source_debt_remaining_usd": source_debt_remaining_usd,
        "fx_rate_used": current_fx,
        "money_containers": money_containers,
        "settlement_eligible_container_ids": settlement_eligible_container_ids,
        "return_public_id_preview": return_public_id_preview,
    }

    return render(request, "billing/bill_return_wizard.html", ctx)




# ---------- Payments (payables) ----------

@require_POST
@role_required(AccountProfile.Role.MANAGER)
def pay_debt_full(request: HttpRequest, bill_id: str) -> JsonResponse:
    bill = _resolve_bill_from_ref(ref=bill_id, for_update=True)
    if bill is None:
        return _bad("not found", 404)
    try:
        money_container_id = request.POST.get("money_container_id")
        currency_code = (request.POST.get("currency_code") or "SYP").strip().upper()
        SV.pay_full(
            actor=request.user,
            bill_id=bill.id,
            money_container_id=int(money_container_id) if money_container_id else None,
            currency_code=currency_code,
        )
        return JsonResponse({"ok": True, "remaining": "0"})
    except Bill.DoesNotExist:
        return _bad("not found", 404)
    except ValueError as ve:
        return _bad(str(ve), 400)


@require_POST
@role_required(AccountProfile.Role.MANAGER)
def pay_debt_batch(request: HttpRequest, bill_id: str) -> JsonResponse:
    amount_raw = (request.POST.get("amount") or "").strip()
    try:
        amount = _parse_money_input(amount_raw, "0", field_name="amount")
    except ValueError:
        return _bad("Enter a positive amount.")
    if amount <= 0:
        return _bad("Enter a positive amount.")
    bill = _resolve_bill_from_ref(ref=bill_id, for_update=True)
    if bill is None:
        return _bad("not found", 404)
    try:
        money_container_id = request.POST.get("money_container_id")
        currency_code = (request.POST.get("currency_code") or "SYP").strip().upper()
        bill_obj = SV.pay_partial(
            actor=request.user,
            bill_id=bill.id,
            amount=amount,
            money_container_id=int(money_container_id) if money_container_id else None,
            currency_code=currency_code,
        )
        return JsonResponse({"ok": True, "remaining": str(bill_obj.remaining)})
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
    return_public_id = (
        request.GET.get("return_id")
        or request.GET.get("id")
        or request.GET.get("rid")
        or ""
    ).strip()
    source_bill_public_id = (
        request.GET.get("bill_id")
        or request.GET.get("source_bill_id")
        or request.GET.get("bill_public_id")
        or ""
    ).strip()
    raw_from = (request.GET.get("date_from") or "").strip()
    raw_to   = (request.GET.get("date_to") or "").strip()
    date_from = _date(raw_from)
    date_to   = _date(raw_to)

    status = (request.GET.get("status") or "").lower()  # property-based
    status_filter = status if status in {"paid", "partial", "unpaid"} else ""
    cursor = request.GET.get("cursor")
    try:
        page_size = min(max(int(request.GET.get("page_size", "30")), 1), 100)
    except ValueError:
        page_size = 30

    def _attach_creditor_entries(batch: list[ProviderReturn]) -> None:
        if not batch:
            return
        ret_ids = [r.id for r in batch]
        debts = CreditorEntry.objects.filter(
            source_app="billing",
            source_model="ProviderReturn",
        ).filter(source_identity_lookup_q(source_ids=ret_ids))

        debt_map: dict[int, list[CreditorEntry]] = {}
        for d in debts:
            base = source_identity_numeric_base(
                source_id=d.source_id or "",
                legacy_source_id=getattr(d, "legacy_source_id", "") or "",
            )
            if base is None:
                continue
            debt_map.setdefault(base, []).append(d)

        for r in batch:
            r._creditor_entries_cached = debt_map.get(r.id, [])

    if status_filter:
        try:
            scan_cursor = int(cursor) if cursor not in (None, "") else None
        except ValueError:
            scan_cursor = None

        items: list[ProviderReturn] = []
        chunk_size = page_size
        while len(items) < page_size:
            batch = list(
                S.returns_list_filters(
                    q,
                    return_public_id,
                    source_bill_public_id,
                    status_filter,
                    date_from,
                    date_to,
                    scan_cursor,
                    chunk_size,
                )
            )
            if not batch:
                break
            _attach_creditor_entries(batch)
            for r in batch:
                if (r.status or "").lower() == status_filter:
                    items.append(r)
                    if len(items) >= page_size:
                        break
            scan_cursor = batch[-1].id
            if len(batch) < chunk_size:
                break
    else:
        items = list(
            S.returns_list_filters(
                q,
                return_public_id,
                source_bill_public_id,
                status_filter,
                date_from,
                date_to,
                cursor,
                page_size,
            )
        )
        _attach_creditor_entries(items)

    nxt = items[-1].id if items else None
    return JsonResponse({"ok": True, "items": [return_row(r) for r in items], "next_cursor": nxt})


# Payments (collections) for provider debts-to-store:

@require_POST
@role_required(AccountProfile.Role.MANAGER)
def collect_return_full(request: HttpRequest, ret_id: str) -> JsonResponse:
    pret = _resolve_provider_return_from_ref(ref=ret_id, for_update=True)
    if pret is None:
        return _bad("not found", 404)
    try:
        money_container_id = request.POST.get("money_container_id")
        currency_code = (request.POST.get("currency_code") or "SYP").strip().upper()
        SV.collect_full(
            actor=request.user,
            return_id=pret.id,
            money_container_id=int(money_container_id) if money_container_id else None,
            currency_code=currency_code,
        )
        return JsonResponse({"ok": True, "remaining": "0"})
    except ProviderReturn.DoesNotExist:
        return _bad("not found", 404)
    except ValueError as ve:
        return _bad(str(ve), 400)


@require_POST
@role_required(AccountProfile.Role.MANAGER)
def collect_return_batch(request: HttpRequest, ret_id: str) -> JsonResponse:
    amount_raw = (request.POST.get("amount") or "").strip()
    try:
        amount = _parse_money_input(amount_raw, "0", field_name="amount")
    except ValueError:
        return _bad("Enter a positive amount.")
    if amount <= 0:
        return _bad("Enter a positive amount.")
    pret_ref = _resolve_provider_return_from_ref(ref=ret_id, for_update=True)
    if pret_ref is None:
        return _bad("not found", 404)
    try:
        money_container_id = request.POST.get("money_container_id")
        currency_code = (request.POST.get("currency_code") or "SYP").strip().upper()
        pret = SV.collect_partial(
            actor=request.user,
            return_id=pret_ref.id,
            amount=amount,
            money_container_id=int(money_container_id) if money_container_id else None,
            currency_code=currency_code,
        )
        return JsonResponse({"ok": True, "remaining": str(pret.remaining)})
    except ValueError as ve:
        return _bad(str(ve))
    except ProviderReturn.DoesNotExist:
        return _bad("not found", 404)
