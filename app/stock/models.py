# app/stock/models.py
from __future__ import annotations
from decimal import Decimal

from django.db import models
from django.utils import timezone

from catalog.models import Product
from django.db.models import Q

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
        constraints = [
            # At most one container can be marked as main store
            models.UniqueConstraint(
                fields=["is_store"],
                condition=Q(is_store=True),
                name="unique_main_store_container",
            ),
        ]

    @property
    def display_label(self) -> str:
        """
        Arabic label for UI, without touching DB names.
        """
        if self.code == "store":
            return "متجر"
        elif self.code == "wh1":
            return "مستودع 1"
        elif self.code == "wh2":
            return "مستودع 2"
        return self.name

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


class StockFifoLayer(models.Model):
    """
    FIFO cost layer per (product, container).

    Each incoming movement (purchase / positive adjustment / sale-return)
    creates one or more layers.

    Outgoing movements (sales, provider returns, negative adjustments)
    consume from the oldest non-empty layers in this table.
    """
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="fifo_layers",
    )
    container = models.ForeignKey(
        ProductContainer,
        on_delete=models.CASCADE,
        related_name="fifo_layers",
    )

    # remaining quantity in primary unit for this layer
    qty_remaining = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        default=Decimal("0.000"),
        help_text="Remaining qty in primary unit for this FIFO layer.",
    )

    # cost per primary unit for this layer
    unit_cost = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal("0.0000"),
        help_text="Cost per primary unit for this FIFO layer.",
    )

    created_at = models.DateTimeField(
        default=timezone.now,
        help_text="Creation time (used for FIFO ordering).",
    )

    # purely for traceability (not required for logic)
    source_app = models.CharField(max_length=32, blank=True, default="")
    source_model = models.CharField(max_length=64, blank=True, default="")
    source_id = models.CharField(max_length=36, blank=True, default="")

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [
            models.Index(fields=["product", "container", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"FIFO {self.product.display_code} @ {self.container.code}: {self.qty_remaining} @ {self.unit_cost}"
