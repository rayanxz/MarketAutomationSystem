# app/pos/models.py
from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models, transaction, IntegrityError
from django.utils import timezone
from django.db.models import Max
from django.core.validators import MinValueValidator

from core.currency import CURRENCY_CHOICES, SYP, USD
from core.public_ids import allocate_next_public_id
from financials.models import MoneyContainer
from catalog.models import Product
from stock.models import ProductContainer

SALES_BILL_PUBLIC_ID_PREFIX = "PS-"
SALES_BILL_PUBLIC_ID_SEQUENCE_KEY = "pos_sales_bill_public_id"


def _sales_bill_public_id_default() -> str:
    return allocate_next_public_id(
        sequence_key=SALES_BILL_PUBLIC_ID_SEQUENCE_KEY,
        prefix=SALES_BILL_PUBLIC_ID_PREFIX,
        model=SalesBill,
    )


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

    SETTLE_SPLIT = "split"
    SETTLE_ALL_SYP = "all_syp"
    SETTLE_ALL_USD = "all_usd"

    SETTLEMENT_MODE_CHOICES = [
        (SETTLE_SPLIT, "Split (SYP+USD)"),
        (SETTLE_ALL_SYP, "All in SYP"),
        (SETTLE_ALL_USD, "All in USD"),
    ]

    public_id = models.CharField(max_length=24, unique=True, default=_sales_bill_public_id_default, editable=False, db_index=True)

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

    money_container = models.ForeignKey(
        MoneyContainer,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="pos_sales_bills",
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
    total_syp = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        default=Decimal("0.000"),
    )
    total_usd = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        default=Decimal("0.000"),
    )
    paid_amount = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        default=Decimal("0.000"),
    )
    settlement_mode = models.CharField(
        max_length=12,
        choices=SETTLEMENT_MODE_CHOICES,
        default=SETTLE_SPLIT,
    )
    settlement_currency = models.CharField(
        max_length=3,
        choices=CURRENCY_CHOICES,
        null=True,
        blank=True,
    )
    fx_rate_used = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        null=True,
        blank=True,
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

    is_deleted = models.BooleanField(
        default=False,
        help_text="Soft-delete flag (only for parked bills).",
    )
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="pos_bills_deleted",
    )


    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Sales bill (POS)"
        verbose_name_plural = "Sales bills (POS)"
        ordering = ("-created_at",)

    def __str__(self) -> str:
        ref = (self.public_id or "").strip() or f"#{self.pk or 'New'}"
        return f"POS Bill {ref} — {self.customer_name or 'No customer'}"

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
    product_name_at_txn = models.CharField(max_length=255, default="", blank=True)
    product_number = models.CharField(max_length=50, blank=True)
    conv_factor_at_txn = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal("1.0000"),
    )
    unit_1_label_at_txn = models.CharField(max_length=32, default="")
    unit_2_label_at_txn = models.CharField(max_length=32, default="", blank=True)
    qty_primary_at_txn = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)

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
    sale_currency = models.CharField(
        max_length=3,
        choices=CURRENCY_CHOICES,
        default=SYP,
    )
    unit_cost_at_txn = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    cost_currency_at_txn = models.CharField(max_length=3, null=True, blank=True)
    fx_rate_at_txn = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)

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


# ================================
# POS Sales Returns (Customer Return)
# ================================

class SalesReturn(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        POSTED = "posted", "Posted"
        CANCELLED = "cancelled", "Cancelled"

    serial = models.PositiveIntegerField(unique=True, db_index=True, null=True, blank=True)

    sale_bill = models.ForeignKey(
        SalesBill,
        on_delete=models.PROTECT,
        related_name="returns",
    )
    customer = models.ForeignKey(
        CustomerProfile,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="returns",
    )
    stock_container = models.ForeignKey(
        ProductContainer,
        on_delete=models.PROTECT,
        related_name="pos_sales_returns",
    )

    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )

    total_syp = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        default=Decimal("0.000"),
        validators=[MinValueValidator(0)],
    )
    total_usd = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        default=Decimal("0.000"),
        validators=[MinValueValidator(0)],
    )

    notes = models.TextField(blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="pos_sales_returns_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    posted_at = models.DateTimeField(null=True, blank=True)
    posted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="pos_sales_returns_posted",
    )

    class Meta:
        ordering = ["-id"]
        indexes = [
            models.Index(fields=["sale_bill"]),
            models.Index(fields=["status"]),
        ]
        constraints = [
            models.CheckConstraint(check=models.Q(total_syp__gte=0), name="pos_ret_total_syp_non_negative"),
            models.CheckConstraint(check=models.Q(total_usd__gte=0), name="pos_ret_total_usd_non_negative"),
        ]

    def __str__(self) -> str:
        s = f"{self.serial or self.pk:03d}"
        return f"SalesReturn #{s}"

    def _assign_serial_locked(self) -> None:
        last = SalesReturn.objects.select_for_update().aggregate(m=Max("serial")).get("m") or 0
        self.serial = int(last) + 1

    def save(self, *args, **kwargs):
        if self.serial:
            return super().save(*args, **kwargs)
        for _ in range(8):
            try:
                with transaction.atomic():
                    self._assign_serial_locked()
                    return super().save(*args, **kwargs)
            except IntegrityError:
                self.serial = None
                continue
        raise


class SalesReturnRow(models.Model):
    ret = models.ForeignKey(
        SalesReturn,
        on_delete=models.CASCADE,
        related_name="rows",
    )
    sale_row = models.ForeignKey(
        SalesBillRow,
        on_delete=models.PROTECT,
        related_name="return_rows",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="pos_return_rows",
    )
    product_name_at_txn = models.CharField(max_length=255, default="", blank=True)

    uom_index = models.PositiveSmallIntegerField(default=1)
    conv_factor_at_txn = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal("1.0000"),
    )
    unit_1_label_at_txn = models.CharField(max_length=32, default="")
    unit_2_label_at_txn = models.CharField(max_length=32, default="", blank=True)
    qty_used_at_txn = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    qty_returned = models.DecimalField(max_digits=14, decimal_places=3, validators=[MinValueValidator(0)])

    currency_code = models.CharField(
        max_length=3,
        choices=CURRENCY_CHOICES,
        default=SYP,
        db_index=True,
    )
    unit_price_at_sale = models.DecimalField(max_digits=14, decimal_places=3, default=Decimal("0.000"))
    unit_cost_at_txn = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    cost_currency_at_txn = models.CharField(max_length=3, null=True, blank=True)
    fx_rate_at_txn = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    discount_amount_at_txn = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    discount_pct_at_txn = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    line_total = models.DecimalField(max_digits=14, decimal_places=3, default=Decimal("0.000"))

    reason = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["ret"]),
            models.Index(fields=["sale_row"]),
            models.Index(fields=["product"]),
        ]

    def __str__(self) -> str:
        name = (self.product_name_at_txn or "").strip() or getattr(self.product, "name", "")
        return f"{name} x {self.qty_returned} (#{self.ret.serial or self.ret_id})"
