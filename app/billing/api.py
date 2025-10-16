# app/billing/api.py
from __future__ import annotations
import json
from decimal import Decimal
from django.db import transaction
from django.http import JsonResponse, HttpRequest
from django.views.decorators.http import require_GET, require_POST
from django.db.models.functions import Lower
from accounts.models import AccountProfile
from catalog.views import role_required
from catalog.models import Product
from .models import Provider, Bill, BillItem

@require_GET
@role_required(AccountProfile.Role.MANAGER)
def providers_ac(request: HttpRequest):
    q = (request.GET.get("q") or "").strip()
    if not q:
        return JsonResponse({"ok": True, "items": []})
    rows = list(
        Provider.objects
        .annotate(n=Lower("name"))
        .filter(n__contains=q.lower())
        .order_by("name")
        .values("id", "name")[:3]
    )
    return JsonResponse({"ok": True, "items": rows})

@require_POST
@role_required(AccountProfile.Role.MANAGER)
def save_bill(request: HttpRequest):
    """
    Body:
    {
      "provider": {"id": 1, "name": "ACME"},   # if id missing, create provider by name
      "pay": {"status": "paid"|"unpaid"|"partial", "paid_amount": "0"},
      "items": [
        {
          "product_id": 123,
          "unit_index": 1|2,
          "qty_raw": "2.0",
          "qty_u1": "48.0",              # client can send; we recompute anyway
          "cost": "1.50",                # per primary unit (after save -> update Product.cost)
          "price": "2.00",               # per primary unit (optional update on Product.price)
          "total_cost": "72.00"          # the negotiated line total
        },
        ...
      ]
    }
    """
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except Exception:
        return JsonResponse({"ok": False, "error": "bad json"}, status=400)

    provider_data = payload.get("provider") or {}
    items_data    = payload.get("items") or []
    pay_data      = payload.get("pay") or {}

    if not items_data:
        return JsonResponse({"ok": False, "error": "no items"}, status=400)

    # resolve provider
    prov = None
    pid = provider_data.get("id")
    pname = (provider_data.get("name") or "").strip()
    if pid:
        prov = Provider.objects.filter(id=pid).first()
    if not prov and pname:
        prov, _ = Provider.objects.get_or_create(name=pname)
    if not prov:
        return JsonResponse({"ok": False, "error": "provider required"}, status=400)

    # compute totals and persist
    try:
        with transaction.atomic():
            bill = Bill(provider=prov)

            # temp; set after items
            bill.total_cost = Decimal("0")
            status = (pay_data.get("status") or "unpaid")
            if status not in ("paid", "unpaid", "partial"):
                status = "unpaid"
            bill.pay_status = status
            bill.paid_amount = Decimal(str(pay_data.get("paid_amount") or "0"))
            bill.save()  # assigns serial

            grand_total = Decimal("0")

            for it in items_data:
                prod = Product.objects.select_for_update().filter(id=int(it["product_id"])).first()
                if not prod:
                    raise ValueError("product not found")

                unit_index = int(it.get("unit_index") or 1)
                qty_raw    = Decimal(str(it.get("qty_raw") or "0"))
                cost       = Decimal(str(it.get("cost") or "0"))
                price      = Decimal(str(it.get("price") or "0"))
                total_cost = Decimal(str(it.get("total_cost") or "0"))
                # convert to primary unit
                if unit_index == 1:
                    qty_u1 = qty_raw
                else:
                    cf = prod.conversion_factor or Decimal("0")
                    if cf <= 0:
                        # fallback: treat as primary
                        qty_u1 = qty_raw
                        unit_index = 1
                    else:
                        qty_u1 = qty_raw * cf

                # line create
                BillItem.objects.create(
                    bill=bill, product=prod, unit_index=unit_index,
                    qty_raw=qty_raw, qty_u1=qty_u1, cost=cost, price=price, total_cost=total_cost
                )

                # stock increment (can move negative→positive)
                prod.stock_qty = (prod.stock_qty or 0) + qty_u1
                # update cost/price to the new ones (as requested)
                if cost > 0:
                    prod.cost = cost
                if price > 0:
                    prod.price = price
                prod.save(update_fields=["stock_qty", "cost", "price", "updated_at"])

                grand_total += total_cost if total_cost > 0 else (qty_u1 * cost)

            bill.total_cost = grand_total
            # clamp paid amount for safety
            if bill.pay_status == Bill.PayStatus.PAID:
                bill.paid_amount = bill.total_cost
            elif bill.pay_status == Bill.PayStatus.UNPAID:
                bill.paid_amount = Decimal("0")
            else:
                # PARTIAL: keep as provided but not more than total
                bill.paid_amount = min(bill.paid_amount, bill.total_cost)

            bill.save(update_fields=["total_cost", "paid_amount", "pay_status", "updated_at"])

    except Exception as e:
        return JsonResponse({"ok": False, "error": "save failed"}, status=500)

    return JsonResponse({"ok": True, "bill": {"id": bill.id, "serial": bill.serial, "total": str(bill.total_cost)}})
