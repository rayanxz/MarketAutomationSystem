# app/stock/services.py
from __future__ import annotations

from decimal import Decimal
from typing import Iterable

from django.db import transaction
from django.db.models import Sum
from django.conf import settings

from catalog.models import Product
from inventory.models import ProductMovement, q3 , q4
from stock.models import ProductContainer, StockEntry , StockFifoLayer, DEC0

from django.utils import timezone
from inventory import services as InvSV



def _fifo_sum_for(product: Product, container: ProductContainer) -> Decimal:
    """
    Sum of remaining FIFO quantity for given product in given container.
    This is the single source of truth for stock.
    """
    agg = (
        StockFifoLayer.objects
        .filter(product=product, container=container)
        .aggregate(s=Sum("qty_remaining"))
    )
    return (agg["s"] or DEC0).quantize(Decimal("0.001"))


def sync_entry_from_fifo(product: Product, container: ProductContainer) -> StockEntry:
    """
    Recalculate StockEntry.qty_primary from FIFO layers and save.
    This function should be called after ANY operation that changes stock.
    """
    total = _fifo_sum_for(product, container)

    entry, _ = StockEntry.objects.get_or_create(
        product=product,
        container=container,
        defaults={"qty_primary": DEC0, "avg_unit_cost": DEC0},
    )
    if entry.qty_primary != total:
        entry.qty_primary = total
        entry.save(update_fields=["qty_primary"])

    return entry


def assert_entry_matches_fifo(product: Product, container: ProductContainer) -> None:
    """
    Debug-time guard: if StockEntry and FIFO diverge, blow up (in DEBUG)
    or at least log + fix in production.
    """
    total_fifo = _fifo_sum_for(product, container)
    entry = (
        StockEntry.objects
        .filter(product=product, container=container)
        .first()
    )

    if entry is None:
        # if no entry, fifo sum must be zero
        if total_fifo != DEC0:
            msg = (
                f"Stock inconsistency: no StockEntry for product={product.id}, "
                f"container={container.code}, but FIFO total={total_fifo}"
            )
            if settings.DEBUG:
                raise RuntimeError(msg)
            # In production, auto-create entry
            StockEntry.objects.create(
                product=product,
                container=container,
                qty_primary=total_fifo,
                avg_unit_cost=DEC0,
            )
        return

    if entry.qty_primary != total_fifo:
        msg = (
            f"Stock inconsistency for product={product.id}, container={container.code}: "
            f"StockEntry.qty_primary={entry.qty_primary}, FIFO total={total_fifo}"
        )
        if settings.DEBUG:
            # explode loudly while developing
            raise RuntimeError(msg)
        else:
            # in production: log + auto-fix instead of killing the cashier
            import logging
            logger = logging.getLogger(__name__)
            logger.error(msg + " – auto-syncing entry from FIFO.")
            entry.qty_primary = total_fifo
            entry.save(update_fields=["qty_primary"])



# ===================== FIFO helpers =====================

@transaction.atomic
def fifo_add_incoming(
    *,
    product: Product,
    container: ProductContainer,
    qty_primary: Decimal,
    unit_cost: Decimal,
    source_app: str = "",
    source_model: str = "",
    source_id: str | int = "",
) -> None:
    """
    Create a FIFO layer for incoming stock (PURCHASE, SALE_RETURN, positive ADJUSTMENT…).

    qty_primary must be POSITIVE (primary unit).
    """
    if not container:
        return

    qty = q3(Decimal(str(qty_primary or DEC0)))
    if qty <= DEC0:
        return

    uc = q4(Decimal(str(unit_cost or DEC0)))

    StockFifoLayer.objects.create(
    product=product,
    container=container,
    qty_remaining=qty,
    unit_cost=uc,
    source_app=source_app or "",
    source_model=source_model or "",
    source_id=str(source_id or ""),
    )

    # لا نلمس StockEntry هنا.
    # StockEntry يتم تحديثه من خلال ProductMovement عبر apply_movement في inventory.




@transaction.atomic
def fifo_consume(
    *,
    product: Product,
    container: ProductContainer,
    qty_out_primary: Decimal,
) -> Decimal:
    """
    Consume FIFO layers for an OUTGO movement (SALE, PROVIDER_RETURN, negative ADJUSTMENT).

    - qty_out_primary must be POSITIVE (how much stock is going OUT).
    - Returns the *effective* unit cost (weighted average over all layers consumed).
    - If layers are not enough, we fall back to product.cost (or last layer cost)
      for the remaining quantity, without creating negative layers.
    """
    if not container:
        # No container = no per-container FIFO → fallback to product cost
        return q4(Decimal(str(getattr(product, "cost", DEC0) or DEC0)))

    need = q3(Decimal(str(qty_out_primary or DEC0)))
    if need <= DEC0:
        return q4(Decimal(str(getattr(product, "cost", DEC0) or DEC0)))

    layers = (
        StockFifoLayer.objects
        .select_for_update()
        .filter(product=product, container=container, qty_remaining__gt=DEC0)
        .order_by("created_at", "id")
    )

    remaining = need
    total_cost = DEC0
    last_cost: Decimal | None = None

    for layer in layers:
        if remaining <= DEC0:
            break

        avail = q3(layer.qty_remaining or DEC0)
        if avail <= DEC0:
            continue

        use = avail if avail <= remaining else remaining

        uc = q4(Decimal(str(layer.unit_cost or DEC0)))
        total_cost += q3(use) * uc

        layer.qty_remaining = q3(avail - use)
        layer.save(update_fields=["qty_remaining"])

        remaining -= use
        last_cost = uc

    # If we need more than what layers had, use fallback (product.cost or last layer)
    if remaining > DEC0:
        fallback_uc = q4(
            last_cost if last_cost is not None
            else Decimal(str(getattr(product, "cost", DEC0) or DEC0))
        )
        total_cost += q3(remaining) * fallback_uc

    if need <= DEC0:
        return q4(Decimal(str(getattr(product, "cost", DEC0) or DEC0)))

    eff_uc = total_cost / need
    eff_uc = q4(eff_uc)

    # لا نلمس StockEntry هنا أيضاً.
    # الحركات (ProductMovement) هي التي تعدّل StockEntry.
    return eff_uc




@transaction.atomic
def rebuild_fifo_from_inventory() -> None:
    """
    Wipe FIFO layers and rebuild them purely from ProductMovement history.

    - Processes movements oldest → newest.
    - For incoming movements (qty > 0): create layers.
    - For outgoing movements (qty < 0): consume layers FIFO.
    """
    from inventory.models import ProductMovement  # local import

    StockFifoLayer.objects.all().delete()

    qs = (
        ProductMovement.objects
        .select_related("product", "container")
        .order_by("created_at", "id")
    )

    for mv in qs.iterator():
        if not mv.container_id:
            continue

        qty = q3(Decimal(str(mv.qty_primary or DEC0)))
        if qty > DEC0:
            # incoming – treat as one FIFO layer
            fifo_add_incoming(
                product=mv.product,
                container=mv.container,
                qty_primary=qty,
                unit_cost=mv.unit_cost,
                source_app=mv.source_app,
                source_model=mv.source_model,
                source_id=mv.source_id,
            )
        elif qty < DEC0:
            # outgoing – consume FIFO layers (ignore returned cost)
            fifo_consume(
                product=mv.product,
                container=mv.container,
                qty_out_primary=-qty,
            )




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
def transfer_from_batch(
    *,
    actor,
    batch: StockFifoLayer,
    to_container: ProductContainer,
    qty_primary: Decimal,
) -> tuple[ProductMovement, ProductMovement]:
    """
    Move qty_primary from a specific FIFO batch (StockFifoLayer) in its current container
    into another container.

    - qty_primary must be > 0 and <= batch.qty_remaining
    - Creates:
        * OUT ADJUSTMENT movement from batch.container
        * IN  ADJUSTMENT movement into to_container
    - Decreases batch.qty_remaining
    - Creates a new FIFO layer in the target container with the same cost
      AND PRESERVES the original source_app/source_model/source_id
      so we can track the originating bill item across containers.
    """
    qty = q3(Decimal(str(qty_primary or DEC0)))
    if qty <= DEC0:
        raise ValueError("Quantity must be positive for transfer.")

    from_container = batch.container
    product = batch.product

    if not from_container:
        raise ValueError("Batch has no source container.")

    if to_container.id == from_container.id:
        raise ValueError("Source and target containers must differ.")

    current_remain = q3(Decimal(str(batch.qty_remaining or DEC0)))
    if qty > current_remain:
        raise ValueError("Cannot move more than batch remaining quantity.")

    unit_cost = q4(Decimal(str(batch.unit_cost or DEC0)))

    # Just some reference so both legs are logically linked
    ref = timezone.now().strftime("TX%Y%m%d%H%M%S")

    # OUT movement: negative ADJUSTMENT in source container
    mv_out = InvSV.record_movement(
        actor=actor,
        product=product,
        unit_index=ProductMovement.UnitIndex.PRIMARY,
        qty_primary=-qty,  # stock out from source
        unit_cost=unit_cost,
        movement_type=ProductMovement.MovementType.ADJUSTMENT,
        source_app="stock",
        source_model="TransferBatch",
        source_id=f"{ref}-OUT",
        container=from_container,
    )

    # Decrease remaining qty on the source batch
    batch.qty_remaining = q3(current_remain - qty)
    batch.save(update_fields=["qty_remaining"])

    # 🔥 HERE: new FIFO layer in destination with SAME origin identity as the original batch
    fifo_add_incoming(
        product=product,
        container=to_container,
        qty_primary=qty,
        unit_cost=unit_cost,
        source_app=batch.source_app or "",
        source_model=batch.source_model or "",
        source_id=batch.source_id or "",
    )

    # IN movement: positive ADJUSTMENT in target container
    mv_in = InvSV.record_movement(
        actor=actor,
        product=product,
        unit_index=ProductMovement.UnitIndex.PRIMARY,
        qty_primary=qty,  # stock in to target
        unit_cost=unit_cost,
        movement_type=ProductMovement.MovementType.ADJUSTMENT,
        source_app="stock",
        source_model="TransferBatch",
        source_id=f"{ref}-IN",
        container=to_container,
    )

    # 🔥 after FIFO + movements: force StockEntry to match FIFO on both sides
    sync_entry_from_fifo(product, from_container)
    sync_entry_from_fifo(product, to_container)
    assert_entry_matches_fifo(product, from_container)
    assert_entry_matches_fifo(product, to_container)

    return mv_out, mv_in

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
    Legacy generic transfer (without choosing a specific batch).

    Kept for compatibility if you use it somewhere else.
    NOT used by the stock_move UI anymore (that uses transfer_from_batch).
    """
    qty = q3(Decimal(str(qty_primary or DEC0)))
    if qty <= DEC0:
        raise ValueError("Quantity must be positive for transfer.")

    unit_cost = getattr(product, "cost", DEC0) or DEC0

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
