# app/billing/models.py
from __future__ import annotations
from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models, transaction, IntegrityError
from django.db.models import Q, Max
from django.db.models.functions import Lower
from django.utils import timezone

from catalog.models import Product

DEC0 = Decimal("0.000")


# =========================
# Party type (for sub-ledgers)
# =========================

class PartyType(models.TextChoices):
    PROVIDER = "provider", "مزود"
    CUSTOMER = "customer", "زبون"
    WORKER   = "worker",   "عامل"


# =========================
# Provider
# =========================

class ActiveProviderManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(is_active=True)


class Provider(models.Model):
    name       = models.CharField(max_length=128, unique=False, db_index=True)
    phone      = models.CharField(max_length=64, blank=True)
    notes      = models.TextField(blank=True)
    created_at = models.DateTimeField(null=True, blank=True)
    is_active  = models.BooleanField(default=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    objects = models.Manager()
    active  = ActiveProviderManager()

    class Meta:
        ordering = ["name"]
        constraints = [
            # Case-insensitive uniqueness while active
            models.UniqueConstraint(
                Lower("name"),
                condition=Q(is_active=True),
                name="uq_provider_name_ci_active",
            ),
        ]

    def __str__(self) -> str:
        return self.name


# =========================
# Commercial document: Bill
# =========================

class Bill(models.Model):
    # NOTE: Debt state (paid/remaining/status) lives in DebtorEntry now.
    serial   = models.PositiveIntegerField(unique=True, db_index=True)
    provider = models.ForeignKey(Provider, on_delete=models.PROTECT, related_name="bills")

    total      = models.DecimalField(max_digits=14, decimal_places=3, default=Decimal("0.000"),
                                     validators=[MinValueValidator(0)])
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]
        constraints = [
            models.CheckConstraint(check=Q(total__gte=0), name="bill_total_non_negative"),
        ]

    # --------- Compatibility helpers (Python-level only) ---------
    class Status(models.TextChoices):
        PAID    = "paid",    "مدفوعة بالكامل"
        UNPAID  = "unpaid",  "غير مدفوعة"
        PARTIAL = "partial", "مدفوعة جزئياً"

    @property
    def debtor_entry(self) -> "DebtorEntry | None":
        return DebtorEntry.objects.filter(
            source_app="billing", source_model="Bill", source_id=str(self.id)
        ).first()

    @property
    def paid_amount(self) -> Decimal:
        d = self.debtor_entry
        return (d.paid_amount if d else DEC0) or DEC0

    @property
    def remaining(self) -> Decimal:
        d = self.debtor_entry
        return (d.remaining if d else (self.total or DEC0)) or DEC0

    @property
    def status(self) -> str:
        d = self.debtor_entry
        if not d:
            return Bill.Status.UNPAID
        return (
            Bill.Status.PAID
            if d.remaining <= 0
            else Bill.Status.PARTIAL if d.paid_amount and d.paid_amount > 0
            else Bill.Status.UNPAID
        )

    def __str__(self) -> str:
        s = f"{self.serial or self.pk:03d}"
        prov_name = getattr(self.provider, "name", "") or "—"
        return f"Bill #{s} — {prov_name}"

    # --------- Race-safe serial assignment ---------
    def _assign_serial_locked(self) -> None:
        """Must be called under SELECT ... FOR UPDATE; assigns next serial."""
        last = Bill.objects.select_for_update().aggregate(m=Max("serial")).get("m") or 0
        self.serial = int(last) + 1

    def save(self, *args, **kwargs):
        # If serial already present (updates), just save.
        if self.serial:
            return super().save(*args, **kwargs)

        # Initial insert with retry to avoid serial collisions.
        for _ in range(8):
            try:
                with transaction.atomic():
                    self._assign_serial_locked()
                    return super().save(*args, **kwargs)
            except IntegrityError:
                # Someone else grabbed the same serial; retry.
                self.serial = None
                continue
        # Highly contended system – surface the error for visibility.
        raise


class BillItem(models.Model):
    class UnitIndex(models.IntegerChoices):
        PRIMARY   = 1, "الوحدة الأولى"
        SECONDARY = 2, "الوحدة الثانية"

    bill       = models.ForeignKey(Bill, on_delete=models.CASCADE, related_name="items")
    product    = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="bill_items")
    unit_index = models.IntegerField(choices=UnitIndex.choices, default=UnitIndex.PRIMARY)
    qty_primary= models.DecimalField(max_digits=14, decimal_places=3)
    cost       = models.DecimalField(max_digits=12, decimal_places=4, validators=[MinValueValidator(0)])
    price      = models.DecimalField(max_digits=12, decimal_places=4, validators=[MinValueValidator(0)])
    line_total = models.DecimalField(max_digits=14, decimal_places=3, validators=[MinValueValidator(0)])
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["bill"]),
            models.Index(fields=["product"]),
        ]

    def __str__(self) -> str:
        return f"{self.product.name} x {self.qty_primary} (#{self.bill.serial or self.bill_id})"


# ================================
# Commercial document: Return to Provider
# ================================

class ProviderReturn(models.Model):
    # NOTE: Debt state (collected/remaining/status) lives in CreditorEntry now.
    serial   = models.PositiveIntegerField(unique=True, db_index=True, null=True, blank=True)
    provider = models.ForeignKey(Provider, on_delete=models.PROTECT, related_name="returns")

    total          = models.DecimalField(max_digits=14, decimal_places=3, default=Decimal("0.000"),
                                         validators=[MinValueValidator(0)])
    initial_paid   = models.DecimalField(max_digits=14, decimal_places=3, default=Decimal("0.000"),
                                         validators=[MinValueValidator(0)])
    initial_status = models.CharField(max_length=8, choices=Bill.Status.choices, default=Bill.Status.UNPAID)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]
        constraints = [
            models.CheckConstraint(check=Q(total__gte=0), name="pret_total_non_negative"),
        ]

    # --------- Compatibility helpers (Python-level only) ---------
    class Status(models.TextChoices):
        PAID    = "paid",    "مدفوعة بالكامل"
        UNPAID  = "unpaid",  "غير مدفوعة"
        PARTIAL = "partial", "مدفوعة جزئياً"

    @property
    def creditor_entry(self) -> "CreditorEntry | None":
        return CreditorEntry.objects.filter(
            source_app="billing", source_model="ProviderReturn", source_id=str(self.id)
        ).first()

    @property
    def paid_amount(self) -> Decimal:
        c = self.creditor_entry  # historical name for "collected"
        return (c.collected if c else DEC0) or DEC0

    @property
    def remaining(self) -> Decimal:
        c = self.creditor_entry
        return (c.remaining if c else (self.total or DEC0)) or DEC0

    @property
    def status(self) -> str:
        c = self.creditor_entry
        if not c:
            return ProviderReturn.Status.UNPAID
        return (
            ProviderReturn.Status.PAID
            if c.remaining <= 0
            else ProviderReturn.Status.PARTIAL if c.collected and c.collected > 0
            else ProviderReturn.Status.UNPAID
        )

    def __str__(self) -> str:
        s = f"{self.serial or self.pk:03d}"
        prov_name = getattr(self.provider, "name", "") or "—"
        return f"Return #{s} — {prov_name}"

    # --------- Race-safe serial assignment ---------
    def _assign_serial_locked(self) -> None:
        last = ProviderReturn.objects.select_for_update().aggregate(m=Max("serial")).get("m") or 0
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


class ProviderReturnItem(models.Model):
    class UnitIndex(models.IntegerChoices):
        PRIMARY   = 1, "الوحدة الأولى"
        SECONDARY = 2, "الوحدة الثانية"

    ret        = models.ForeignKey(ProviderReturn, on_delete=models.CASCADE, related_name="items")
    product    = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="return_items")
    unit_index = models.IntegerField(choices=UnitIndex.choices, default=UnitIndex.PRIMARY)
    qty_primary= models.DecimalField(max_digits=14, decimal_places=3)
    cost       = models.DecimalField(max_digits=12, decimal_places=4, validators=[MinValueValidator(0)])
    line_total = models.DecimalField(max_digits=14, decimal_places=3, validators=[MinValueValidator(0)])
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["ret"]),
            models.Index(fields=["product"]),
        ]

    def __str__(self) -> str:
        return f"{self.product.name} x {self.qty_primary} (#{self.ret.serial or self.ret_id})"


# =====================================
# Debtor / Creditor Sub-Ledgers
# =====================================

class DebtorEntry(models.Model):
    """What the store owes a provider (payables) per source doc (Bill) or manual debt."""

    class Status(models.TextChoices):
        OPEN   = "open",   "مفتوحة"
        CLOSED = "closed", "مغلقة"

    provider     = models.ForeignKey(Provider, on_delete=models.PROTECT, related_name="debtor_entries")
    source_app   = models.CharField(max_length=64)   # "billing"
    source_model = models.CharField(max_length=64)   # "Bill" | "ManualDebt"
    source_id    = models.CharField(max_length=64)   # bill id as str | "manual"
    created_at   = models.DateTimeField(default=timezone.now)

    total       = models.DecimalField(max_digits=14, decimal_places=3, default=Decimal("0.000"),
                                      validators=[MinValueValidator(0)])
    paid_amount = models.DecimalField(max_digits=14, decimal_places=3, default=Decimal("0.000"),
                                      validators=[MinValueValidator(0)])
    status      = models.CharField(max_length=8, choices=Status.choices, default=Status.OPEN)

    # NEW: meta for manual debts & list columns
    party_type  = models.CharField(max_length=16, choices=PartyType.choices, default=PartyType.PROVIDER)
    party_name  = models.CharField(max_length=128, blank=True)
    doc_serial  = models.PositiveIntegerField(null=True, blank=True)  # serial when manual (Bill counter)
    due_date    = models.DateField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["provider_id"]),
            models.Index(fields=["status"]),
            models.Index(fields=["created_at"]),
            models.Index(fields=["party_name"]),
        ]
        constraints = [
            models.UniqueConstraint(fields=["source_app", "source_model", "source_id"], name="uniq_debtor_by_source"),
            models.CheckConstraint(check=Q(total__gte=0), name="debtor_total_ge0"),
            models.CheckConstraint(check=Q(paid_amount__gte=0), name="debtor_paid_ge0"),
            models.CheckConstraint(check=Q(paid_amount__lte=models.F("total")), name="debtor_paid_le_total"),
        ]

    @property
    def remaining(self) -> Decimal:
        return (self.total or DEC0) - (self.paid_amount or DEC0)

    def __str__(self) -> str:
        party = self.party_name or (self.provider.name if self.provider_id else "—")
        return f"Debtor #{self.id} → {party} ({self.remaining} remaining)"


class DebtorPayment(models.Model):
    entry      = models.ForeignKey(DebtorEntry, on_delete=models.CASCADE, related_name="payments")
    created_at = models.DateTimeField(default=timezone.now)
    amount     = models.DecimalField(max_digits=14, decimal_places=3, validators=[MinValueValidator(0.001)])
    journal_entry_id = models.IntegerField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["entry_id"]),
            models.Index(fields=["created_at"]),
        ]
        constraints = [
            models.CheckConstraint(check=Q(amount__gt=0), name="debtor_payment_amount_pos"),
        ]

    def __str__(self) -> str:
        return f"DebtorPayment {self.amount} on entry {self.entry_id}"


class CreditorEntry(models.Model):
    """What the provider owes the store (receivables) per source doc (ProviderReturn) or manual debt."""

    class Status(models.TextChoices):
        OPEN   = "open",   "مفتوحة"
        CLOSED = "closed", "مغلقة"

    provider     = models.ForeignKey(Provider, on_delete=models.PROTECT, related_name="creditor_entries")
    source_app   = models.CharField(max_length=64)   # "billing"
    source_model = models.CharField(max_length=64)   # "ProviderReturn" | "ManualDebt"
    source_id    = models.CharField(max_length=64)   # return id as str | "manual"
    created_at   = models.DateTimeField(default=timezone.now)

    total     = models.DecimalField(max_digits=14, decimal_places=3, default=Decimal("0.000"),
                                    validators=[MinValueValidator(0)])
    collected = models.DecimalField(max_digits=14, decimal_places=3, default=Decimal("0.000"),
                                    validators=[MinValueValidator(0)])
    status    = models.CharField(max_length=8, choices=Status.choices, default=Status.OPEN)

    # NEW: meta for manual debts & list columns
    party_type  = models.CharField(max_length=16, choices=PartyType.choices, default=PartyType.PROVIDER)
    party_name  = models.CharField(max_length=128, blank=True)
    doc_serial  = models.PositiveIntegerField(null=True, blank=True)  # serial when manual (Return counter)
    due_date    = models.DateField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["provider_id"]),
            models.Index(fields=["status"]),
            models.Index(fields=["created_at"]),
            models.Index(fields=["party_name"]),
        ]
        constraints = [
            models.UniqueConstraint(fields=["source_app", "source_model", "source_id"], name="uniq_creditor_by_source"),
            models.CheckConstraint(check=Q(total__gte=0), name="creditor_total_ge0"),
            models.CheckConstraint(check=Q(collected__gte=0), name="creditor_coll_ge0"),
            models.CheckConstraint(check=Q(collected__lte=models.F("total")), name="creditor_coll_le_total"),
        ]

    @property
    def remaining(self) -> Decimal:
        return (self.total or DEC0) - (self.collected or DEC0)

    def __str__(self) -> str:
        party = self.party_name or (self.provider.name if self.provider_id else "—")
        return f"Creditor #{self.id} ← {party} ({self.remaining} remaining)"


class CreditorReceipt(models.Model):
    entry      = models.ForeignKey(CreditorEntry, on_delete=models.CASCADE, related_name="receipts")
    created_at = models.DateTimeField(default=timezone.now)
    amount     = models.DecimalField(max_digits=14, decimal_places=3, validators=[MinValueValidator(0.001)])
    journal_entry_id = models.IntegerField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["entry_id"]),
            models.Index(fields=["created_at"]),
        ]
        constraints = [
            models.CheckConstraint(check=Q(amount__gt=0), name="creditor_receipt_amount_pos"),
        ]

    def __str__(self) -> str:
        return f"CreditorReceipt {self.amount} on entry {self.entry_id}"
