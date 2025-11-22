# app/stock/models.py
from __future__ import annotations
from decimal import Decimal

from django.db import models
from django.utils import timezone

from catalog.models import Product

DEC0 = Decimal("0")
DEC3 = Decimal("0.001")


def q3(x: Decimal) -> Decimal:
    return (x or DEC0).quantize(DEC3)


class ProductContainer(models.Model):
    """
    Physical/Logical stock location: store, warehouse1, warehouse2, etc.
    """
    name = models.CharField(max_length=64, unique=True)
    code = models.CharField(
        max_length=32,
        unique=True,
        db_index=True,
        help_text="Short machine-readable code, e.g. 'store', 'wh1'.",
    )

    is_store = models.BooleanField(
        default=False,
        help_text="Exactly one main store for POS / website.",
    )
    is_active = models.BooleanField(default=True)

    sort_order = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self) -> str:
        return f"{self.code} — {self.name}"


class StockEntry(models.Model):
    """
    Snapshot / cache: current stock per (product, container).
    This is updated *only* from inventory movements.
    """
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="stock_entries",
    )
    container = models.ForeignKey(
        ProductContainer,
        on_delete=models.CASCADE,
        related_name="stock_entries",
    )

    # Current quantity in primary unit (can be negative if allowed)
    qty_primary = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        default=Decimal("0.000"),
        help_text="Current quantity in primary unit for this product in this container.",
    )

    # (Optional) average cost in this container – we can use later for valuation
    avg_unit_cost = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal("0.0000"),
        help_text="Average cost per primary unit in this container (optional for now).",
    )

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("product", "container")
        indexes = [
            models.Index(fields=["product", "container"]),
            models.Index(fields=["container"]),
        ]

    def __str__(self) -> str:
        return f"{self.product.display_code} @ {self.container.code}: {self.qty_primary}"
