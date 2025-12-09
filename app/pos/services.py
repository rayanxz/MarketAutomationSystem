# app/pos/services.py
from __future__ import annotations

from decimal import Decimal
from collections import defaultdict
from typing import Tuple

from django.db import transaction

from catalog.models import Product
from inventory import services as InvSV
from inventory.models import ProductMovement, DEC0, q3
from stock.models import ProductContainer, StockEntry  # ⬅ added StockEntry
from .models import SalesBill, SalesBillRow


class InsufficientStockError(Exception):
    """
    Raised when trying to finalize a POS bill that would cause stock
    in the store container to go below zero.

    `items` is a list of dicts:
      {
        "product_id": int,
        "product_name": str,
        "needed": Decimal,
        "available": Decimal,
      }
    """
    def __init__(self, items: list[dict]):
        self.items = items
        super().__init__("INSUFFICIENT_STOCK")


def _get_store_container() -> ProductContainer | None:
    """
    Main POS container: we prefer is_store=True; fallback to code='store'.
    """
    c = ProductContainer.objects.filter(is_store=True).first()
    if c:
        return c
    return ProductContainer.objects.filter(code="store").first()


def _qty_to_primary(
    product: Product,
    uom_index: int,
    qty: Decimal,
) -> Tuple[Decimal, int]:
    """
    Convert qty based on UOM index to primary units.
    Returns (qty_primary, actual_unit_index_used).
    """
    qty = q3(qty or DEC0)
    if qty <= 0:
        return DEC0, 1

    conv = getattr(product, "conversion_factor", None) or Decimal("1")
    if uom_index == 2:
        # secondary → primary
        return q3(qty * conv), 2
    return qty, 1


@transaction.atomic
def finalize_pos_bill(*, bill: SalesBill, actor) -> None:
    """
    Called when a POS bill is saved as FINAL (not parked).
    Creates ProductMovement rows (movement_type='sale') per SalesBillRow.

    - Only runs once per bill (idempotent via check on existing movements).
    - Uses 'pos' / 'SalesBill' as source.
    - Works only for the main store container.
    - NOW: validates that the store container has enough stock for all
      products in this bill. If not enough, raises InsufficientStockError
      and DOES NOT post any movements.
    """
    # Only act on finalized bills
    if not bill.finalized or bill.parked:
        return

    # Avoid double-posting if something calls us twice
    already = ProductMovement.objects.filter(
        source_app="pos",
        source_model="SalesBill",
        source_id=str(bill.id),
    ).exists()
    if already:
        return

    container = _get_store_container()
    if container is None:
        # No store container: for now, silently skip posting
        # (you may later turn this into a hard error)
        return

    rows: list[SalesBillRow] = list(bill.rows.all())
    if not rows:
        return

    # ==========================
    # 1) Aggregate required qty per product (in PRIMARY units)
    # ==========================
    needed_by_product: dict[int, Decimal] = defaultdict(lambda: DEC0)
    product_cache: dict[int, Product] = {}

    for row in rows:
        # Might be a product that was deleted from catalog; skip
        try:
            product = product_cache.get(row.product_id)
            if product is None:
                product = Product.objects.get(pk=row.product_id)
                product_cache[row.product_id] = product
        except Product.DoesNotExist:
            continue

        qty_primary, _unit_index_used = _qty_to_primary(
            product, row.uom_index, row.qty
        )
        if qty_primary <= 0:
            continue

        needed_by_product[product.id] = q3(
            (needed_by_product[product.id] or DEC0) + qty_primary
        )

    if not needed_by_product:
        # nothing actually sold
        return

    # ==========================
    # 2) Load current stock for these products in the store container
    #    and lock rows to avoid race conditions.
    # ==========================
    entries = (
        StockEntry.objects
        .select_for_update()
        .filter(
            product_id__in=needed_by_product.keys(),
            container=container,
        )
    )

    available_by_product: dict[int, Decimal] = {}
    for e in entries:
        available_by_product[e.product_id] = q3(e.qty_primary or DEC0)

    # ==========================
    # 3) Detect shortages
    # ==========================
    shortages: list[dict] = []

    for pid, required in needed_by_product.items():
        available = available_by_product.get(pid, DEC0)
        if required > available:
            prod = product_cache.get(pid)
            shortages.append(
                {
                    "product_id": pid,
                    "product_name": prod.name if prod else "",
                    "needed": required,
                    "available": available,
                }
            )

    if shortages:
        # DO NOT post any sale movements; let caller handle this
        raise InsufficientStockError(shortages)

    # ==========================
    # 4) All good → post SALE movements (FIFO cost handled in InvSV)
    # ==========================
    for row in rows:
        # Might be a product that was deleted from catalog; skip
        try:
            product = Product.objects.get(pk=row.product_id)
        except Product.DoesNotExist:
            continue

        qty_primary, unit_index_used = _qty_to_primary(
            product, row.uom_index, row.qty
        )
        if qty_primary <= 0:
            continue

        # For initial cost hint, we use product.cost if it exists, otherwise 0.
        # record_sale_item will override this with FIFO cost if container is set.
        unit_cost = getattr(product, "cost", None)
        if unit_cost is None:
            unit_cost = DEC0

        InvSV.record_sale_item(
            actor=actor,
            product=product,
            unit_index=unit_index_used,
            qty_primary=qty_primary,   # POSITIVE; wrapper will flip it to negative
            unit_cost=unit_cost,
            source_app="pos",
            source_model="SalesBill",
            source_id=bill.id,
            container=container,
        )
