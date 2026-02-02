# app/pos/services_returns.py
from __future__ import annotations

from decimal import Decimal
from typing import Iterable, Dict, Any
import logging

from django.db import transaction
from django.db.models import Sum, Q
from django.utils import timezone

from catalog.models import Product
from inventory.models import DEC0, q3, q4, ProductMovement
from inventory import services as InvSV
from inventory.models import SaleCostPart
from stock import services as StockSV
from stock.models import ProductContainer
from financials.models import MoneyContainer, MoneyContainerCurrency
from financials import services as FinSV
from debts import services as DebtSV
from debts.models import DebtorDebt, PartyType
from audit_log import services as AuditSV

from .models import SalesBill, SalesBillRow, SalesReturn, SalesReturnRow
from .services import _ensure_customer_counterparty

logger = logging.getLogger(__name__)


def _dec(x) -> Decimal:
    try:
        return Decimal(str(x or "0"))
    except Exception:
        return Decimal("0")


def _conv_for(product: Product, conv_override: Decimal | None = None) -> Decimal:
    conv = _dec(conv_override if conv_override is not None else (getattr(product, "conversion_factor", None) or "1"))
    if conv <= 0:
        conv = Decimal("1")
    return conv


def _qty_to_primary(*, qty_raw: Decimal, uom_index: int, product: Product, conv_override: Decimal | None = None) -> Decimal:
    qty = q3(_dec(qty_raw))
    if qty <= DEC0:
        return DEC0
    if getattr(product, "is_single_unit", False):
        uom_index = 1
    if int(uom_index or 1) == 2:
        qty = q3(qty * _conv_for(product, conv_override=conv_override))
    return q3(qty)


def _calc_row_disc_total(*, row: SalesBillRow, qty_primary_total: Decimal, unit_price_primary: Decimal) -> Decimal:
    base = q3(qty_primary_total * unit_price_primary)
    disc_amount_total = q3(_dec(row.disc_amount))
    disc_pct = _dec(row.disc_pct or 0)
    if disc_amount_total <= DEC0 and disc_pct > 0 and base > 0:
        disc_amount_total = q3((base * disc_pct) / Decimal("100"))
    if disc_amount_total < 0:
        disc_amount_total = DEC0
    if disc_amount_total > base:
        disc_amount_total = base
    return disc_amount_total


def returned_qty_by_sale_row(*, bill_id: int) -> dict[int, Decimal]:
    rows = (
        SalesReturnRow.objects
        .filter(ret__sale_bill_id=bill_id, ret__status=SalesReturn.Status.POSTED)
        .values("sale_row_id")
        .annotate(q=Sum("qty_returned"))
    )
    return {int(r["sale_row_id"]): q3(Decimal(str(r["q"] or DEC0))) for r in rows}


def _find_debtor_entry(*, bill_id: int, currency_code: str) -> DebtorDebt | None:
    cur = (currency_code or "SYP").upper()
    src = str(bill_id)
    qs = (
        DebtorDebt.objects
        .select_for_update()
        .filter(source_app="pos", source_model="SalesBill", currency_code=cur)
        .filter(
            Q(source_id=src)
            | Q(legacy_source_id=src)
            | Q(source_id=f"{src}:{cur}")
            | Q(legacy_source_id=f"{src}:{cur}")
        )
        .order_by("id")
    )
    return qs.first()


def _avg_sale_cost_for_bill_product(*, bill_id: int, product_id: int) -> tuple[Decimal, str]:
    parts = (
        SaleCostPart.objects
        .filter(
            movement__source_app="pos",
            movement__source_model="SalesBill",
            movement__source_id=str(bill_id),
            movement__product_id=product_id,
        )
        .select_related("fifo_layer")
    )

    total_cost = DEC0
    total_qty = DEC0
    cost_currency = "SYP"

    for p in parts:
        total_cost = q3(total_cost + q3(_dec(p.total_cost)))
        total_qty = q3(total_qty + q3(_dec(p.qty_primary)))
        if p.fifo_layer and getattr(p.fifo_layer, "cost_currency", None):
            cost_currency = (p.fifo_layer.cost_currency or cost_currency).upper()

    if total_qty > DEC0:
        return q4(total_cost / total_qty), cost_currency

    return q4(DEC0), cost_currency


def create_sales_return_draft(
    *,
    actor,
    sale_bill_id: int,
    stock_container_id: int,
    items: Iterable[Dict[str, Any]],
) -> SalesReturn:
    items = list(items)
    if not items:
        raise ValueError("No return items were selected.")

    bill = (
        SalesBill.objects
        .select_for_update()
        .select_related("customer")
        .prefetch_related("rows")
        .get(pk=sale_bill_id)
    )

    if bill.parked or not bill.finalized:
        raise ValueError("Cannot return items from a non-finalized bill.")

    container = ProductContainer.objects.select_for_update().get(pk=stock_container_id)
    if not container.is_active:
        raise ValueError("Selected stock container is inactive.")

    rows = list(bill.rows.all())
    row_map = {int(r.id): r for r in rows}

    product_ids = {int(r.product_id) for r in rows if r.product_id}
    products = {p.id: p for p in Product.objects.select_for_update().filter(id__in=product_ids)}

    returned_map = returned_qty_by_sale_row(bill_id=bill.id)

    ret = SalesReturn(
        sale_bill=bill,
        customer=bill.customer,
        stock_container=container,
        status=SalesReturn.Status.DRAFT,
        created_by=actor if getattr(actor, "is_authenticated", False) else None,
        total_syp=DEC0,
        total_usd=DEC0,
    )
    ret.save()

    total_syp = DEC0
    total_usd = DEC0

    aggregated: dict[int, dict[str, Any]] = {}
    for row in items:
        sale_row_id = int(row.get("sale_row_id") or 0)
        if sale_row_id <= 0:
            continue
        payload = aggregated.setdefault(sale_row_id, {"qty": DEC0, "reason": ""})
        payload["qty"] = q3(_dec(payload["qty"]) + _dec(row.get("qty")))
        if not payload["reason"] and (row.get("reason") or "").strip():
            payload["reason"] = (row.get("reason") or "").strip()[:255]

    for sale_row_id, payload in aggregated.items():
        sale_row = row_map.get(sale_row_id)
        if sale_row is None:
            raise ValueError("Invalid sale row in return items.")

        product = products.get(int(sale_row.product_id))
        if product is None:
            raise ValueError("Product not found for return row.")

        qty_input = _dec(payload.get("qty"))
        reason = (payload.get("reason") or "").strip()[:255]

        conv_at_txn = getattr(sale_row, "conv_factor_at_txn", None)
        qty_primary = _qty_to_primary(
            qty_raw=qty_input,
            uom_index=sale_row.uom_index,
            product=product,
            conv_override=conv_at_txn,
        )
        if qty_primary <= DEC0:
            continue

        sold_qty_primary = _qty_to_primary(
            qty_raw=sale_row.qty,
            uom_index=sale_row.uom_index,
            product=product,
            conv_override=conv_at_txn,
        )
        already_returned = returned_map.get(sale_row_id, DEC0)
        remaining = q3(sold_qty_primary - already_returned)
        if qty_primary > remaining:
            raise ValueError("Return qty exceeds remaining quantity for this row.")

        unit_price_primary = q4(_dec(sale_row.unit_price))
        disc_total = _calc_row_disc_total(
            row=sale_row,
            qty_primary_total=sold_qty_primary,
            unit_price_primary=unit_price_primary,
        )
        disc_part = q3((disc_total * qty_primary) / sold_qty_primary) if sold_qty_primary > DEC0 else DEC0
        line_total = q3((unit_price_primary * qty_primary) - disc_part)
        if line_total < DEC0:
            line_total = DEC0

        SalesReturnRow.objects.create(
            ret=ret,
            sale_row=sale_row,
            product=product,
            uom_index=int(sale_row.uom_index or 1),
            conv_factor_at_txn=conv_at_txn or Decimal("1"),
            unit_1_label_at_txn=(getattr(sale_row, "unit_1_label_at_txn", "") or "").strip(),
            unit_2_label_at_txn=(getattr(sale_row, "unit_2_label_at_txn", "") or "").strip(),
            qty_returned=qty_primary,
            currency_code=(sale_row.sale_currency or "SYP").upper(),
            unit_price_at_sale=unit_price_primary,
            line_total=line_total,
            reason=reason,
        )

        if (sale_row.sale_currency or "SYP").upper() == "USD":
            total_usd = q3(total_usd + line_total)
        else:
            total_syp = q3(total_syp + line_total)

    if not ret.rows.exists():
        ret.delete()
        raise ValueError("No return items were selected.")

    ret.total_syp = q3(total_syp)
    ret.total_usd = q3(total_usd)
    ret.save(update_fields=["total_syp", "total_usd"])

    AuditSV.log_create(
        actor=actor,
        target=ret,
        title="POS sales return draft",
        message=f"Draft sales return #{ret.serial or ret.id}",
        meta={
            "kind": "pos.sale_return_draft",
            "sale_bill_id": bill.id,
            "return_id": ret.id,
        },
    )

    return ret


@transaction.atomic
def post_sales_return(
    *,
    actor,
    return_id: int,
    settle_mode: str,
    money_container_id: int | None = None,
) -> SalesReturn:
    dbg_rem_syp = None
    dbg_rem_usd = None
    dbg_red_syp = None
    dbg_red_usd = None
    dbg_ret_status = None
    dbg_bill_id = None
    dbg_customer_id = None
    try:
        ret = (
            SalesReturn.objects
            .select_for_update()
            .select_related("sale_bill", "customer", "stock_container")
            .prefetch_related("rows__sale_row", "rows__product")
            .get(pk=return_id)
        )

        dbg_ret_status = ret.status

        if ret.status != SalesReturn.Status.DRAFT:
            raise ValueError("Return is not in draft state.")

        bill = ret.sale_bill
        dbg_bill_id = bill.id
        dbg_customer_id = bill.customer_id
        if bill.parked or not bill.finalized:
            raise ValueError("Cannot post return for non-finalized bill.")

        rows = list(ret.rows.all())
        if not rows:
            raise ValueError("Return has no items.")

        total_syp = DEC0
        total_usd = DEC0
        for r in rows:
            if (r.currency_code or "SYP").upper() == "USD":
                total_usd = q3(total_usd + q3(_dec(r.line_total)))
            else:
                total_syp = q3(total_syp + q3(_dec(r.line_total)))

        if total_syp <= DEC0 and total_usd <= DEC0:
            raise ValueError("Return total must be > 0.")

        ret.total_syp = q3(total_syp)
        ret.total_usd = q3(total_usd)
        ret.save(update_fields=["total_syp", "total_usd"])

        settle = (settle_mode or "cash").lower().strip()
        if settle not in {"cash", "credit"}:
            raise ValueError("Invalid settlement mode.")

        if settle == "credit" and not bill.customer_id:
            settle = "cash"

        # ----- Stock movements + FIFO (return IN) -----
        for r in rows:
            qty_primary = q3(_dec(r.qty_returned))
            if qty_primary <= DEC0:
                continue

            product = r.product
            unit_cost, cost_currency = _avg_sale_cost_for_bill_product(
                bill_id=bill.id,
                product_id=product.id,
            )
            if unit_cost <= DEC0:
                unit_cost = q4(_dec(getattr(product, "cost", DEC0)))
                if unit_cost <= DEC0:
                    logger.warning("No SaleCostPart and product cost missing for return product_id=%s", product.id)

            StockSV.fifo_add_incoming(
                product=product,
                container=ret.stock_container,
                qty_primary=qty_primary,
                unit_cost=unit_cost,
                cost_currency=cost_currency,
                source_app="pos",
                source_model="SalesReturn",
                source_id=str(ret.id),
            )

            InvSV.record_movement(
                actor=actor,
                product=product,
                unit_index=int(r.uom_index or 1),
                qty_primary=qty_primary,
                unit_cost=unit_cost,
                movement_type=ProductMovement.MovementType.SALE_RETURN,
                source_app="pos",
                source_model="SalesReturn",
                source_id=str(ret.id),
                container=ret.stock_container,
                origin_source_app="pos",
                origin_source_model="SalesBill",
                origin_source_id=str(bill.id),
            )

        fx_rate = bill.fx_rate_used or FinSV.get_current_fx_syp_per_usd()

        def _apply_debt_and_settle(*, amount: Decimal, currency_code: str) -> tuple[Decimal, Decimal]:
            if amount <= DEC0:
                return DEC0, DEC0

            cur = (currency_code or "SYP").upper()
            total_amount = q3(amount)
            reduce_amount = DEC0

            entry = None
            if bill.customer_id:
                entry = _find_debtor_entry(bill_id=bill.id, currency_code=cur)
                if entry and entry.remaining > DEC0:
                    reduce_amount = q3(min(total_amount, entry.remaining))

            remaining_amount = q3(total_amount - reduce_amount)

            if remaining_amount > DEC0 and settle == "cash":
                if not money_container_id:
                    raise ValueError("money_container_id is required for cash refunds")

                container = MoneyContainer.objects.select_for_update().get(pk=money_container_id)

                if not MoneyContainerCurrency.objects.filter(
                    container_id=container.id,
                    currency__code=cur,
                    is_enabled=True,
                ).exists():
                    raise ValueError(f"Currency {cur} is disabled for this container")

                container.refresh_from_db(fields=["balance_syp", "balance_usd"])
                bal = container.balance_usd if cur == "USD" else container.balance_syp
                if q3(_dec(bal)) < remaining_amount:
                    raise ValueError(f"Insufficient funds after debt reduction for {cur}")

            if reduce_amount > DEC0 and entry is not None:
                entry.total = q3((entry.total or DEC0) - reduce_amount)
                entry.status = DebtorDebt.Status.CLOSED if entry.remaining <= DEC0 else DebtorDebt.Status.OPEN
                entry.save(update_fields=["total", "status"])

                cp = _ensure_customer_counterparty(customer=bill.customer)
                FinSV.post_counterparty_adjust_with_fx(
                    actor=actor,
                    counterparty_id=cp.id,
                    currency_code=cur,
                    amount_signed=-q3(reduce_amount),
                    fx_syp_per_usd=fx_rate,
                    note=f"POS return debt reduce #{ret.serial or ret.id}",
                    source_app="pos",
                    source_model="SalesReturn",
                    source_id=str(ret.id),
                )

            if remaining_amount <= DEC0:
                return reduce_amount, DEC0

            if settle == "cash":
                container = MoneyContainer.objects.select_for_update().get(pk=money_container_id)
                FinSV.post_cash_withdraw(
                    actor=actor,
                    container_id=container.id,
                    currency_code=cur,
                    amount=remaining_amount,
                    note=f"POS sales return refund #{ret.serial or ret.id}",
                    source_app="pos",
                    source_model="SalesReturn",
                    source_id=str(ret.id),
                )
                return reduce_amount, remaining_amount

            if settle == "credit":
                DebtSV.create_creditor_entry(
                    provider=None,
                    total=remaining_amount,
                    collected=DEC0,
                    source_app="pos",
                    source_model="SalesReturn",
                    source_id=str(ret.id),
                    currency_code=cur,
                    doc_serial=ret.serial,
                    party_type=PartyType.CUSTOMER,
                    party_name=bill.customer_name or "",
                    customer_id=bill.customer_id,
                )

                cp = _ensure_customer_counterparty(customer=bill.customer)
                FinSV.post_counterparty_adjust_with_fx(
                    actor=actor,
                    counterparty_id=cp.id,
                    currency_code=cur,
                    amount_signed=-q3(remaining_amount),
                    fx_syp_per_usd=fx_rate,
                    note=f"POS return credit #{ret.serial or ret.id}",
                    source_app="pos",
                    source_model="SalesReturn",
                    source_id=str(ret.id),
                )
                return reduce_amount, remaining_amount

            return reduce_amount, remaining_amount

        def _mark_posted() -> SalesReturn:
            ret.status = SalesReturn.Status.POSTED
            ret.posted_at = timezone.now()
            ret.posted_by = actor if getattr(actor, "is_authenticated", False) else None
            ret.save(update_fields=["status", "posted_at", "posted_by"])

            AuditSV.log_update(
                actor=actor,
                target=ret,
                title="POS sales return posted",
                message=f"Posted sales return #{ret.serial or ret.id}",
                meta={
                    "kind": "pos.sale_return_posted",
                    "sale_bill_id": bill.id,
                    "return_id": ret.id,
                    "total_syp": str(ret.total_syp or DEC0),
                    "total_usd": str(ret.total_usd or DEC0),
                },
            )

            return ret

        red_syp, rem_syp = _apply_debt_and_settle(amount=total_syp, currency_code="SYP")
        red_usd, rem_usd = _apply_debt_and_settle(amount=total_usd, currency_code="USD")

        dbg_red_syp = red_syp
        dbg_red_usd = red_usd
        dbg_rem_syp = rem_syp
        dbg_rem_usd = rem_usd

        if q3(rem_syp + rem_usd) <= DEC0 and q3(red_syp + red_usd) > DEC0:
            return _mark_posted()

        return _mark_posted()
    except Exception as e:
        cls = e.__class__.__name__
        msg = str(e)
        raise ValueError(
            "post_sales_return error "
            f"cls={cls} msg={msg} "
            f"remaining_refund_syp={dbg_rem_syp} remaining_refund_usd={dbg_rem_usd} "
            f"debt_reduced_syp={dbg_red_syp} debt_reduced_usd={dbg_red_usd} "
            f"return_status={dbg_ret_status} sale_bill_id={dbg_bill_id} "
            f"customer_id={dbg_customer_id} money_container_id={money_container_id}"
        ) from e
