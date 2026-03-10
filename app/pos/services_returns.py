# app/pos/services_returns.py
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Iterable, Dict, Any

from django.db import transaction
from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.utils import timezone

from catalog.models import Product
from inventory.models import DEC0, q3, q4, ProductMovement
from inventory import services as InvSV
from inventory.models import SaleCostPart
from stock import services as StockSV
from stock.models import ProductContainer
from stock.services import MissingCostBasisError
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


def _q_money(currency_code: str, amount: Decimal) -> Decimal:
    return FinSV.q_money(amount=Decimal(amount or DEC0), currency_code=(currency_code or "SYP").upper())


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
    return DebtSV.resolve_debtor_entry_for_source(
        source_app="pos",
        source_model="SalesBill",
        source_id=str(bill_id),
        currency_code=(currency_code or "SYP").upper(),
        for_update=True,
    )


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
    cost_currencies: set[str] = set()

    for p in parts:
        total_cost = q3(total_cost + q3(_dec(p.total_cost)))
        total_qty = q3(total_qty + q3(_dec(p.qty_primary)))
        if p.fifo_layer and getattr(p.fifo_layer, "cost_currency", None):
            cur = (p.fifo_layer.cost_currency or "").upper()
            if cur in ("SYP", "USD"):
                cost_currencies.add(cur)

    if total_qty > DEC0:
        if len(cost_currencies) != 1:
            raise MissingCostBasisError(
                f"Missing or mixed sale cost currencies for product {product_id} on bill {bill_id}."
            )
        return q4(total_cost / total_qty), next(iter(cost_currencies))

    raise MissingCostBasisError(
        f"Missing sale cost parts for product {product_id} on bill {bill_id}."
    )


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
    products = {p.id: p for p in Product.objects.select_for_update().filter(id__in=product_ids, is_active=True)}
    if len(products) != len(product_ids):
        raise ValidationError("Product is archived and cannot be used in new operations.")

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

        qty_input = abs(_dec(payload.get("qty")))
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
        row_currency = (sale_row.sale_currency or "SYP").upper()
        line_total = _q_money(row_currency, (unit_price_primary * qty_primary) - disc_part)
        if line_total < DEC0:
            line_total = DEC0

        SalesReturnRow.objects.create(
            ret=ret,
            sale_row=sale_row,
            product=product,
            product_name_at_txn=(getattr(sale_row, "product_name", "") or "").strip(),
            uom_index=int(sale_row.uom_index or 1),
            conv_factor_at_txn=conv_at_txn or Decimal("1"),
            unit_1_label_at_txn=(getattr(sale_row, "unit_1_label_at_txn", "") or "").strip(),
            unit_2_label_at_txn=(getattr(sale_row, "unit_2_label_at_txn", "") or "").strip(),
            qty_used_at_txn=qty_input,
            qty_returned=qty_primary,
            currency_code=(sale_row.sale_currency or "SYP").upper(),
            unit_price_at_sale=unit_price_primary,
            discount_amount_at_txn=q3(sale_row.disc_amount or DEC0),
            discount_pct_at_txn=q3(sale_row.disc_pct or DEC0),
            fx_rate_at_txn=bill.fx_rate_used,
            line_total=line_total,
            reason=reason,
        )

        if row_currency == "USD":
            total_usd = _q_money("USD", total_usd + line_total)
        else:
            total_syp = _q_money("SYP", total_syp + line_total)

    if not ret.rows.exists():
        ret.delete()
        raise ValueError("No return items were selected.")

    ret.total_syp = _q_money("SYP", total_syp)
    ret.total_usd = _q_money("USD", total_usd)
    ret.save(update_fields=["total_syp", "total_usd"])

    AuditSV.log_create_safe(
        actor=actor,
        target=ret,
        title="POS sales return draft",
        message=f"Draft sales return #{ret.serial or ret.id}",
        source="pos.services_returns.create_sales_return_draft",
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
                total_usd = _q_money("USD", total_usd + _dec(r.line_total))
            else:
                total_syp = _q_money("SYP", total_syp + _dec(r.line_total))

        if total_syp <= DEC0 and total_usd <= DEC0:
            raise ValueError("Return total must be > 0.")

        ret.total_syp = _q_money("SYP", total_syp)
        ret.total_usd = _q_money("USD", total_usd)
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
                raise MissingCostBasisError(
                    f"Missing return cost basis for product {product.id}."
                )

            update_fields = []
            if hasattr(r, "unit_cost_at_txn"):
                r.unit_cost_at_txn = unit_cost
                update_fields.append("unit_cost_at_txn")
            if hasattr(r, "cost_currency_at_txn"):
                r.cost_currency_at_txn = (cost_currency or "SYP").upper()
                update_fields.append("cost_currency_at_txn")
            if hasattr(r, "fx_rate_at_txn") and r.fx_rate_at_txn in (None, ""):
                r.fx_rate_at_txn = bill.fx_rate_used or FinSV.get_current_fx_syp_per_usd()
                update_fields.append("fx_rate_at_txn")
            if hasattr(r, "product_name_at_txn") and not (r.product_name_at_txn or "").strip():
                r.product_name_at_txn = getattr(product, "name", "") or ""
                update_fields.append("product_name_at_txn")
            if update_fields:
                r.save(update_fields=update_fields)

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
                cost_currency=cost_currency,
                movement_type=ProductMovement.MovementType.SALE_RETURN,
                source_app="pos",
                source_model="SalesReturn",
                source_id=str(ret.id),
                container=ret.stock_container,
                origin_source_app="pos",
                origin_source_model="SalesBill",
                origin_source_id=str(bill.id),
                product_name_at_txn=(getattr(r, "product_name_at_txn", "") or getattr(product, "name", "") or ""),
                sale_unit_price_at_txn=getattr(r, "unit_price_at_sale", None),
                sale_currency_at_txn=getattr(r, "currency_code", None),
                fx_rate_at_txn=bill.fx_rate_used,
                qty_used_at_txn=abs(getattr(r, "qty_used_at_txn", DEC0) or DEC0),
                qty_primary_at_txn=qty_primary,
                unit_index_used_at_txn=int(r.uom_index or 1),
                conversion_factor_at_txn=getattr(r, "conv_factor_at_txn", None),
                unit_1_label_at_txn=getattr(r, "unit_1_label_at_txn", "") or "",
                unit_2_label_at_txn=getattr(r, "unit_2_label_at_txn", "") or "",
                discount_amount_at_txn=getattr(r, "discount_amount_at_txn", None),
                discount_pct_at_txn=getattr(r, "discount_pct_at_txn", None),
            )

        fx_rate = bill.fx_rate_used or FinSV.get_current_fx_syp_per_usd()

        def _apply_debt_and_settle(*, amount: Decimal, currency_code: str) -> tuple[Decimal, Decimal]:
            if amount <= DEC0:
                return DEC0, DEC0

            cur = (currency_code or "SYP").upper()
            total_amount = _q_money(cur, amount)
            reduce_amount = DEC0

            entry = None
            if bill.customer_id:
                entry = _find_debtor_entry(bill_id=bill.id, currency_code=cur)
                if entry and entry.remaining > DEC0:
                    reduce_amount = _q_money(cur, min(total_amount, entry.remaining))

            remaining_amount = _q_money(cur, total_amount - reduce_amount)

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
                if _q_money(cur, _dec(bal)) < remaining_amount:
                    raise ValueError(f"Insufficient funds after debt reduction for {cur}")

            if reduce_amount > DEC0 and entry is not None:
                entry.total = _q_money(cur, (entry.total or DEC0) - reduce_amount)
                entry.status = DebtorDebt.Status.CLOSED if entry.remaining <= DEC0 else DebtorDebt.Status.OPEN
                entry.save(update_fields=["total", "status"])

                cp = _ensure_customer_counterparty(customer=bill.customer)
                FinSV.post_counterparty_adjust_with_fx(
                    actor=actor,
                    counterparty_id=cp.id,
                    currency_code=cur,
                    amount_signed=-reduce_amount,
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
                    amount_signed=-remaining_amount,
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

            AuditSV.log_update_safe(
                actor=actor,
                target=ret,
                title="POS sales return posted",
                message=f"Posted sales return #{ret.serial or ret.id}",
                source="pos.services_returns.post_sales_return",
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

        if rem_syp <= DEC0 and rem_usd <= DEC0 and (red_syp > DEC0 or red_usd > DEC0):
            return _mark_posted()

        return _mark_posted()
    except Exception as e:
        if isinstance(e, (ValueError, ValidationError, MissingCostBasisError)):
            raise
        logger.exception(
            "post_sales_return unexpected failure",
            extra={
                "return_id": return_id,
                "money_container_id": money_container_id,
                "remaining_refund_syp": str(dbg_rem_syp),
                "remaining_refund_usd": str(dbg_rem_usd),
                "debt_reduced_syp": str(dbg_red_syp),
                "debt_reduced_usd": str(dbg_red_usd),
                "return_status": dbg_ret_status,
                "sale_bill_id": dbg_bill_id,
                "customer_id": dbg_customer_id,
            },
        )
        raise RuntimeError("Failed to post sales return.") from e
