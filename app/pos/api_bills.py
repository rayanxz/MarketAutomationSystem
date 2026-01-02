# app/pos/api_bills.py
from __future__ import annotations
from datetime import date
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpRequest
from django.views.decorators.http import require_POST, require_GET
from django.utils import timezone
from django.db import transaction

from .models import SalesBill, SalesBillRow, CustomerProfile, PosShift
from . import services as POSSV

from audit_log import services as AuditSV
from audit_log.models import AuditAction

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

        # optional shift id (from POS shift box)
        shift = None
        shift_id = payload.get("shift_id")
        if shift_id:
            try:
                shift_obj = PosShift.objects.get(pk=int(shift_id))
                # small safety: only owner or superuser can bind to this shift
                if shift_obj.user_id == request.user.id or request.user.is_superuser:
                    shift = shift_obj
            except (ValueError, PosShift.DoesNotExist):
                shift = None

        # =====================
        # Rows from payload (validate BEFORE touching DB)
        # =====================
        rows = payload.get("rows") or []

        seen = set()
        dup = []
        for r in rows:
            pid = int(r.get("product_id") or 0)
            if pid and pid in seen:
                dup.append(pid)
            seen.add(pid)
        if dup:
            return JsonResponse(
                {"ok": False, "error": "DUPLICATE_PRODUCT_ROWS_NOT_ALLOWED"},
                status=400,
            )


        if not parked and not rows:
            # finalized bill with no rows → reject, but do NOT touch DB
            return JsonResponse(
                {"ok": False, "error": "EMPTY_FINAL_BILL"},
                status=400,
            )

        # =====================
        # Customer handling
        # =====================
        customer_obj = None
        if create_new_customer and customer_name:
            customer_obj = CustomerProfile.objects.create(
                name=customer_name,
                # created_at is auto_now_add; no need to pass it explicitly
                created_by=request.user if request.user.is_authenticated else None,
            )
        elif customer_name:
            # very simple lookup for now
            customer_obj = CustomerProfile.objects.filter(
                name__iexact=customer_name
            ).first()

        # =====================
        # Existing vs new bill
        # =====================
        if bill_id:
            try:
                bill = SalesBill.objects.select_for_update().get(pk=bill_id)
            except SalesBill.DoesNotExist:
                return JsonResponse(
                    {"ok": False, "error": "BILL_NOT_FOUND"},
                    status=404,
                )

            # HARD RULE: finalized bills are read-only in POS
            if bill.finalized:
                return JsonResponse(
                    {"ok": False, "error": "BILL_FINALIZED_READONLY"},
                    status=400,
                )

            # Ownership / permissions (POS-level only for now)
            # Cashier can only edit their own bills; superuser can edit any
            if bill.cashier and bill.cashier != request.user and not request.user.is_superuser:
                return JsonResponse(
                    {"ok": False, "error": "PERMISSION_DENIED"},
                    status=403,
                )
            before = {
                "parked": bool(bill.parked),
                "finalized": bool(bill.finalized),
                "is_deleted": bool(getattr(bill, "is_deleted", False)),
                "total_amount": str(bill.total_amount or Decimal("0")),
                "paid_amount": str(bill.paid_amount or Decimal("0")),
            }
            # revive deleted parked bill if cashier saves it again
            if bill.is_deleted:
                bill.is_deleted = False
                bill.deleted_at = None
                bill.deleted_by = None


            # wipe rows and rewrite
            bill.rows.all().delete()
        else:
            before = None
            # NEW BILL:
            # - bind cashier to current user
            # - attach to current work day + login session container
            cashier = request.user if request.user.is_authenticated else None
            bill = SalesBill(
                cashier=cashier,
            )

            session = POSSV.get_or_create_login_session(cashier)
            if session is not None:
                bill.login_session = session
                bill.work_day = session.day
            else:
                # fallback: at least make sure the bill is tied to a work day
                bill.work_day = POSSV.get_or_create_work_day()

        # =====================
        # Update bill fields
        # =====================
        bill.customer = customer_obj
        bill.customer_name = customer_name
        bill.pay_status = pay_status
        bill.total_amount = total_amount
        bill.paid_amount = paid_amount
        bill.parked = parked
        bill.finalized = not parked
        bill.shift = shift
        bill.save()

        # =====================
        # Rewrite rows
        # =====================
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

        # =====================
        # Finalize to inventory (sale from store)
        # =====================
        if not bill.parked:
            try:
                POSSV.finalize_pos_bill(bill=bill, actor=request.user)
            except POSSV.InsufficientStockError as e:
                # Mark the transaction for rollback: we do NOT want to keep
                # this bill/rows if stock is insufficient.
                transaction.set_rollback(True)

                items = []
                for it in getattr(e, "items", []):
                    items.append(
                        {
                            "product_id": it.get("product_id"),
                            "product_name": it.get("product_name", ""),
                            "needed": str(it.get("needed")),
                            "available": str(it.get("available")),
                        }
                    )

                return JsonResponse(
                    {
                        "ok": False,
                        "error": "INSUFFICIENT_STOCK",
                        "items": items,
                    },
                    status=400,
                )
            except RuntimeError as e:
                transaction.set_rollback(True)
                return JsonResponse(
                    {"ok": False, "error": str(e)},
                    status=400,
                )
        def _log_after_commit(*, title: str, kind: str):
            rows_count = len(rows or [])

            meta = {
                "kind": kind,  # pos.sale_bill_saved / pos.sale_bill_pended
                "summary": {
                    "bill_id": bill.id,
                    "rows_count": rows_count,
                    "customer_name": bill.customer_name or "",
                    "pay_status": bill.pay_status,
                    "total_amount": str(bill.total_amount or Decimal("0")),
                    "paid_amount": str(bill.paid_amount or Decimal("0")),
                    "parked": bool(bill.parked),
                    "finalized": bool(bill.finalized),
                    "shift_id": bill.shift_id,
                    "work_day": str(bill.work_day.date) if bill.work_day else "",
                },
            }

            transaction.on_commit(lambda: AuditSV.log_event(
                action=AuditAction.INFO,
                actor=request.user,
                request=request,
                target=bill,
                title=title,
                message=title,
                meta=meta,
            ))

        # ===== Audit: parked vs saved =====
        is_new = (before is None)

        # parked event
        if bill.parked and not bill.finalized:
            if is_new or (before and not before.get("parked", False)):
                _log_after_commit(title="POS bill parked", kind="pos.sale_bill_pended")

        # saved event
        if (not bill.parked) and bill.finalized:
            if is_new or (before and not before.get("finalized", False)):
                _log_after_commit(title="POS bill saved", kind="pos.sale_bill_saved")

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

    For now:
      - superuser → sees all today's bills
      - normal user → sees only their own bills (cashier=request.user)
    """
    # 🔥 use Django local date, not plain date.today()
    today = timezone.localdate()

    qs = SalesBill.objects.filter(created_at__date=today, is_deleted=False)

    # scope by cashier
    if not request.user.is_superuser:
        qs = qs.filter(cashier=request.user)

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

    For now:
      - superuser → can view any bill
      - normal user → can view only their own bills
    """
    try:
        bill = SalesBill.objects.select_related("customer").get(pk=bill_id)
    except SalesBill.DoesNotExist:
        return JsonResponse({"ok": False, "error": "Bill not found"}, status=404)

    # permissions:
    # - superuser OR staff can view any bill
    # - normal user can view only their own bills
    if not (request.user.is_superuser or request.user.is_staff):
        if bill.cashier and bill.cashier != request.user:
            return JsonResponse({"ok": False, "error": "PERMISSION_DENIED"}, status=403)

    if bill.is_deleted and not (request.user.is_superuser or request.user.is_staff):
        return JsonResponse({"ok": False, "error": "NOT_FOUND"}, status=404)

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

    For now:
      - cashier can delete only their own parked bills
      - superuser can delete any parked bill
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

    # ownership / permissions
    if bill.cashier and bill.cashier != request.user and not request.user.is_superuser:
        return JsonResponse(
            {"ok": False, "error": "PERMISSION_DENIED"},
            status=403,
        )
    with transaction.atomic():
        bill = SalesBill.objects.select_for_update().get(pk=bill.pk)

        bill.is_deleted = True
        bill.deleted_at = timezone.now()
        bill.deleted_by = request.user
        bill.save(update_fields=["is_deleted", "deleted_at", "deleted_by"])

        meta = {
            "kind": "pos.sale_bill_deleted",
            "summary": {
                "bill_id": bill.id,
                "customer_name": bill.customer_name or "",
                "pay_status": bill.pay_status,
                "total_amount": str(bill.total_amount or Decimal("0")),
                "paid_amount": str(bill.paid_amount or Decimal("0")),
                "parked": bool(bill.parked),
                "finalized": bool(bill.finalized),
            },
        }


        transaction.on_commit(lambda: AuditSV.log_event(
            action=AuditAction.INFO,
            actor=request.user,
            request=request,
            target=bill,
            title="POS bill deleted (parked)",
            message="POS bill deleted (parked)",
            meta=meta,
        ))

    return JsonResponse({"ok": True})
