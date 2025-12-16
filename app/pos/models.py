# app/pos/models.py
from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone


class CustomerProfile(models.Model):
    """
    Simple customer profile for POS only.
    Not the full CRM of the system, just: name + optional phone/notes.
    """
    name = models.CharField(max_length=255)
    phone = models.CharField(max_length=50, blank=True)
    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="pos_customers_created",
    )

    class Meta:
        verbose_name = "Customer profile"
        verbose_name_plural = "Customer profiles"
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name


class PosDay(models.Model):
    """
    A logical POS work day (local calendar date).
    Used to group bills, login sessions and shifts.
    """
    date = models.DateField(unique=True)
    opened_at = models.DateTimeField(default=timezone.now)
    closed_at = models.DateTimeField(null=True, blank=True)
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = "POS work day"
        verbose_name_plural = "POS work days"
        ordering = ("-date",)

    def __str__(self) -> str:
        return f"POS day {self.date}"


class PosLoginSession(models.Model):
    """
    One POS login-usage window for a cashier.

    For now this is 'best-effort':
    - we open it when the user starts using POS
    - later we can explicitly close it on logout / timeout.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="pos_login_sessions",
    )
    day = models.ForeignKey(
        PosDay,
        on_delete=models.CASCADE,
        related_name="login_sessions",
    )
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    closed_reason = models.CharField(max_length=32, blank=True)

    class Meta:
        verbose_name = "POS login session"
        verbose_name_plural = "POS login sessions"
        ordering = ("-started_at",)

    def __str__(self) -> str:
        return f"Login session #{self.pk or 'new'} — {self.user} on {self.day.date}"


class PosShift(models.Model):
    """
    A formal cashier shift (subset of a POS work day,
    optionally inside one login session).
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="pos_shifts",
    )
    day = models.ForeignKey(
        PosDay,
        on_delete=models.CASCADE,
        related_name="shifts",
    )
    login_session = models.ForeignKey(
        PosLoginSession,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="shifts",
    )
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    title = models.CharField(max_length=100, blank=True)

    class Meta:
        verbose_name = "POS shift"
        verbose_name_plural = "POS shifts"
        ordering = ("-started_at",)

    def __str__(self) -> str:
        return f"Shift #{self.pk or 'new'} — {self.user} on {self.day.date}"


class SalesBill(models.Model):
    """
    One POS bill. This is *only* the POS layer; real accounting/billing happens elsewhere.
    """

    PAY_FULL = "full"
    PAY_NONE = "none"
    PAY_PARTIAL = "partial"

    PAY_STATUS_CHOICES = [
        (PAY_FULL, "Paid in full"),
        (PAY_NONE, "Unpaid"),
        (PAY_PARTIAL, "Partially paid"),
    ]

    # NEW: container links
    work_day = models.ForeignKey(
        PosDay,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="bills",
    )
    login_session = models.ForeignKey(
        PosLoginSession,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="bills",
    )
    shift = models.ForeignKey(
        PosShift,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="bills",
    )

    customer = models.ForeignKey(
        CustomerProfile,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="bills",
    )
    # denormalized name in case profile changes or is deleted
    customer_name = models.CharField(max_length=255, blank=True)

    cashier = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="pos_bills",
    )

    pay_status = models.CharField(
        max_length=10,
        choices=PAY_STATUS_CHOICES,
        default=PAY_FULL,
    )

    total_amount = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        default=Decimal("0.000"),
    )
    paid_amount = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        default=Decimal("0.000"),
    )

    # POS-specific flags
    parked = models.BooleanField(
        default=True,
        help_text="If True, this bill is 'parked' (draft) and editable.",
    )
    finalized = models.BooleanField(
        default=False,
        help_text="If True, this bill was saved as final and should not be edited from POS.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Sales bill (POS)"
        verbose_name_plural = "Sales bills (POS)"
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"POS Bill #{self.pk or 'New'} — {self.customer_name or 'No customer'}"

    @property
    def left_amount(self) -> Decimal:
        return (self.total_amount or Decimal("0")) - (self.paid_amount or Decimal("0"))


class SalesBillRow(models.Model):
    """
    Line inside a POS bill.
    We store product_id as integer to avoid hard dependency on catalog.Product migrations.
    """

    bill = models.ForeignKey(
        SalesBill,
        on_delete=models.CASCADE,
        related_name="rows",
    )

    product_id = models.IntegerField()
    product_name = models.CharField(max_length=255)
    product_number = models.CharField(max_length=50, blank=True)

    qty = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        default=Decimal("0.000"),
    )
    uom_index = models.PositiveSmallIntegerField(
        default=1,
        help_text="1 = primary unit, 2 = secondary unit",
    )

    unit_price = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        default=Decimal("0.000"),
        help_text="Price per primary unit",
    )

    disc_amount = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        default=Decimal("0.000"),
    )
    disc_pct = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("0.00"),
    )

    notes = models.TextField(blank=True)

    class Meta:
        verbose_name = "Sales bill row"
        verbose_name_plural = "Sales bill rows"
        ordering = ("id",)

    def __str__(self) -> str:
        return f"{self.product_name} x {self.qty}"
