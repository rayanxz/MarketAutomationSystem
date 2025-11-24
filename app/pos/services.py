# app/pos/services.py
from __future__ import annotations

from decimal import Decimal

from django.db import transaction

from catalog.models import Product
from inventory import services as InvSV
from inventory.models import ProductMovement, DEC0, q3
from stock.models import ProductContainer
from .models import SalesBill, SalesBillRow


def _get_store_container() -> ProductContainer | None:
    """
    Main POS container: we prefer is_store=True; fallback to code='store'.
    """
    c = ProductContainer.objects.filter(is_store=True).first()
    if c:
        return c
    return ProductContainer.objects.filter(code="store").first()


def _qty_to_primary(product: Product, uom_index: int, qty: Decimal) -> (Decimal, int):
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
        # No store container: silently skip for now
        return

    rows: list[SalesBillRow] = list(bill.rows.all())

    for row in rows:
        # Might be a product that was deleted from catalog; skip
        try:
            product = Product.objects.get(pk=row.product_id)
        except Product.DoesNotExist:
            continue

        qty_primary, unit_index_used = _qty_to_primary(product, row.uom_index, row.qty)
        if qty_primary <= 0:
            continue

        # For cost, we use product.cost if it exists, otherwise 0
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
