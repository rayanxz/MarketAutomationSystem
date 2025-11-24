# app/pos/api_bills.py
from __future__ import annotations
from datetime import date
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpRequest
from django.views.decorators.http import require_POST, require_GET
from django.utils import timezone
from django.db import transaction

from .models import SalesBill, SalesBillRow, CustomerProfile
from . import services as POSSV


def _parse_decimal(x):
    try:
        return Decimal(str(x or "0"))
    except Exception:
        return Decimal("0")


@login_required
@require_POST
def api_bill_save(request: HttpRequest):
    """
    Save / update a bill.
    URL: /pos/api/bill/save/
    Body: JSON as built in sendBillToBackend() in pos.js
    """
    import json
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)

    with transaction.atomic():
        bill_id = payload.get("id")
        parked = bool(payload.get("parked"))
        pay_status = payload.get("pay_status") or SalesBill.PAY_FULL
        total_amount = _parse_decimal(payload.get("total_amount"))
        paid_amount = _parse_decimal(payload.get("paid_amount"))
        customer_name = (payload.get("customer_name") or "").strip()
        create_new_customer = bool(payload.get("create_new_customer"))

        customer_obj = None
        if create_new_customer and customer_name:
            customer_obj = CustomerProfile.objects.create(
                name=customer_name,
                created_at=timezone.now(),
                created_by=request.user if request.user.is_authenticated else None,
            )
        elif customer_name:
            # very simple lookup for now
            customer_obj = CustomerProfile.objects.filter(
                name__iexact=customer_name
            ).first()

        if bill_id:
            bill = SalesBill.objects.select_for_update().get(pk=bill_id)
            # if already finalized, don't allow editing here
            if bill.finalized and not parked:
                return JsonResponse({"ok": False, "error": "Bill already finalized."}, status=400)
            # wipe rows and rewrite
            bill.rows.all().delete()
        else:
            bill = SalesBill(
                cashier=request.user if request.user.is_authenticated else None,
            )

        bill.customer = customer_obj
        bill.customer_name = customer_name
        bill.pay_status = pay_status
        bill.total_amount = total_amount
        bill.paid_amount = paid_amount
        bill.parked = parked
        bill.finalized = not parked
        bill.save()

        rows = payload.get("rows") or []
        for r in rows:
            SalesBillRow.objects.create(
                bill=bill,
                product_id=int(r.get("product_id") or 0),
                product_name=r.get("name") or "",
                product_number=r.get("number") or "",
                qty=_parse_decimal(r.get("qty")),
                uom_index=int(r.get("uom_index") or 1),
                unit_price=_parse_decimal(r.get("unit_price")),
                disc_amount=_parse_decimal(r.get("disc_amount")),
                disc_pct=_parse_decimal(r.get("disc_pct") or 0),
                notes=r.get("notes") or "",
            )

        # If bill is NOT parked → finalize: create inventory movements (SALE from store)
        if not bill.parked:
            POSSV.finalize_pos_bill(bill=bill, actor=request.user)


        return JsonResponse({
            "ok": True,
            "bill": {
                "id": bill.id,
                "parked": bill.parked,
            }
        })


@login_required
@require_GET
def api_bills_today(request: HttpRequest):
    """
    List today's bills for left panel.
    URL: /pos/api/bills/today/
    Optional ?q= for customer name search.
    """
    today = date.today()
    qs = SalesBill.objects.filter(created_at__date=today)

    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(customer_name__icontains=q)

    bills = []
    for b in qs.select_related("customer").order_by("-created_at"):
        dt = timezone.localtime(b.created_at)
        bills.append({
            "id": b.id,
            "created_at": dt.isoformat(),
            "time": dt.strftime("%H:%M"),
            "customer_name": b.customer_name,
            "customer_id": b.customer_id,
            "pay_status": b.pay_status,
            "total_amount": str(b.total_amount),
            "paid_amount": str(b.paid_amount),
            "parked": b.parked,
        })
    return JsonResponse({"ok": True, "bills": bills})


@login_required
@require_GET
def api_bill_detail(request: HttpRequest, bill_id: int):
    """
    Single bill detail for loading into middle section when left item is clicked.
    URL: /pos/api/bill/<bill_id>/
    """
    try:
        bill = SalesBill.objects.select_related("customer").get(pk=bill_id)
    except SalesBill.DoesNotExist:
        return JsonResponse({"ok": False, "error": "Bill not found"}, status=404)

    rows = []
    for r in bill.rows.all():
        rows.append({
            "product_id": r.product_id,
            "name": r.product_name,
            "number": r.product_number,
            "qty": str(r.qty),
            "uom_index": r.uom_index,
            "unit_price": str(r.unit_price),
            "disc_amount": str(r.disc_amount),
            "disc_pct": str(r.disc_pct),
            "notes": r.notes,
            # if later you want units, add conv/u1_label/u2_label here
        })

    dt = timezone.localtime(bill.created_at)
    data = {
        "id": bill.id,
        "created_at": dt.isoformat(),
        "customer_name": bill.customer_name,
        "customer_id": bill.customer_id,
        "pay_status": bill.pay_status,
        "total_amount": str(bill.total_amount),
        "paid_amount": str(bill.paid_amount),
        "parked": bill.parked,
        "rows": rows,
    }
    return JsonResponse({"ok": True, "bill": data})


@login_required
@require_GET
def api_customers_search(request: HttpRequest):
    """
    Simple customer autocomplete by name.

    GET /pos/api/customers/search/?q=...&limit=7
    """
    q = (request.GET.get("q") or "").strip()
    limit = int(request.GET.get("limit") or 7)
    if not q:
        return JsonResponse({"ok": True, "hits": []})

    qs = CustomerProfile.objects.filter(name__icontains=q).order_by("name")[:limit]
    hits = [
        {
            "id": c.id,
            "name": c.name,
            "phone": c.phone,
        }
        for c in qs
    ]
    return JsonResponse({"ok": True, "hits": hits})



@login_required
@require_POST
def api_bill_delete(request, pk: int):
    """
    Delete a parked POS bill (used by Ctrl+Backspace on parked bills).
    Finalized bills are NOT deletable from POS.
    """
    try:
        bill = SalesBill.objects.get(pk=pk)
    except SalesBill.DoesNotExist:
        return JsonResponse({"ok": False, "error": "NOT_FOUND"}, status=404)

    # only parked bills can be deleted
    if not bill.parked:
        return JsonResponse(
            {"ok": False, "error": "ONLY_PARKED_DELETABLE"},
            status=400,
        )

    # (optional) you can also check bill.cashier == request.user here

    bill.delete()
    return JsonResponse({"ok": True})
