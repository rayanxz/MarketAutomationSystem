# app/inventory/models.py
from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from catalog.models import Product

DEC0 = Decimal("0")
DEC3 = Decimal("0.001")
DEC4 = Decimal("0.0001")


def q3(x: Decimal) -> Decimal:
    return (x or DEC0).quantize(DEC3)


def q4(x: Decimal) -> Decimal:
    return (x or DEC0).quantize(DEC4)


class ProductMovement(models.Model):
    """
    Normalized log of all stock movements.

    - qty_primary is in *primary unit* of the product.
      positive  => stock in
      negative  => stock out
    """

    class UnitIndex(models.IntegerChoices):
        PRIMARY = 1, "الوحدة الأولى"
        SECONDARY = 2, "الوحدة الثانية"

    class MovementType(models.TextChoices):
        PURCHASE = "purchase", "فاتورة شراء"
        PURCHASE_REVERSAL = "purchase_reversal", "عكس فاتورة شراء"
        PROVIDER_RETURN = "provider_return", "مرتجع إلى المورد"
        PROVIDER_RETURN_REVERSAL = "provider_return_reversal", "عكس مرتجع إلى المورد"
        SALE = "sale", "فاتورة مبيع"
        SALE_RETURN = "sale_return", "مرتجع من الزبون"
        ADJUSTMENT = "adjustment", "تسوية مخزون"

    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="movements",
    )

    # NEW: where this movement happened
    container = models.ForeignKey(
        "stock.ProductContainer",   # string to avoid circular import at import time
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="movements",
        help_text="Stock container/location affected by this movement.",
    )

    qty_primary = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        help_text="Signed quantity in primary unit (+in, -out).",
    )

    unit_index = models.IntegerField(
        choices=UnitIndex.choices,
        default=UnitIndex.PRIMARY,
        help_text="1 = primary unit, 2 = secondary unit (for info).",
    )

    unit_cost = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Cost per primary unit at the time of movement.",
    )

    total_cost = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Absolute value of qty_primary * unit_cost.",
    )

    movement_type = models.CharField(
        max_length=32,
        choices=MovementType.choices,
    )

    # Where did this movement come from?
    source_app = models.CharField(max_length=32)
    source_model = models.CharField(max_length=64)
    source_id = models.CharField(max_length=36)

    # Original batch identity (for FIFO traceability across transfers)
    origin_source_app = models.CharField(max_length=32, blank=True, default="")
    origin_source_model = models.CharField(max_length=64, blank=True, default="")
    origin_source_id = models.CharField(max_length=36, blank=True, default="")


    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="product_movements",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["product", "created_at"]),
            models.Index(fields=["movement_type"]),
            models.Index(fields=["source_app", "source_model", "source_id"]),
            models.Index(fields=["container", "product"]),  # NEW
            models.Index(fields=["origin_source_app", "origin_source_model", "origin_source_id"]),

        ]

    def __str__(self) -> str:
        return f"{self.product.display_code} {self.movement_type} {self.qty_primary} @ {self.unit_cost}"

    def clean(self):
        # normalize decimals
        self.qty_primary = q3(self.qty_primary)
        self.unit_cost = q4(self.unit_cost)
        self.total_cost = q3(abs(self.qty_primary) * self.unit_cost)

    def save(self, *args, **kwargs):
        self.clean()
        return super().save(*args, **kwargs)


# inventory/models.py
class SaleCostPart(models.Model):
    movement = models.ForeignKey(
        ProductMovement,
        on_delete=models.CASCADE,
        related_name="cost_parts",
    )
    fifo_layer = models.ForeignKey(
        "stock.StockFifoLayer",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    qty_primary = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0"))],
    )
    unit_cost = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        validators=[MinValueValidator(Decimal("0"))],
    )
    total_cost = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0"))],
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["movement"]),
            models.Index(fields=["fifo_layer"]),
        ]

