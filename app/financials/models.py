# financials/models.py
from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

import secrets

DEC0 = Decimal("0")

def gen_ref_code() -> str:
    return "MC-" + secrets.token_hex(4).upper()  # e.g. MC-8F3A12BC


class Currency(models.Model):
    code = models.CharField(max_length=10, unique=True)  # "SYP", "USD"
    name = models.CharField(max_length=64, blank=True)
    decimals = models.PositiveSmallIntegerField(default=2)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]

    def __str__(self) -> str:
        return self.code


class MoneyContainer(models.Model):
    class ContainerType(models.TextChoices):
        DRAWER = "drawer", "درج"
        SAFE = "safe", "خزنة"
        BANK = "bank", "بنك"

    ref_code = models.CharField(max_length=40, unique=True, default=gen_ref_code, db_index=True)
    name = models.CharField(max_length=120, unique=True)

    container_type = models.CharField(
        max_length=20,
        choices=ContainerType.choices,
        default=ContainerType.DRAWER,
        db_index=True,
    )

    is_active = models.BooleanField(default=True)
    note = models.TextField(blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="financials_created_containers",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    features = models.ManyToManyField(
        "ContainerFeature",
        blank=True,
        related_name="money_containers",
    )

    allowed_users = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="financials_allowed_containers",
    )

    # Per-currency balances (legacy single balance does not exist here)
    balance_syp = models.DecimalField(max_digits=18, decimal_places=2, default=DEC0)
    balance_usd = models.DecimalField(max_digits=18, decimal_places=2, default=DEC0)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class MoneyContainerCurrency(models.Model):
    container = models.ForeignKey(MoneyContainer, on_delete=models.CASCADE, related_name="currency_states")
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT, related_name="container_states")
    is_enabled = models.BooleanField(default=True)

    class Meta:
        unique_together = [("container", "currency")]
        indexes = [
            models.Index(fields=["container", "currency"]),
            models.Index(fields=["currency", "is_enabled"]),
        ]

    def __str__(self) -> str:
        return f"{self.container_id}:{self.currency.code} enabled={self.is_enabled}"


class ContainerFeature(models.Model):
    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=120)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self) -> str:
        return self.name


# ======================
# GLOBAL FX (USD -> SYP)
# ======================
class FxSettings(models.Model):
    """
    Singleton-ish row holding the CURRENT FX used by the system until changed.
    Rate meaning: how many SYP for 1 USD.
    Example: 1 USD = 20000 SYP => rate_syp_per_usd = 20000
    """
    rate_syp_per_usd = models.DecimalField(max_digits=18, decimal_places=6)
    is_active = models.BooleanField(default=True)

    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="financials_fx_updates",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["is_active", "updated_at"])]
        ordering = ["-updated_at"]

    def clean(self) -> None:
        if self.rate_syp_per_usd is None or self.rate_syp_per_usd <= 0:
            raise ValidationError("FX rate must be > 0")

    def __str__(self) -> str:
        return f"FX 1USD={self.rate_syp_per_usd} SYP"


class CounterpartyType(models.TextChoices):
    PROVIDER = "provider", "Provider"
    CUSTOMER = "customer", "Customer"
    STAFF = "staff", "Staff"
    SYSTEM = "system", "System"


class Counterparty(models.Model):
    type = models.CharField(max_length=20, choices=CounterpartyType.choices)
    name = models.CharField(max_length=160)

    # Stable links to domain entities (preferred over note markers).
    provider = models.ForeignKey(
        "billing.Provider",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="financials_counterparties",
    )
    customer = models.ForeignKey(
        "pos.CustomerProfile",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="financials_counterparties",
    )

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
            models.Index(fields=["provider"]),
            models.Index(fields=["customer"]),
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
    OPENING_BALANCE = "opening_balance", "Opening Balance"
    CASH_ADD = "cash_add", "Cash Add"
    CASH_WITHDRAW = "cash_withdraw", "Cash Withdraw"
    CONTAINER_TRANSFER = "container_transfer", "Container Transfer"
    EXCHANGE = "exchange", "Exchange"
    COUNTERPARTY_INC = "counterparty_inc", "Counterparty Increase/Decrease"
    COUNTERPARTY_SETTLE = "counterparty_settle", "Counterparty Settlement"
    REVERSAL = "reversal", "Reversal"


class Receipt(models.Model):
    serial = models.CharField(max_length=40, unique=True, blank=True)
    kind = models.CharField(max_length=40, choices=ReceiptKind.choices)
    status = models.CharField(max_length=20, choices=ReceiptStatus.choices, default=ReceiptStatus.DRAFT)

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="financials_receipts",
    )

    note = models.TextField(blank=True)

    source_app = models.CharField(max_length=50, blank=True)
    source_model = models.CharField(max_length=80, blank=True)
    source_id = models.CharField(max_length=80, blank=True)

    group_key = models.UUIDField(default=uuid4, editable=False, db_index=True)

    # ✅ FX snapshot stored on each receipt
    # null allowed only to not explode old rows; services will enforce it for new postings.
    fx_syp_per_usd = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    posted_at = models.DateTimeField(null=True, blank=True)

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

        # ✅ enforce FX when posted (keeps old drafts survivable)
        if self.status in (ReceiptStatus.POSTED, ReceiptStatus.REVERSED):
            if self.fx_syp_per_usd is None or self.fx_syp_per_usd <= 0:
                raise ValidationError("Posted receipts must contain valid FX (> 0).")

    def ensure_serial(self) -> None:
        if self.serial:
            return
        d = self.created_at.date()
        self.serial = f"FIN-{d:%Y%m%d}-{self.id:06d}"


class PostingTargetType(models.TextChoices):
    CONTAINER = "container", "Container"
    COUNTERPARTY = "counterparty", "Counterparty"


class PostingLine(models.Model):
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
    amount = models.DecimalField(max_digits=18, decimal_places=6)
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
                    (Q(target_type=PostingTargetType.CONTAINER) & Q(container__isnull=False) & Q(counterparty__isnull=True))
                    | (Q(target_type=PostingTargetType.COUNTERPARTY) & Q(counterparty__isnull=False) & Q(container__isnull=True))
                ),
            ),
        ]

    def __str__(self) -> str:
        tgt = self.container_id if self.container_id else self.counterparty_id
        return f"{self.target_type}:{tgt} {self.currency.code} {self.amount}"
