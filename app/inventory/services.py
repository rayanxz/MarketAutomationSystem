# app/inventory/services.py
from __future__ import annotations

from decimal import Decimal
from typing import Optional, Dict, Any

from django.db import transaction
from django.utils import timezone

from catalog.models import Product
from inventory.models import ProductMovement, q3, q4, DEC0
from stock.models import ProductContainer


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
    )

    # Update product stock + optional fields
    current = product.stock_qty or DEC0
    product.stock_qty = q3(current + qty_primary_q)

    update_fields = ["stock_qty"]

    if extra_product_updates:
        for field_name, value in extra_product_updates.items():
            setattr(product, field_name, value)
            update_fields.append(field_name)

    if hasattr(product, "updated_at"):
        product.updated_at = timezone.now()
        update_fields.append("updated_at")

    # no duplicates in update_fields
    product.save(update_fields=list(dict.fromkeys(update_fields)))

    if container is not None:
        from stock import services as StockSV
        StockSV.apply_movement(mv)

    return mv


@transaction.atomic
def record_purchase_item(
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
    extra_product_updates: Optional[Dict[str, Any]] = None,
) -> ProductMovement:
    return record_movement(
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
    )


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
) -> ProductMovement:
    # qty_primary should be NEGATIVE here (stock goes out)
    return record_movement(
        actor=actor,
        product=product,
        unit_index=unit_index,
        qty_primary=qty_primary,
        unit_cost=unit_cost,
        movement_type=ProductMovement.MovementType.PROVIDER_RETURN,
        source_app=source_app,
        source_model=source_model,
        source_id=source_id,
        container=container
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
    Logs a SALE movement (stock goes OUT of the container).

    qty_primary should be POSITIVE here; we flip it to negative inside.
    """
    qty_primary = Decimal(str(qty_primary or 0))
    if qty_primary > 0:
        qty_primary = -qty_primary  # stock out
    return record_movement(
        actor=actor,
        product=product,
        unit_index=unit_index,
        qty_primary=qty_primary,
        unit_cost=unit_cost,
        movement_type=ProductMovement.MovementType.SALE,
        source_app=source_app,
        source_model=source_model,
        source_id=source_id,
        container=container,
    )
