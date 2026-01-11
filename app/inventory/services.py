# app/inventory/services.py
from __future__ import annotations

from decimal import Decimal
from typing import Optional, Dict, Any

from django.db import transaction
from django.utils import timezone

from catalog.models import Product
from inventory.models import ProductMovement, q3, q4, DEC0
from stock.models import ProductContainer
from stock import services as StockSV


@transaction.atomic
def record_movement(
    *,
    actor,
    product: Product,
    unit_index: int,
    qty_primary: Decimal,
    unit_cost: Decimal,
    movement_type: str,
    source_app: str,
    source_model: str,
    source_id: str | int,
    container: ProductContainer | None = None,
    extra_product_updates: Optional[Dict[str, Any]] = None,
    origin_source_app: str = "",
    origin_source_model: str = "",
    origin_source_id: str | int = "",
) -> ProductMovement:
    """
    Core helper: logs a product movement AND updates product.stock_qty (and optional fields).

    - qty_primary should be signed:
        +ve => stock in
        -ve => stock out
    """
    qty_primary_q = q3(Decimal(str(qty_primary)))
    unit_cost_q = q4(Decimal(str(unit_cost)))
    total_cost_q = q3(abs(qty_primary_q) * unit_cost_q)

    mv = ProductMovement.objects.create(
        product=product,
        qty_primary=qty_primary_q,
        unit_index=unit_index,
        unit_cost=unit_cost_q,
        total_cost=total_cost_q,
        movement_type=movement_type,
        source_app=source_app,
        source_model=source_model,
        source_id=str(source_id),
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        container=container,
        origin_source_app=(origin_source_app or ""),
        origin_source_model=(origin_source_model or ""),
        origin_source_id=str(origin_source_id or ""),
    )

    # Collect product fields to update, then save ONCE
    update_fields: set[str] = set()

    # Apply allowed product updates (never stock_qty)
    if extra_product_updates:
        for k, v in extra_product_updates.items():
            if k == "stock_qty":
                continue
            if hasattr(product, k):
                setattr(product, k, v)
                update_fields.add(k)

    # Apply movement to FIFO/StockEntry cache, then update stock_qty cache
    if container is not None:
        StockSV.apply_movement(mv)
        product.stock_qty = q3(StockSV.total_stock_primary(product))
        update_fields.add("stock_qty")

    # Save product once (if anything changed)
    if update_fields:
        product.save(update_fields=sorted(update_fields))

    return mv




@transaction.atomic
def record_purchase_item(
    *,
    actor,
    product: Product,
    unit_index: int,
    qty_primary: Decimal,
    unit_cost: Decimal,
    cost_currency: str | None = None,
    source_app: str,
    source_model: str,
    source_id: str | int,
    container: ProductContainer | None = None,
    extra_product_updates: Optional[Dict[str, Any]] = None,
) -> ProductMovement:
    """
    Purchase item:

    - Logs a PURCHASE ProductMovement (qty_primary > 0).
    - Updates product.stock_qty.
    - Updates StockEntry snapshot (via StockSV.apply_movement inside record_movement).
    - Adds a FIFO layer per (product, container) if container is set.
    """

    if container is None:
        raise ValueError("container is required for purchases")

    # 1) FIFO FIRST (so StockEntry sync can see it)
    if container is not None:
        StockSV.fifo_add_incoming(
            product=product,
            container=container,
            qty_primary=qty_primary,
            unit_cost=unit_cost,
            cost_currency=cost_currency,
            source_app=source_app,
            source_model=source_model,
            source_id=source_id,
        )

    # 2) THEN movement (this triggers apply_movement -> sync_entry_from_fifo)
    mv = record_movement(
        actor=actor,
        product=product,
        unit_index=unit_index,
        qty_primary=qty_primary,
        unit_cost=unit_cost,
        movement_type=ProductMovement.MovementType.PURCHASE,
        source_app=source_app,
        source_model=source_model,
        source_id=source_id,
        container=container,
        extra_product_updates=extra_product_updates,
        origin_source_app=source_app,
        origin_source_model=source_model,
        origin_source_id=source_id,
    )

    return mv

@transaction.atomic
def record_provider_return_item(
    *,
    actor,
    product: Product,
    unit_index: int,
    qty_primary: Decimal,
    unit_cost: Decimal,
    source_app: str,
    source_model: str,
    source_id: str | int,
    container: ProductContainer | None = None,
    fifo_scope: dict[str, str] | None = None,
) -> ProductMovement:
    """
    Provider return (goods go BACK to provider):

    - qty_primary should be NEGATIVE (stock goes OUT).
    - We consume FIFO layers from this container and compute effective unit cost.
    """

    if container is None:
        # provider returns without container can't use FIFO; block it because it's unsafe
        raise ValueError("container is required for provider returns (FIFO requires container)")

    qty_primary_val = Decimal(str(qty_primary or 0))
    # ALWAYS treat provider return as OUT (negative)
    qty = -abs(qty_primary_val)


    qty_out = -qty  # positive amount going out

    eff_cost = unit_cost
    if container is not None:
        if fifo_scope:
            eff_cost = StockSV.fifo_consume_scoped(
                product=product,
                container=container,
                qty_out_primary=qty_out,
                scope_source_app=fifo_scope.get("source_app", ""),
                scope_source_model=fifo_scope.get("source_model", ""),
                scope_source_id=fifo_scope.get("source_id", ""),
            )
        else:
            eff_cost = StockSV.fifo_consume(
                product=product,
                container=container,
                qty_out_primary=qty_out,
            )


    return record_movement(
        actor=actor,
        product=product,
        unit_index=unit_index,
        qty_primary=qty,   # NEGATIVE
        unit_cost=eff_cost,
        movement_type=ProductMovement.MovementType.PROVIDER_RETURN,
        source_app=source_app,
        source_model=source_model,
        source_id=source_id,
        container=container,
        origin_source_app=(fifo_scope.get("source_app") if fifo_scope else ""),
        origin_source_model=(fifo_scope.get("source_model") if fifo_scope else ""),
        origin_source_id=(fifo_scope.get("source_id") if fifo_scope else ""),
    )



# ===== NEW: POS Sales =====
@transaction.atomic
def record_sale_item(
    *,
    actor,
    product: Product,
    unit_index: int,
    qty_primary: Decimal,
    unit_cost: Decimal,
    source_app: str,
    source_model: str,
    source_id: str | int,
    container: ProductContainer | None = None,
) -> ProductMovement:
    """
    Logs a SALE movement (stock goes OUT of the container) using FIFO cost.

    - Creates ONE ProductMovement (summary, weighted avg cost)
    - Creates MULTIPLE SaleCostPart rows (true FIFO breakdown)
    """

    if container is None:
        raise ValueError("container is required for sales (FIFO requires container)")


    from inventory.models import SaleCostPart
    from stock.services import fifo_consume_with_parts

    qty_pos = Decimal(str(qty_primary or 0))
    if qty_pos <= 0:
        qty_pos = abs(qty_pos) if qty_pos != 0 else Decimal("0")
    if qty_pos <= 0:
        return None
    # -----------------------------
    # FIFO consume WITH PARTS
    # -----------------------------
    parts = []
    if container is not None and qty_pos > 0:
        parts = fifo_consume_with_parts(
            product=product,
            container=container,
            qty_out_primary=qty_pos,
        )

    # -----------------------------
    # Compute weighted average cost
    # -----------------------------
    total_cost = DEC0
    total_qty = DEC0

    for p in parts:
        total_cost += p["total_cost"]
        total_qty += p["qty_primary"]

    if total_qty > DEC0:
        eff_cost = q4(total_cost / total_qty)
    else:
        eff_cost = q4(Decimal(str(unit_cost or DEC0)))

    # -----------------------------
    # Create ProductMovement
    # -----------------------------
    mv = record_movement(
        actor=actor,
        product=product,
        unit_index=unit_index,
        qty_primary=-qty_pos,  # SALE = stock out
        unit_cost=eff_cost,
        movement_type=ProductMovement.MovementType.SALE,
        source_app=source_app,
        source_model=source_model,
        source_id=source_id,
        container=container,
    )

    # -----------------------------
    # Save FIFO cost parts
    # -----------------------------
    for p in parts:
        SaleCostPart.objects.create(
            movement=mv,
            fifo_layer=p["fifo_layer"],
            qty_primary=p["qty_primary"],
            unit_cost=p["unit_cost"],
            total_cost=p["total_cost"],
        )

    return mv


