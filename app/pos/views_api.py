from __future__ import annotations
from decimal import Decimal
from typing import List, Dict, Any, Optional

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpRequest
from django.views.decorators.http import require_GET

from catalog.models import Product, ProductBarcode, ProductUnitId, UnitType

def unit_label(code: str) -> str:
    return dict(UnitType.choices).get(code, code)

def product_payload(p: Product) -> Dict[str, Any]:
    effective_sale_currency = p.get_effective_default_sale_currency()
    if effective_sale_currency == "USD" and (p.default_price_usd or 0) <= 0 and p.allow_syp_sales:
        effective_sale_currency = "SYP"
    default_price = p.get_default_price_for_currency(effective_sale_currency)
    data = {
        "id": p.id,
        "name": p.name,
        "number": p.display_code,  # zero-padded string
        "price": str(default_price),  # effective default sale price per primary unit
        "default_price_syp": str(p.default_price_syp),
        "default_price_usd": str(p.default_price_usd),
        "allow_syp_sales": bool(p.allow_syp_sales),
        "allow_usd_sales": bool(p.allow_usd_sales),
        "effective_default_sale_currency": effective_sale_currency,
        "units": {
            "primary": {"code": p.unit_primary, "label": unit_label(p.unit_primary)},
            "secondary": None,
            "conversion_factor": None,
        },
        "notes": p.notes or "",
    }
    if p.unit_secondary and not p.is_single_unit:
        data["units"]["secondary"] = {"code": p.unit_secondary, "label": unit_label(p.unit_secondary)}
        data["units"]["conversion_factor"] = str(p.conversion_factor or Decimal("0"))
    return data

@login_required
@require_GET
def api_barcode_lookup(request: HttpRequest, value: str):
    bc = (
        ProductBarcode.objects
        .select_related("product")
        .filter(barcode=value, product__is_active=True)
        .first()
    )
    if not bc:
        return JsonResponse({"ok": False, "error": "NOT_FOUND"})
    p = bc.product
    payload = product_payload(p)
    payload["matched_unit_index"] = 1 if p.is_single_unit else int(bc.unit_index)  # 1 or 2
    return JsonResponse({"ok": True, "product": payload})

@login_required
@require_GET
def api_search_name(request: HttpRequest):
    q = (request.GET.get("q") or "").strip()
    limit = int(request.GET.get("limit") or 5)
    if not q:
        return JsonResponse({"ok": True, "hits": []})
    qs = Product.objects.filter(is_active=True, name__icontains=q).order_by("name")[:limit]
    hits = [{"id": p.id, "name": p.name, "number": p.display_code} for p in qs]
    return JsonResponse({"ok": True, "hits": hits})

@login_required
@require_GET
def api_lookup_code(request: HttpRequest, value: str):
    uid = (
        ProductUnitId.objects
        .select_related("product")
        .filter(value=value, product__is_active=True)
        .first()
    )
    if not uid:
        return JsonResponse({"ok": False, "error": "NOT_FOUND"})
    p = uid.product
    payload = product_payload(p)
    payload["matched_unit_index"] = 1 if p.is_single_unit else int(uid.unit_index)  # 1 or 2
    return JsonResponse({"ok": True, "product": payload})

@login_required
@require_GET
def api_lookup_id(request: HttpRequest, pk: int):
    try:
        p = Product.objects.get(pk=pk, is_active=True)
    except Product.DoesNotExist:
        return JsonResponse({"ok": False, "error": "NOT_FOUND"})
    payload = product_payload(p)
    payload["matched_unit_index"] = 1  # default U1
    return JsonResponse({"ok": True, "product": payload})
