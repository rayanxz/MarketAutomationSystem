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
from catalog.models import Product
from core.currency import SYP, USD
from financials.models import MoneyContainer, MoneyContainerCurrency
from financials import services as FinSV
from inventory.models import q3, DEC0
from . import services as POSSV

from audit_log import services as AuditSV
from audit_log.models import AuditAction

def _parse_decimal(x):
    try:
        return Decimal(str(x or "0"))
    except Exception:
        return Decimal("0")


def _calc_row_total(*, product: Product, row: dict) -> Decimal:
    qty = _parse_decimal(row.get("qty"))
    uom_index = int(row.get("uom_index") or 1)
    unit_price = _parse_decimal(row.get("unit_price"))

    conv = getattr(product, "conversion_factor", None) or Decimal("1")
    qty_primary = q3(qty * conv) if uom_index == 2 else q3(qty)
    if qty_primary <= 0:
        return DEC0

    base = q3(qty_primary * unit_price)

    disc_amt = _parse_decimal(row.get("disc_amount"))
    disc_pct = _parse_decimal(row.get("disc_pct"))
    if disc_amt <= 0 and disc_pct > 0 and base > 0:
        disc_amt = q3((base * disc_pct) / Decimal("100"))

    if disc_amt < 0:
        disc_amt = DEC0
    if disc_amt > base:
        disc_amt = base

    return q3(base - disc_amt)

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
        settlement_mode = (payload.get("settlement_mode") or SalesBill.SETTLE_SPLIT).lower()
        money_container_id = payload.get("money_container_id")
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
        # Validate rows + totals split by currency
        # =====================
        normalized_rows = []
        total_syp = DEC0
        total_usd = DEC0

        product_ids = []
        for r in rows:
            pid = int(r.get("product_id") or 0)
            if pid:
                product_ids.append(pid)
        products = Product.objects.in_bulk(product_ids)

        for r in rows:
            pid = int(r.get("product_id") or 0)
            if not pid or pid not in products:
                return JsonResponse({"ok": False, "error": "INVALID_PRODUCT"}, status=400)
            product = products[pid]

            row_currency = (r.get("currency") or "").upper()
            if not row_currency:
                row_currency = product.get_effective_default_sale_currency()
                if row_currency == USD and (product.default_price_usd or DEC0) <= 0 and product.allow_syp_sales:
                    row_currency = SYP

            if row_currency not in (SYP, USD):
                return JsonResponse({"ok": False, "error": "INVALID_CURRENCY"}, status=400)

            if row_currency == SYP and not product.allow_syp_sales:
                return JsonResponse({"ok": False, "error": "SYP_NOT_ALLOWED"}, status=400)
            if row_currency == USD and not product.allow_usd_sales:
                return JsonResponse({"ok": False, "error": "USD_NOT_ALLOWED"}, status=400)

            if row_currency == USD:
                unit_price = _parse_decimal(r.get("unit_price"))
                if (product.default_price_usd or DEC0) <= 0 and unit_price <= 0:
                    return JsonResponse({"ok": False, "error": "USD_PRICE_MISSING"}, status=400)

            row_total = _calc_row_total(product=product, row=r)
            if row_currency == USD:
                total_usd = q3(total_usd + row_total)
            else:
                total_syp = q3(total_syp + row_total)

            row_copy = dict(r)
            row_copy["currency"] = row_currency
            normalized_rows.append(row_copy)

        rows = normalized_rows

        fx_rate = None
        try:
            fx_rate = FinSV.get_current_fx_syp_per_usd()
        except Exception:
            fx_rate = None

        if settlement_mode not in (SalesBill.SETTLE_SPLIT, SalesBill.SETTLE_ALL_SYP, SalesBill.SETTLE_ALL_USD):
            return JsonResponse({"ok": False, "error": "INVALID_SETTLEMENT_MODE"}, status=400)

        if settlement_mode == SalesBill.SETTLE_ALL_SYP:
            if fx_rate is None:
                return JsonResponse({"ok": False, "error": "FX_REQUIRED"}, status=400)
            settlement_total = q3(total_syp + (total_usd * fx_rate))
            settlement_currency = SYP
        elif settlement_mode == SalesBill.SETTLE_ALL_USD:
            if fx_rate is None:
                return JsonResponse({"ok": False, "error": "FX_REQUIRED"}, status=400)
            settlement_total = q3(total_usd + (total_syp / fx_rate))
            settlement_currency = USD
        else:
            settlement_total = q3(total_syp + total_usd)
            settlement_currency = None

        if not parked and pay_status == SalesBill.PAY_PARTIAL and settlement_mode == SalesBill.SETTLE_SPLIT:
            return JsonResponse({"ok": False, "error": "PARTIAL_REQUIRES_SINGLE_CURRENCY"}, status=400)

        container = None
        if money_container_id:
            try:
                container = MoneyContainer.objects.get(pk=int(money_container_id))
            except (ValueError, MoneyContainer.DoesNotExist):
                return JsonResponse({"ok": False, "error": "INVALID_CONTAINER"}, status=400)

            if not container.features.filter(code="pos_sales", is_active=True).exists():
                return JsonResponse({"ok": False, "error": "CONTAINER_NOT_POS"}, status=400)

            if not (request.user.is_superuser or request.user.is_staff):
                if container.allowed_users.exists() and not container.allowed_users.filter(pk=request.user.pk).exists():
                    return JsonResponse({"ok": False, "error": "CONTAINER_FORBIDDEN"}, status=403)

            enabled_codes = set(
                MoneyContainerCurrency.objects
                .filter(container=container, is_enabled=True)
                .values_list("currency__code", flat=True)
            )
            if settlement_mode == SalesBill.SETTLE_SPLIT:
                if total_syp > 0 and SYP not in enabled_codes:
                    return JsonResponse({"ok": False, "error": "SYP_DISABLED_IN_CONTAINER"}, status=400)
                if total_usd > 0 and USD not in enabled_codes:
                    return JsonResponse({"ok": False, "error": "USD_DISABLED_IN_CONTAINER"}, status=400)
            else:
                if settlement_currency and settlement_currency not in enabled_codes:
                    return JsonResponse({"ok": False, "error": "CURRENCY_DISABLED_IN_CONTAINER"}, status=400)

        if not parked and pay_status != SalesBill.PAY_NONE and container is None:
            return JsonResponse({"ok": False, "error": "CONTAINER_REQUIRED"}, status=400)

        if pay_status == SalesBill.PAY_FULL:
            paid_amount = settlement_total
        elif pay_status == SalesBill.PAY_NONE:
            paid_amount = DEC0
        else:
            if paid_amount <= 0:
                return JsonResponse({"ok": False, "error": "PAID_AMOUNT_REQUIRED"}, status=400)
            if paid_amount >= settlement_total:
                return JsonResponse({"ok": False, "error": "PAID_AMOUNT_TOO_HIGH"}, status=400)

        total_amount = settlement_total

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
        bill.total_syp = total_syp
        bill.total_usd = total_usd
        bill.paid_amount = paid_amount
        bill.settlement_mode = settlement_mode
        bill.settlement_currency = settlement_currency
        bill.fx_rate_used = fx_rate
        bill.money_container = container
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
                sale_currency=(r.get("currency") or SYP),
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
            except ValueError as e:
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
                    "total_syp": str(bill.total_syp or Decimal("0")),
                    "total_usd": str(bill.total_usd or Decimal("0")),
                    "paid_amount": str(bill.paid_amount or Decimal("0")),
                    "settlement_mode": bill.settlement_mode,
                    "settlement_currency": bill.settlement_currency,
                    "money_container_id": bill.money_container_id,
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
            "total_syp": str(b.total_syp),
            "total_usd": str(b.total_usd),
            "paid_amount": str(b.paid_amount),
            "settlement_mode": b.settlement_mode,
            "settlement_currency": b.settlement_currency,
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
            "currency": r.sale_currency or SYP,
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
        "total_syp": str(bill.total_syp),
        "total_usd": str(bill.total_usd),
        "paid_amount": str(bill.paid_amount),
        "settlement_mode": bill.settlement_mode,
        "settlement_currency": bill.settlement_currency,
        "money_container_id": bill.money_container_id,
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
