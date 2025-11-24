# app/stock/services.py
from __future__ import annotations

from decimal import Decimal
from typing import Iterable

from django.db import transaction

from catalog.models import Product
from inventory.models import ProductMovement, q3
from stock.models import ProductContainer, StockEntry, DEC0

from django.utils import timezone
from inventory import services as InvSV


@transaction.atomic
def apply_movement(mv: ProductMovement) -> StockEntry | None:
    """
    Apply a single ProductMovement to stock snapshot.

    - If mv.container is None => do nothing (legacy / not routed yet).
    - Otherwise, adjust StockEntry for (product, container) by mv.qty_primary.
    """
    container = getattr(mv, "container", None)
    if not container:
        return None

    qty = q3(Decimal(str(mv.qty_primary or DEC0)))

    entry, _ = StockEntry.objects.select_for_update().get_or_create(
        product=mv.product,
        container=container,
        defaults={"qty_primary": DEC0},
    )

    entry.qty_primary = q3((entry.qty_primary or DEC0) + qty)
    # We are NOT touching avg_unit_cost yet – we’ll design costing rules later.
    entry.save(update_fields=["qty_primary", "updated_at"])
    return entry


@transaction.atomic
def apply_movements(movements: Iterable[ProductMovement]) -> None:
    """
    Apply a sequence of movements. Convenient when posting a bill.
    """
    for mv in movements:
        apply_movement(mv)


@transaction.atomic
def rebuild_all_from_inventory() -> None:
    """
    Nuclear option: wipe StockEntry and rebuild from ProductMovement.
    Safe because inventory is the single source of truth.
    """
    StockEntry.objects.all().delete()

    # Process movements oldest → newest so aggregates are correct
    qs = ProductMovement.objects.select_related("product", "container").order_by("created_at", "id")
    for mv in qs.iterator():
        apply_movement(mv)


def get_stock_for_product(product: Product) -> dict:
    """
    Helper: returns per-container quantities for a product.
    {'store': '10.000', 'wh1': '50.000', ...}
    """
    entries = (
        StockEntry.objects
        .filter(product=product)
        .select_related("container")
        .order_by("container__sort_order", "container__name")
    )
    return {
        e.container.code: str(e.qty_primary or DEC0)
        for e in entries
    }


@transaction.atomic
def transfer_between_containers(
    *,
    actor,
    product: Product,
    from_container: ProductContainer,
    to_container: ProductContainer,
    qty_primary: Decimal,
) -> tuple[ProductMovement, ProductMovement]:
    """
    Move qty_primary (primary unit, positive) from one container to another.

    Creates two ProductMovement rows:
      - ADJUSTMENT negative from 'from_container'
      - ADJUSTMENT positive into 'to_container'

    Overall product.stock_qty stays the same (out + in).
    """
    qty = q3(Decimal(str(qty_primary or DEC0)))
    if qty <= DEC0:
        raise ValueError("Quantity must be positive for transfer.")

    unit_cost = getattr(product, "cost", DEC0) or DEC0

    # Just some reference so both legs are logically linked
    ref = timezone.now().strftime("TX%Y%m%d%H%M%S")

    mv_out = InvSV.record_movement(
        actor=actor,
        product=product,
        unit_index=ProductMovement.UnitIndex.PRIMARY,
        qty_primary=-qty,
        unit_cost=unit_cost,
        movement_type=ProductMovement.MovementType.ADJUSTMENT,
        source_app="stock",
        source_model="Transfer",
        source_id=f"{ref}-OUT",
        container=from_container,
    )

    mv_in = InvSV.record_movement(
        actor=actor,
        product=product,
        unit_index=ProductMovement.UnitIndex.PRIMARY,
        qty_primary=qty,
        unit_cost=unit_cost,
        movement_type=ProductMovement.MovementType.ADJUSTMENT,
        source_app="stock",
        source_model="Transfer",
        source_id=f"{ref}-IN",
        container=to_container,
    )

    return mv_out, mv_in