from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


DEC0 = Decimal("0")


class Currency(models.Model):
    """
    Supported currencies (start with SYP, USD).
    decimals:
      - SYP usually 0
      - USD usually 2
    """
    code = models.CharField(max_length=10, unique=True)  # "SYP", "USD"
    name = models.CharField(max_length=64, blank=True)
    decimals = models.PositiveSmallIntegerField(default=2)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]

    def __str__(self) -> str:
        return self.code


class MoneyContainer(models.Model):
    """
    Store-owned money location (cash drawer, safe, bank).
    Balances are NOT stored here as truth; they are derived from PostingLine.
    """
    name = models.CharField(max_length=120, unique=True)
    is_active = models.BooleanField(default=True)
    note = models.TextField(blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="financials_created_containers",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class CounterpartyType(models.TextChoices):
    PROVIDER = "provider", "Provider"
    CUSTOMER = "customer", "Customer"
    STAFF = "staff", "Staff"
    SYSTEM = "system", "System"


class Counterparty(models.Model):
    """
    Entity you can owe / be owed by.
    Optionally links to your real Provider/Customer/User models (nullable) later.
    For now, name + type is enough.
    """
    type = models.CharField(max_length=20, choices=CounterpartyType.choices)
    name = models.CharField(max_length=160)

    # Optional links (safe to keep nullable; wire them later)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="financials_counterparty_user",
    )

    is_active = models.BooleanField(default=True)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["type", "name"]),
        ]
        ordering = ["type", "name"]
        unique_together = [("type", "name")]

    def __str__(self) -> str:
        return f"{self.type}:{self.name}"


class ReceiptStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    POSTED = "posted", "Posted"
    REVERSED = "reversed", "Reversed"
    VOID = "void", "Void"


class ReceiptKind(models.TextChoices):
    CASH_ADD = "cash_add", "Cash Add"
    CASH_WITHDRAW = "cash_withdraw", "Cash Withdraw"
    CONTAINER_TRANSFER = "container_transfer", "Container Transfer"
    COUNTERPARTY_INC = "counterparty_inc", "Counterparty Increase/Decrease"
    COUNTERPARTY_SETTLE = "counterparty_settle", "Counterparty Settlement"
    REVERSAL = "reversal", "Reversal"


class Receipt(models.Model):
    """
    Human-friendly document (serial/type/actor/time/source).
    Truth is in PostingLine.
    """
    serial = models.CharField(max_length=40, unique=True, blank=True)
    kind = models.CharField(max_length=40, choices=ReceiptKind.choices)
    status = models.CharField(max_length=20, choices=ReceiptStatus.choices, default=ReceiptStatus.DRAFT)

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="financials_receipts",
    )

    note = models.TextField(blank=True)

    # Link to source document in other app (billing, pos, etc.) later
    source_app = models.CharField(max_length=50, blank=True)
    source_model = models.CharField(max_length=80, blank=True)
    source_id = models.CharField(max_length=80, blank=True)

    group_key = models.UUIDField(default=uuid4, editable=False, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    posted_at = models.DateTimeField(null=True, blank=True)

    # reversal link
    reverses = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="reversed_by",
    )

    class Meta:
        indexes = [
            models.Index(fields=["kind", "status", "created_at"]),
            models.Index(fields=["source_app", "source_model", "source_id"]),
        ]
        ordering = ["-id"]

    def __str__(self) -> str:
        return self.serial or f"Receipt#{self.pk}"

    def clean(self) -> None:
        if self.kind == ReceiptKind.REVERSAL and not self.reverses_id:
            raise ValidationError("Reversal receipt must point to `reverses`.")
        if self.reverses_id and self.kind != ReceiptKind.REVERSAL:
            raise ValidationError("Only REVERSAL kind can set `reverses`.")

    def ensure_serial(self) -> None:
        """
        Serial after ID exists. Format: FIN-YYYYMMDD-000001
        """
        if self.serial:
            return
        d = self.created_at.date()
        self.serial = f"FIN-{d:%Y%m%d}-{self.id:06d}"


class PostingTargetType(models.TextChoices):
    CONTAINER = "container", "Container"
    COUNTERPARTY = "counterparty", "Counterparty"


class PostingLine(models.Model):
    """
    The truth: signed amount posted to either a container or counterparty in a currency.
      + amount => increases target balance
      - amount => decreases target balance

    Convention (recommended):
      - Container: + means cash increased, - means cash decreased
      - Counterparty: + means counterparty owes store, - means store owes counterparty
    """
    receipt = models.ForeignKey(Receipt, on_delete=models.PROTECT, related_name="lines")

    target_type = models.CharField(max_length=20, choices=PostingTargetType.choices)

    container = models.ForeignKey(
        MoneyContainer,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="posting_lines",
    )
    counterparty = models.ForeignKey(
        Counterparty,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="posting_lines",
    )

    currency = models.ForeignKey(Currency, on_delete=models.PROTECT, related_name="posting_lines")
    amount = models.DecimalField(max_digits=18, decimal_places=6)  # we quantize in services, keep storage flexible

    meta_json = models.TextField(blank=True, default="")

    class Meta:
        indexes = [
            models.Index(fields=["target_type", "currency"]),
            models.Index(fields=["container", "currency"]),
            models.Index(fields=["counterparty", "currency"]),
            models.Index(fields=["receipt"]),
        ]
        constraints = [
            models.CheckConstraint(
                name="financials_postingline_target_matches",
                check=(
                    # container target
                    (Q(target_type=PostingTargetType.CONTAINER) & Q(container__isnull=False) & Q(counterparty__isnull=True))
                    |
                    # counterparty target
                    (Q(target_type=PostingTargetType.COUNTERPARTY) & Q(counterparty__isnull=False) & Q(container__isnull=True))
                ),
            ),
        ]

    def __str__(self) -> str:
        tgt = self.container_id if self.container_id else self.counterparty_id
        return f"{self.target_type}:{tgt} {self.currency.code} {self.amount}"
