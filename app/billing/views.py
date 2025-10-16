# app/billing/views.py
from __future__ import annotations
from decimal import Decimal, InvalidOperation
import json

from django.db import transaction
from django.db.models.functions import Lower
from django.http import JsonResponse, HttpRequest, HttpResponse
from django.shortcuts import redirect, render, get_object_or_404
from django.views.decorators.http import require_GET, require_POST

from accounts.models import AccountProfile
from catalog.views import role_required
from catalog.models import Product
from billing.models import Provider, Bill, BillItem


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


# ---------- Utilities ----------

def _to_decimal(val, default: str = "0") -> Decimal:
    if val is None:
        val = default
    if isinstance(val, (int, float, Decimal)):
        return Decimal(str(val))
    s = str(val).strip().replace(",", ".") or default
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return Decimal(default)


# ---------- APIs ----------

@require_GET
@role_required(AccountProfile.Role.MANAGER)
def api_providers_ac(request: HttpRequest) -> JsonResponse:
    q = (request.GET.get("q") or "").strip()
    if not q:
        return JsonResponse({"ok": True, "items": []})
    items = (
        Provider.objects
        .filter(name__icontains=q)
        .order_by(Lower("name"))
        .values("id", "name")[:8]
    )
    return JsonResponse({"ok": True, "items": list(items)})


@require_POST
@role_required(AccountProfile.Role.MANAGER)
def api_bill_save(request: HttpRequest) -> JsonResponse:
    """
    JSON body:
    {
      "provider": {"id": 12} OR {"name": "ACME"},
      "items": [
        {
          "product_id": 1,
          "unit_index": 1|2,
          "qty_raw": "2.5",
          "cost": "12.3",     # per primary unit
          "price": "15.0",    # per primary unit
          "total_cost": "30"  # optional override
        }, ...
      ],
      "pay": {"status": "paid"|"unpaid"|"partial", "paid_amount": "0"}
    }
    """
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except Exception:
        return JsonResponse({"ok": False, "error": "bad json"}, status=400)

    prov_in = payload.get("provider") or {}
    items_in = payload.get("items") or []
    pay_in = payload.get("pay") or {}

    if not items_in:
        return JsonResponse({"ok": False, "error": "no items"}, status=400)

    # Provider: resolve by id or CI name, create if new
    provider = None
    pid = prov_in.get("id")
    pname = (prov_in.get("name") or "").strip()
    if pid:
        provider = Provider.objects.filter(id=pid).first()
    if not provider:
        if not pname:
            return JsonResponse({"ok": False, "error": "provider required"}, status=400)
        provider = Provider.objects.filter(name__iexact=pname).first() or Provider.objects.create(name=pname)

    # Payment
    status_raw = (pay_in.get("status") or "unpaid").lower()
    if status_raw not in {"paid", "unpaid", "partial"}:
        status_raw = "unpaid"
    paid_amount = _to_decimal(pay_in.get("paid_amount"), "0")

    try:
        with transaction.atomic():
            bill = Bill(provider=provider, status=status_raw, paid_amount=paid_amount, total=Decimal("0"))
            bill.save()  # assigns serial if empty

            grand = Decimal("0")

            for row in items_in:
                prod_id = int(row.get("product_id"))
                unit_idx = int(row.get("unit_index") or 1)
                qty_raw = _to_decimal(row.get("qty_raw"))
                cost_u1 = _to_decimal(row.get("cost"))
                price_u1 = _to_decimal(row.get("price"))
                total_override = row.get("total_cost")
                total_override = _to_decimal(total_override, "0") if total_override not in (None, "") else None

                product = get_object_or_404(Product.objects.select_for_update(), pk=prod_id)

                # Convert to primary qty if needed
                qty_primary = qty_raw
                if unit_idx == 2 and product.conversion_factor:
                    qty_primary = qty_raw * Decimal(product.conversion_factor)

                line_total = total_override if (total_override is not None and total_override > 0) else (cost_u1 * qty_primary)

                # Persist item
                BillItem.objects.create(
                    bill=bill,
                    product=product,
                    unit_index=unit_idx,
                    qty_primary=qty_primary,
                    cost=cost_u1,
                    price=price_u1,
                    line_total=line_total,
                )

                # Update product live fields
                product.cost = cost_u1
                product.price = price_u1
                product.stock_qty = (product.stock_qty or Decimal("0")) + qty_primary
                product.save(update_fields=["cost", "price", "stock_qty", "updated_at"])

                grand += line_total

            bill.total = grand
            bill.save(update_fields=["total"])

            return JsonResponse({
                "ok": True,
                "bill": {
                    "id": bill.id,
                    "serial": bill.serial,
                    "total": str(bill.total),
                    "provider": {"id": provider.id, "name": provider.name},
                }
            })
    except Exception:
        return JsonResponse({"ok": False, "error": "save failed"}, status=500)
