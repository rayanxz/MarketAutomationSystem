# app/pos/api_bills.py
from __future__ import annotations
from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.http import JsonResponse, HttpRequest
from django.views.decorators.http import require_POST, require_GET
from django.utils import timezone
from django.db import transaction

from .models import SalesBill, SalesBillRow, CustomerProfile, PosShift
from debts import services as DebtSV
from debts.models import DebtorDebt, DebtorPayment, PartyType
from catalog.models import Product
from core.currency import SYP, USD
from financials.models import MoneyContainer, MoneyContainerCurrency
from financials import services as FinSV
from inventory.models import q3, q4, DEC0
from . import services as POSSV
from accounts.decorators import role_required_api
from accounts.models import AccountProfile
from accounts.utils import has_role

from audit_log import services as AuditSV
from audit_log.models import AuditAction

def _parse_decimal(x):
    try:
        return Decimal(str(x or "0"))
    except Exception:
        return Decimal("0")


def _q_money(currency_code: str, amount: Decimal) -> Decimal:
    return FinSV.q_money(amount=Decimal(amount or DEC0), currency_code=(currency_code or SYP).upper())


def _q_fx(value: Decimal) -> Decimal:
    return FinSV.q_fx(Decimal(value))


def _calc_row_total(*, product: Product, row: dict) -> Decimal:
    qty = _parse_decimal(row.get("qty"))
    uom_index = int(row.get("uom_index") or 1)
    unit_price = _parse_decimal(row.get("unit_price"))

    if product.is_single_unit:
        uom_index = 1
        conv = Decimal("1")
    else:
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


def _sync_customer_debt(*, bill: SalesBill, actor) -> None:
    fx_rate = FinSV.q_fx(bill.fx_rate_used or FinSV.get_current_fx_syp_per_usd())
    total_syp = _q_money(SYP, bill.total_syp or DEC0)
    total_usd = _q_money(USD, bill.total_usd or DEC0)
    mode = bill.settlement_mode or SalesBill.SETTLE_SPLIT
    pay_status = bill.pay_status or SalesBill.PAY_NONE

    # Legacy fallback if split totals were not persisted on old rows.
    if total_syp <= DEC0 and total_usd <= DEC0:
        for row in bill.rows.all():
            try:
                product = Product.objects.get(pk=row.product_id, is_active=True)
            except Product.DoesNotExist:
                raise ValidationError("Product is archived and cannot be used in new operations.")
            row_total = POSSV._calc_row_total(product=product, row=row)
            row_currency = (row.sale_currency or SYP).upper()
            if row_currency == USD:
                total_usd = _q_money(USD, total_usd + row_total)
            else:
                total_syp = _q_money(SYP, total_syp + row_total)

    totals_by_code: dict[str, Decimal] = {}
    settled_counterparty_by_code: dict[str, Decimal] = {}
    container_collected_by_code: dict[str, Decimal] = {}
    debtor_entries_by_code: dict[str, DebtorDebt] = {}

    customer_party_name = (bill.customer_name or "").strip()
    if pay_status != SalesBill.PAY_FULL and not bill.customer_id and not customer_party_name:
        raise ValueError("CUSTOMER_REQUIRED_FOR_DEBT")

    if mode == SalesBill.SETTLE_ALL_SYP:
        total_settlement_syp = _q_money(SYP, total_syp + (total_usd * fx_rate))
        if total_settlement_syp > DEC0:
            totals_by_code[SYP] = total_settlement_syp
        paid_syp = DEC0
        if pay_status == SalesBill.PAY_FULL:
            paid_syp = total_settlement_syp
        elif pay_status == SalesBill.PAY_PARTIAL:
            paid_syp = _q_money(SYP, bill.paid_amount or DEC0)
        if paid_syp > DEC0:
            settled_counterparty_by_code[SYP] = paid_syp
            container_collected_by_code[SYP] = paid_syp
        if pay_status != SalesBill.PAY_FULL and total_settlement_syp > DEC0:
            debtor_entries_by_code[SYP] = DebtSV.create_debtor_entry(
                provider=None,
                total=total_settlement_syp,
                paid_amount=paid_syp,
                source_app="pos",
                source_model="SalesBill",
                source_id=str(bill.id),
                currency_code=SYP,
                party_type=PartyType.CUSTOMER,
                party_name=customer_party_name,
                customer_id=bill.customer_id,
            )
    elif mode == SalesBill.SETTLE_ALL_USD:
        total_settlement_usd = _q_money(USD, total_usd + (total_syp / fx_rate))
        if total_settlement_usd > DEC0:
            totals_by_code[USD] = total_settlement_usd
        paid_usd = DEC0
        if pay_status == SalesBill.PAY_FULL:
            paid_usd = total_settlement_usd
        elif pay_status == SalesBill.PAY_PARTIAL:
            paid_usd = _q_money(USD, bill.paid_amount or DEC0)
        if paid_usd > DEC0:
            settled_counterparty_by_code[USD] = paid_usd
            container_collected_by_code[USD] = paid_usd
        if pay_status != SalesBill.PAY_FULL and total_settlement_usd > DEC0:
            debtor_entries_by_code[USD] = DebtSV.create_debtor_entry(
                provider=None,
                total=total_settlement_usd,
                paid_amount=paid_usd,
                source_app="pos",
                source_model="SalesBill",
                source_id=str(bill.id),
                currency_code=USD,
                party_type=PartyType.CUSTOMER,
                party_name=customer_party_name,
                customer_id=bill.customer_id,
            )
    else:
        if total_syp > DEC0:
            totals_by_code[SYP] = total_syp
        if total_usd > DEC0:
            totals_by_code[USD] = total_usd

        paid_syp = DEC0
        paid_usd = DEC0
        if pay_status == SalesBill.PAY_FULL:
            paid_syp = total_syp
            paid_usd = total_usd
        elif pay_status == SalesBill.PAY_PARTIAL:
            paid_syp = _q_money(SYP, bill.paid_amount or DEC0)

        if paid_syp > DEC0:
            settled_counterparty_by_code[SYP] = paid_syp
            container_collected_by_code[SYP] = paid_syp
        if paid_usd > DEC0:
            settled_counterparty_by_code[USD] = paid_usd
            container_collected_by_code[USD] = paid_usd

        if pay_status != SalesBill.PAY_FULL:
            if total_syp > DEC0:
                debtor_entries_by_code[SYP] = DebtSV.create_debtor_entry(
                    provider=None,
                    total=total_syp,
                    paid_amount=paid_syp,
                    source_app="pos",
                    source_model="SalesBill",
                    source_id=str(bill.id),
                    currency_code=SYP,
                    party_type=PartyType.CUSTOMER,
                    party_name=customer_party_name,
                    customer_id=bill.customer_id,
                )
            if total_usd > DEC0:
                debtor_entries_by_code[USD] = DebtSV.create_debtor_entry(
                    provider=None,
                    total=total_usd,
                    paid_amount=paid_usd,
                    source_app="pos",
                    source_model="SalesBill",
                    source_id=str(bill.id),
                    currency_code=USD,
                    party_type=PartyType.CUSTOMER,
                    party_name=customer_party_name,
                    customer_id=bill.customer_id,
                )

    if not totals_by_code:
        return

    if container_collected_by_code and not bill.money_container_id:
        raise ValueError("POS_MISSING_MONEY_CONTAINER")

    cp = POSSV.ensure_bill_counterparty(bill=bill)
    primary_receipt = FinSV.post_counterparty_sale_action_with_fx(
        actor=actor,
        counterparty_id=cp.id,
        totals_by_code=totals_by_code,
        settled_counterparty_by_code=settled_counterparty_by_code,
        container_collected_by_code=container_collected_by_code,
        container_id=(bill.money_container_id if container_collected_by_code else None),
        fx_syp_per_usd=fx_rate,
        action_key=f"pos:SalesBill:{bill.id}:create",
        note=f"POS sale #{bill.id}",
        source_app="pos",
        source_model="SalesBill",
        source_id=str(bill.id),
    )

    for code, entry in debtor_entries_by_code.items():
        paid = _q_money(code, getattr(entry, "paid_amount", DEC0) or DEC0)
        if paid <= DEC0:
            continue
        if DebtorPayment.objects.filter(entry=entry, receipt=primary_receipt).exists():
            continue
        DebtorPayment.objects.create(
            entry=entry,
            amount=paid,
            currency_code=code,
            receipt=primary_receipt,
            money_container=bill.money_container if container_collected_by_code else None,
            fx_syp_per_usd_used=fx_rate,
        )

@role_required_api(AccountProfile.Role.CASHIER, AccountProfile.Role.MANAGER)
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
                # user can bind own shift; manager/owner can bind any shift.
                if shift_obj.user_id == request.user.id or has_role(request.user, AccountProfile.Role.MANAGER):
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
        products = Product.objects.filter(id__in=product_ids, is_active=True).in_bulk()

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
                total_usd = _q_money(USD, total_usd + row_total)
            else:
                total_syp = _q_money(SYP, total_syp + row_total)

            row_copy = dict(r)
            row_copy["currency"] = row_currency
            normalized_rows.append(row_copy)

        rows = normalized_rows

        fx_rate = None
        try:
            fx_rate = FinSV.get_current_fx_syp_per_usd()
            fx_rate = _q_fx(fx_rate)
        except Exception:
            fx_rate = None

        if settlement_mode not in (SalesBill.SETTLE_SPLIT, SalesBill.SETTLE_ALL_SYP, SalesBill.SETTLE_ALL_USD):
            return JsonResponse({"ok": False, "error": "INVALID_SETTLEMENT_MODE"}, status=400)

        if settlement_mode == SalesBill.SETTLE_ALL_SYP:
            if fx_rate is None:
                return JsonResponse({"ok": False, "error": "FX_REQUIRED"}, status=400)
            settlement_total = _q_money(SYP, total_syp + (total_usd * fx_rate))
            settlement_currency = SYP
            partial_validation_limit = settlement_total
        elif settlement_mode == SalesBill.SETTLE_ALL_USD:
            if fx_rate is None:
                return JsonResponse({"ok": False, "error": "FX_REQUIRED"}, status=400)
            settlement_total = _q_money(USD, total_usd + (total_syp / fx_rate))
            settlement_currency = USD
            partial_validation_limit = settlement_total
        else:
            # Legacy compatibility for split mode: keep aggregate field in SYP scale.
            settlement_total = _q_money(SYP, total_syp + total_usd)
            settlement_currency = None
            # In split mode, the single partial `paid_amount` input is treated as SYP cash only.
            partial_validation_limit = _q_money(SYP, total_syp)

        container = None
        if money_container_id:
            try:
                container = MoneyContainer.objects.get(pk=int(money_container_id))
            except (ValueError, MoneyContainer.DoesNotExist):
                return JsonResponse({"ok": False, "error": "INVALID_CONTAINER"}, status=400)

            if not container.is_active:
                return JsonResponse({"ok": False, "error": "INVALID_CONTAINER"}, status=400)

            if not FinSV.container_supports_feature(container=container, feature_code="pos_sales"):
                return JsonResponse({"ok": False, "error": "CONTAINER_NOT_POS"}, status=400)

            if not FinSV.user_has_money_container_access(user=request.user, container=container):
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
            pay_cur = USD if settlement_mode == SalesBill.SETTLE_ALL_USD else SYP
            paid_amount = _q_money(pay_cur, paid_amount)
            if paid_amount <= 0:
                return JsonResponse({"ok": False, "error": "PAID_AMOUNT_REQUIRED"}, status=400)
            if settlement_mode == SalesBill.SETTLE_SPLIT and partial_validation_limit <= 0:
                return JsonResponse({"ok": False, "error": "SPLIT_PARTIAL_REQUIRES_SYP_AMOUNT"}, status=400)
            if paid_amount >= partial_validation_limit:
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

        if not parked and pay_status != SalesBill.PAY_FULL and customer_obj is None:
            return JsonResponse({"ok": False, "error": "CUSTOMER_REQUIRED_FOR_DEBT"}, status=400)

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
            if bill.cashier and bill.cashier != request.user and not has_role(request.user, AccountProfile.Role.MANAGER):
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
            pid = int(r.get("product_id") or 0)
            prod = products.get(pid)
            if prod and prod.is_single_unit:
                conv_val = Decimal("1")
            else:
                conv_val = _parse_decimal(getattr(prod, "conversion_factor", None)) if prod else Decimal("1")
            if not conv_val or conv_val <= 0:
                conv_val = Decimal("1")
            unit1_label = prod.get_unit_primary_display() if prod and getattr(prod, "unit_primary", None) else ""
            unit2_label = "" if (prod and prod.is_single_unit) else (prod.get_unit_secondary_display() if prod and getattr(prod, "unit_secondary", None) else "")
            uom_index = int(r.get("uom_index") or 1)
            if prod and prod.is_single_unit:
                uom_index = 1
            qty_used = abs(_parse_decimal(r.get("qty")))
            qty_primary = qty_used
            if uom_index == 2:
                qty_primary = q3(qty_used * q3(conv_val))
            row_currency = (r.get("currency") or SYP).upper()
            if row_currency == USD:
                cost_hint = q4(Decimal(str(getattr(prod, "default_cost_usd", DEC0) or DEC0)))
            else:
                cost_hint = q4(Decimal(str(getattr(prod, "default_cost_syp", DEC0) or DEC0)))
            if cost_hint <= DEC0:
                cost_hint = None
            SalesBillRow.objects.create(
                bill=bill,
                product_id=pid,
                product_name=r.get("name") or "",
                product_name_at_txn=r.get("name") or "",
                product_number=str(r.get("number") or pid),
                conv_factor_at_txn=conv_val,
                unit_1_label_at_txn=unit1_label or "",
                unit_2_label_at_txn=unit2_label or "",
                qty_primary_at_txn=qty_primary,
                qty=qty_used,
                uom_index=uom_index,
                unit_price=_parse_decimal(r.get("unit_price")),
                sale_currency=row_currency,
                unit_cost_at_txn=cost_hint,
                cost_currency_at_txn=(row_currency if cost_hint is not None else None),
                fx_rate_at_txn=fx_rate,
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
            except ValidationError as e:
                transaction.set_rollback(True)
                msg = "; ".join([str(m) for m in e.messages]) if getattr(e, "messages", None) else str(e)
                return JsonResponse({"ok": False, "error": msg}, status=400)
            try:
                _sync_customer_debt(bill=bill, actor=request.user)
            except ValueError as e:
                transaction.set_rollback(True)
                return JsonResponse({"ok": False, "error": str(e)}, status=400)
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

            transaction.on_commit(lambda: AuditSV.log_event_safe(
                action=AuditAction.INFO,
                actor=request.user,
                request=request,
                target=bill,
                title=title,
                message=title,
                source="pos.api_bill_save",
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


@role_required_api(AccountProfile.Role.CASHIER, AccountProfile.Role.MANAGER)
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
    if not has_role(request.user, AccountProfile.Role.MANAGER):
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


@role_required_api(AccountProfile.Role.CASHIER, AccountProfile.Role.MANAGER)
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
    is_manager = has_role(request.user, AccountProfile.Role.MANAGER)
    if not is_manager:
        if bill.cashier and bill.cashier != request.user:
            return JsonResponse({"ok": False, "error": "PERMISSION_DENIED"}, status=403)

    if bill.is_deleted and not is_manager:
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



@role_required_api(AccountProfile.Role.CASHIER, AccountProfile.Role.MANAGER)
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


@role_required_api(AccountProfile.Role.CASHIER, AccountProfile.Role.MANAGER)
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
    if bill.cashier and bill.cashier != request.user and not has_role(request.user, AccountProfile.Role.MANAGER):
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


        transaction.on_commit(lambda: AuditSV.log_event_safe(
            action=AuditAction.INFO,
            actor=request.user,
            request=request,
            target=bill,
            title="POS bill deleted (parked)",
            message="POS bill deleted (parked)",
            source="pos.api_bill_delete",
            meta=meta,
        ))

    return JsonResponse({"ok": True})
