# app/stock/services.py
from __future__ import annotations

from decimal import Decimal
from typing import Iterable

from django.db import transaction
from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.conf import settings

from catalog.models import Product
from inventory.models import ProductMovement, q3 , q4
from stock.models import ProductContainer, StockEntry , StockFifoLayer, DEC0

from django.utils import timezone

from datetime import datetime


def total_stock_primary(product) -> Decimal:
    agg = StockEntry.objects.filter(product=product).aggregate(s=Sum("qty_primary"))
    return (agg["s"] or DEC0)


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
    cost_currency: str | None = None,
    source_app: str = "",
    source_model: str = "",
    source_id: str | int = "",
    created_at: datetime | None = None,
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
    cur = (cost_currency or "SYP").upper()

    # Build query for "same logical batch"
    qs = (
        StockFifoLayer.objects
        .select_for_update()
        .filter(
            product=product,
            container=container,
            source_app=(source_app or ""),
            source_model=(source_model or ""),
            source_id=str(source_id or ""),
            unit_cost=uc,
            cost_currency=cur,
            qty_remaining__gt=DEC0,
        )
    )

    # ✅ If caller provides created_at, we treat that as part of the identity.
    # This prevents rebuild from merging different historical layers into one.
    if created_at is not None:
        qs = qs.filter(created_at=created_at)

    existing = qs.order_by("created_at", "id").first()


    if existing:
        existing.qty_remaining = q3(existing.qty_remaining + qty)
        existing.save(update_fields=["qty_remaining"])
    else:
        StockFifoLayer.objects.create(
            product=product,
            container=container,
            qty_remaining=qty,
            unit_cost=uc,
            cost_currency=cur,
            source_app=source_app or "",
            source_model=source_model or "",
            source_id=str(source_id or ""),
            created_at=created_at or timezone.now(),
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
def fifo_consume_scoped(
    *,
    product: Product,
    container: ProductContainer,
    qty_out_primary: Decimal,
    scope_source_app: str,
    scope_source_model: str,
    scope_source_id: str | int,
) -> Decimal:
    """
    Consume FIFO ONLY from layers that match the given scope.
    Used for provider-return-from-specific-bill-item logic.
    """
    if not container:
        return q4(Decimal(str(getattr(product, "cost", DEC0) or DEC0)))

    need = q3(Decimal(str(qty_out_primary or DEC0)))
    if need <= DEC0:
        return q4(Decimal(str(getattr(product, "cost", DEC0) or DEC0)))

    layers = (
        StockFifoLayer.objects
        .select_for_update()
        .filter(
            product=product,
            container=container,
            qty_remaining__gt=DEC0,
            source_app=(scope_source_app or ""),
            source_model=(scope_source_model or ""),
            source_id=str(scope_source_id or ""),
        )
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

    # IMPORTANT: scoped consume must NOT fallback to other batches
    if remaining > DEC0:
        raise ValueError("لا يمكن إرجاع كمية أكبر من المتبقي من نفس فاتورة الشراء (batch محدد).")

    eff_uc = q4(total_cost / need)
    return eff_uc



def fifo_consume_with_parts(
    *,
    product: Product,
    container: ProductContainer,
    qty_out_primary: Decimal,
) -> list[dict]:
    """
    Consume FIFO layers and RETURN detailed cost parts.

    Returns:
    [
      {
        "fifo_layer": StockFifoLayer | None,
        "qty_primary": Decimal,
        "unit_cost": Decimal,
        "total_cost": Decimal,
      },
      ...
    ]
    """
    if not container:
        # fallback: single fake part using product cost
        uc = q4(Decimal(str(getattr(product, "cost", DEC0) or DEC0)))
        qty = q3(qty_out_primary or DEC0)
        return [{
            "fifo_layer": None,
            "qty_primary": qty,
            "unit_cost": uc,
            "total_cost": q3(qty * uc),
        }]

    need = q3(Decimal(str(qty_out_primary or DEC0)))
    if need <= DEC0:
        return []

    layers = (
        StockFifoLayer.objects
        .select_for_update()
        .filter(product=product, container=container, qty_remaining__gt=DEC0)
        .order_by("created_at", "id")
    )

    remaining = need
    parts: list[dict] = []
    last_cost: Decimal | None = None

    for layer in layers:
        if remaining <= DEC0:
            break

        avail = q3(layer.qty_remaining)
        if avail <= DEC0:
            continue

        use = avail if avail <= remaining else remaining
        uc = q4(layer.unit_cost)

        parts.append({
            "fifo_layer": layer,
            "qty_primary": q3(use),
            "unit_cost": uc,
            "total_cost": q3(use * uc),
        })

        layer.qty_remaining = q3(avail - use)
        layer.save(update_fields=["qty_remaining"])

        remaining -= use
        last_cost = uc

        # fallback if FIFO not enough
        if remaining > DEC0:
            raise ValueError("INSUFFICIENT_FIFO_STOCK")


    return parts


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
            fifo_add_incoming(
                product=mv.product,
                container=mv.container,
                qty_primary=qty,
                unit_cost=mv.unit_cost,
                source_app=(mv.origin_source_app or mv.source_app or ""),
                source_model=(mv.origin_source_model or mv.source_model or ""),
                source_id=(mv.origin_source_id or mv.source_id or ""),
                created_at=mv.created_at,
            )

        elif qty < DEC0:
            # outgoing – consume FIFO layers
            if (mv.origin_source_app or mv.origin_source_model or mv.origin_source_id):
                fifo_consume_scoped(
                    product=mv.product,
                    container=mv.container,
                    qty_out_primary=-qty,
                    scope_source_app=(mv.origin_source_app or ""),
                    scope_source_model=(mv.origin_source_model or ""),
                    scope_source_id=(mv.origin_source_id or ""),
                )
            else:
                fifo_consume(
                    product=mv.product,
                    container=mv.container,
                    qty_out_primary=-qty,
                )


# app/stock/services.py
@transaction.atomic
def apply_movement(mv: ProductMovement) -> StockEntry | None:
    """
    Apply movement to StockEntry by syncing from FIFO.

    HARD INVARIANT:
    FIFO MUST already reflect this movement.
    """
    container = getattr(mv, "container", None)
    if not container:
        return None

    fifo_total_after = q3(
        (StockFifoLayer.objects
            .filter(product=mv.product, container=container)
            .aggregate(s=Sum("qty_remaining"))["s"]
        ) or DEC0
    )

    entry = (
        StockEntry.objects
        .select_for_update()
        .filter(product=mv.product, container=container)
        .first()
    )

    # ---- Only warn if mismatch indicates a PRE-EXISTING inconsistency ----
    if entry is not None:
        entry_before = q3(entry.qty_primary or DEC0)
        mv_qty = q3(mv.qty_primary or DEC0)
        expected_after = q3(entry_before + mv_qty)

        # If entry was consistent BEFORE, then entry_before + mv_qty should match FIFO after.
        if expected_after != fifo_total_after:
            import logging
            logging.getLogger(__name__).warning(
                "FIFO/StockEntry mismatch BEFORE apply_movement – auto-healing "
                f"(product={mv.product.id}, container={container.code}, "
                f"type={mv.movement_type}, mv_qty={mv_qty}, "
                f"entry_before={entry_before}, expected_after={expected_after}, "
                f"fifo_after={fifo_total_after}, "
                f"src={mv.source_app}.{mv.source_model}#{mv.source_id})"
            )

    # ---- Sync StockEntry to FIFO (authoritative) ----
    StockEntry.objects.update_or_create(
        product=mv.product,
        container=container,
        defaults={"qty_primary": fifo_total_after, "avg_unit_cost": DEC0},
    )

    # Optional hard assert in DEBUG
    if settings.DEBUG:
        assert_entry_matches_fifo(mv.product, container)

    # Return fresh entry
    return StockEntry.objects.get(product=mv.product, container=container)





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
    TRUE nuclear rebuild:
    1) rebuild FIFO layers from ProductMovement history
    2) rebuild StockEntry cache from FIFO
    """
    # 1) rebuild FIFO from inventory movements
    rebuild_fifo_from_inventory()

    # 2) rebuild cache entries from FIFO
    StockEntry.objects.all().delete()

    pairs = (
        StockFifoLayer.objects
        .values_list("product_id", "container_id")
        .distinct()
    )

    for product_id, container_id in pairs:
        sync_entry_from_fifo(
            product=Product.objects.get(pk=product_id),
            container=ProductContainer.objects.get(pk=container_id),
        )


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
    ref: str | None = None,
    line_no : int | None = None,
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

    from inventory import services as InvSV

    batch = (
        StockFifoLayer.objects
        .select_for_update()
        .select_related("product", "container")
        .get(pk=batch.pk)
    )
    if not getattr(batch.product, "is_active", True):
        raise ValidationError("Product is archived and cannot be used in new operations.")

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

    # ✅ shared ref: passed from view (group), fallback if called standalone
    if not ref:
        ref = timezone.now().strftime("TX%Y%m%d%H%M%S")
    suffix = f"L{line_no}" if line_no is not None else "ROW"


    # 1) FIFO FIRST: decrease source batch
    batch.qty_remaining = q3(current_remain - qty)
    batch.save(update_fields=["qty_remaining"])

    # 2) FIFO: add incoming layer to destination (same origin identity)
    fifo_add_incoming(
        product=product,
        container=to_container,
        qty_primary=qty,
        unit_cost=unit_cost,
        source_app=batch.source_app or "",
        source_model=batch.source_model or "",
        source_id=batch.source_id or "",
        created_at=batch.created_at,
    )

    # 3) Now record movements (apply_movement will sync StockEntry from the *updated* FIFO)
    mv_out = InvSV.record_movement(
        actor=actor,
        product=product,
        unit_index=ProductMovement.UnitIndex.PRIMARY,
        qty_primary=-qty,
        unit_cost=unit_cost,
        movement_type=ProductMovement.MovementType.ADJUSTMENT,
        source_app="stock",
        source_model="TransferBatch",
        source_id=f"{ref}-{suffix}-OUT",
        container=from_container,
        origin_source_app=batch.source_app or "",
        origin_source_model=batch.source_model or "",
        origin_source_id=batch.source_id or "",
    )

    mv_in = InvSV.record_movement(
        actor=actor,
        product=product,
        unit_index=ProductMovement.UnitIndex.PRIMARY,
        qty_primary=qty,
        unit_cost=unit_cost,
        movement_type=ProductMovement.MovementType.ADJUSTMENT,
        source_app="stock",
        source_model="TransferBatch",
        source_id=f"{ref}-{suffix}-IN",
        container=to_container,
        origin_source_app=batch.source_app or "",
        origin_source_model=batch.source_model or "",
        origin_source_id=batch.source_id or "",
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
    raise RuntimeError(
        "transfer_between_containers is forbidden. "
        "Use transfer_from_batch (FIFO-safe) only."
    )

