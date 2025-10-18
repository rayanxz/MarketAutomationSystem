# app/billing/models.py
from __future__ import annotations

from decimal import Decimal
from django.core.validators import MinValueValidator
from django.db import models, transaction, IntegrityError
from django.db.models.functions import Lower
from django.db.models import Q


from catalog.models import Product


class Provider(models.Model):
    name = models.CharField(max_length=128, unique=False, db_index=True)  # unique handled by conditional constraint
    phone = models.CharField(max_length=64, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(null=True, blank=True)
    # NEW: soft delete / archive
    is_active = models.BooleanField(default=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            # Allow reusing a name when the older profile is archived.
            models.UniqueConstraint(
                Lower("name"),
                condition=Q(is_active=True),
                name="uq_provider_name_ci_active",
            ),
        ]

    def __str__(self) -> str:
        return self.name


class Bill(models.Model):
    class Status(models.TextChoices):
        PAID = "paid", "مدفوعة بالكامل"
        UNPAID = "unpaid", "غير مدفوعة"
        PARTIAL = "partial", "مدفوعة جزئياً"

    serial = models.PositiveIntegerField(unique=True, db_index=True, null=True, blank=True)
    provider = models.ForeignKey(Provider, on_delete=models.PROTECT, related_name="bills")

    total = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        default=Decimal("0.000"),
        validators=[MinValueValidator(0)],
    )

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.UNPAID)

    paid_amount = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        default=Decimal("0.000"),
        validators=[MinValueValidator(0)],
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]

    def __str__(self) -> str:
        s = f"{self.serial or self.pk:03d}"
        return f"Bill #{s} — {self.provider.name}"

    def assign_serial_if_needed(self) -> None:
        if self.serial:
            return
        for _ in range(5):
            try:
                with transaction.atomic():
                    last = (
                        Bill.objects.select_for_update()
                        .order_by("-serial")
                        .values_list("serial", flat=True)
                        .first()
                    )
                    self.serial = 1 if last in (None, 0) else int(last) + 1
                    return
            except IntegrityError:
                continue

    def save(self, *args, **kwargs):
        if not self.serial:
            self.assign_serial_if_needed()
        super().save(*args, **kwargs)


class BillItem(models.Model):
    class UnitIndex(models.IntegerChoices):
        PRIMARY = 1, "الوحدة الأولى"
        SECONDARY = 2, "الوحدة الثانية"

    bill = models.ForeignKey(Bill, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="bill_items")

    unit_index = models.IntegerField(choices=UnitIndex.choices, default=UnitIndex.PRIMARY)

    # Always stored in PRIMARY units
    qty_primary = models.DecimalField(max_digits=14, decimal_places=3)

    # Snapshot at time of bill (per PRIMARY unit)
    cost = models.DecimalField(max_digits=12, decimal_places=4, validators=[MinValueValidator(0)])
    price = models.DecimalField(max_digits=12, decimal_places=4, validators=[MinValueValidator(0)])

    # Final: non-null with validator
    line_total = models.DecimalField(
        max_digits=14, decimal_places=3, validators=[MinValueValidator(0)]
    )

    # Final: auto timestamp for new rows
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["bill"]),
            models.Index(fields=["product"]),
        ]

    def __str__(self) -> str:
        return f"{self.product.name} x {self.qty_primary} (#{self.bill.serial or self.bill_id})"
