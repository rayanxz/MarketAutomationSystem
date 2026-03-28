# app/billing/models.py
from __future__ import annotations
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction, IntegrityError
from django.db.models import Q, Max
from django.db.models.functions import Lower

from catalog.models import Product
from financials.models import MoneyContainer
from core.currency import CURRENCY_CHOICES, SYP as CURRENCY_SYP, USD as CURRENCY_USD

from debts.models import DebtorDebt as DebtorEntry, CreditorDebt as CreditorEntry
from django.conf import settings

DEC0 = Decimal("0.000")


def _debt_entry_identity_priority(entry, *, base_source_id: str) -> tuple[int, int]:
    sid = str(getattr(entry, "source_id", "") or "").strip()
    legacy = str(getattr(entry, "legacy_source_id", "") or "").strip()
    legacy_usd = f"{base_source_id}:USD"
    if sid == base_source_id:
        bucket = 0
    elif legacy == base_source_id:
        bucket = 1
    elif sid == legacy_usd:
        bucket = 2
    elif legacy == legacy_usd:
        bucket = 3
    else:
        bucket = 4
    return (bucket, int(getattr(entry, "id", 0) or 0))




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
    created_at = models.DateTimeField(auto_now_add=True)
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
    class CreationPaymentStatus(models.TextChoices):
        PAID = "paid", "مدفوعة بالكامل"
        UNPAID = "unpaid", "غير مدفوعة"
        PARTIAL = "partial", "مدفوعة جزئياً"

    class CreationPaymentMethod(models.TextChoices):
        NONE = "none", "بدون دفع"
        SYP_ONLY = "syp_only", "SYP فقط"
        USD_ONLY = "usd_only", "USD فقط"
        SEPARATE = "separate", "منفصل"
        MIXED = "mixed", "مختلط"

    # NOTE: Debt state (paid/remaining/status) lives in DebtorEntry now.
    serial   = models.PositiveIntegerField(unique=True, db_index=True)
    provider = models.ForeignKey(Provider, on_delete=models.PROTECT, related_name="bills")

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.PROTECT,
        related_name="created_bills",
    )

    money_container = models.ForeignKey(
        MoneyContainer,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="purchase_bills",
    )

    creation_payment_status = models.CharField(
        max_length=10,
        choices=CreationPaymentStatus.choices,
        null=True,
        blank=True,
        db_index=True,
    )
    creation_payment_method = models.CharField(
        max_length=12,
        choices=CreationPaymentMethod.choices,
        null=True,
        blank=True,
    )
    creation_paid_syp = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        default=Decimal("0.000"),
        validators=[MinValueValidator(0)],
    )
    creation_paid_usd = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        default=Decimal("0.000"),
        validators=[MinValueValidator(0)],
    )

    total      = models.DecimalField(max_digits=14, decimal_places=3, default=Decimal("0.000"),
                                     validators=[MinValueValidator(0)])
    created_at = models.DateTimeField(auto_now_add=True)

    from decimal import Decimal

    settlement_currency = models.CharField(
        max_length=3,
        choices=CURRENCY_CHOICES,
        default=CURRENCY_SYP,
        db_index=True,
    )

    # snapshot: SYP per 1 USD
    fx_usd_syp = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        null=True,
        blank=True,
    )

    subtotal_syp = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        default=Decimal("0.000"),
    )

    subtotal_usd = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        default=Decimal("0.000"),
    )

    # per-currency totals (authoritative split)
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

    # fx snapshot at creation time (SYP per 1 USD)
    fx_rate_usd_to_syp_used = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        null=True,
        blank=True,
    )

    # totals after FX normalization (keep for current services)
    grand_total_syp = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        default=Decimal("0.000"),
    )
    grand_total_usd = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        default=Decimal("0.000"),
    )

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
    def debtor_entries(self):
        cached = getattr(self, "_debtor_entries_cached", None)
        if cached is not None:
            return cached
        return list(
            DebtorEntry.objects.filter(
                source_app="billing",
                source_model="Bill",
            ).filter(
                Q(source_id=str(self.id))
                | Q(legacy_source_id=str(self.id))
                | Q(source_id=f"{self.id}:USD")
                | Q(legacy_source_id=f"{self.id}:USD")
            )
        )

    def _debtor_entry_by_currency(self, code: str):
        code = (code or "").upper()
        base_source_id = str(self.id)
        rows = []
        for e in self.debtor_entries:
            cur = (getattr(e, "currency_code", None) or "SYP").upper()
            if cur == code:
                rows.append(e)
        if rows:
            rows.sort(key=lambda e: _debt_entry_identity_priority(e, base_source_id=base_source_id))
            return rows[0]
        # legacy suffix fallback
        if code == CURRENCY_USD:
            legacy_rows = []
            for e in self.debtor_entries:
                if (e.source_id or "").endswith(":USD") or (getattr(e, "legacy_source_id", "") or "").endswith(":USD"):
                    legacy_rows.append(e)
            if legacy_rows:
                legacy_rows.sort(key=lambda e: _debt_entry_identity_priority(e, base_source_id=base_source_id))
                return legacy_rows[0]
        return None

    @property
    def debtor_entry(self):
        # legacy: return SYP entry for backward compatibility
        return self._debtor_entry_by_currency(CURRENCY_SYP) or (self.debtor_entries[0] if self.debtor_entries else None)

    @property
    def debtor_entry_usd(self):
        return self._debtor_entry_by_currency(CURRENCY_USD)

    @property
    def paid_syp(self) -> Decimal:
        d = self._debtor_entry_by_currency(CURRENCY_SYP)
        return (d.paid_amount if d else DEC0) or DEC0

    @property
    def paid_usd(self) -> Decimal:
        d = self._debtor_entry_by_currency(CURRENCY_USD)
        return (d.paid_amount if d else DEC0) or DEC0

    @property
    def remaining_syp(self) -> Decimal:
        d = self._debtor_entry_by_currency(CURRENCY_SYP)
        return (d.remaining if d else (self.total_syp or DEC0)) or DEC0

    @property
    def remaining_usd(self) -> Decimal:
        d = self._debtor_entry_by_currency(CURRENCY_USD)
        return (d.remaining if d else (self.total_usd or DEC0)) or DEC0


    @property
    def paid_amount(self) -> Decimal:
        if (self.settlement_currency or CURRENCY_SYP) == CURRENCY_USD:
            return self.paid_usd
        return self.paid_syp

    @property
    def remaining(self) -> Decimal:
        if (self.settlement_currency or CURRENCY_SYP) == CURRENCY_USD:
            return self.remaining_usd
        return self.remaining_syp

    @property
    def status(self) -> str:
        syp = self._debtor_entry_by_currency(CURRENCY_SYP)
        usd = self._debtor_entry_by_currency(CURRENCY_USD)

        if not syp and not usd:
            return Bill.Status.UNPAID

        rem_s = syp.remaining if syp else (self.total_syp or DEC0)
        rem_u = usd.remaining if usd else (self.total_usd or DEC0)
        if rem_s <= 0 and rem_u <= 0:
            return Bill.Status.PAID

        paid_s = syp.paid_amount if syp else DEC0
        paid_u = usd.paid_amount if usd else DEC0
        if (paid_s and paid_s > 0) or (paid_u and paid_u > 0):
            return Bill.Status.PARTIAL
        return Bill.Status.UNPAID

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
    currency = models.CharField(
        max_length=3,
        choices=CURRENCY_CHOICES,
        default=CURRENCY_SYP,
        db_index=True,
    )

    bill       = models.ForeignKey(Bill, on_delete=models.CASCADE, related_name="items")
    product    = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="bill_items")
    product_name_at_txn = models.CharField(max_length=255, default="", blank=True)
    unit_index = models.IntegerField(choices=UnitIndex.choices, default=UnitIndex.PRIMARY)
    conv_factor_at_txn = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal("1.0000"),
    )
    unit_1_label_at_txn = models.CharField(max_length=32, default="")
    unit_2_label_at_txn = models.CharField(max_length=32, default="", blank=True)
    qty_used_at_txn = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    qty_primary= models.DecimalField(max_digits=14, decimal_places=3)
    cost       = models.DecimalField(max_digits=12, decimal_places=4, validators=[MinValueValidator(0)])
    price      = models.DecimalField(max_digits=12, decimal_places=4, validators=[MinValueValidator(0)])
    line_total = models.DecimalField(max_digits=14, decimal_places=3, validators=[MinValueValidator(0)])
    fx_rate_at_txn = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["bill"]),
            models.Index(fields=["product"]),
        ]

    def __str__(self) -> str:
        name = (self.product_name_at_txn or "").strip() or getattr(self.product, "name", "")
        return f"{name} x {self.qty_primary} (#{self.bill.serial or self.bill_id})"

    def clean(self):
        super().clean()
        if self.product:
            allow_syp = getattr(self.product, "allow_syp_purchasing", self.product.enable_syp)
            allow_usd = getattr(self.product, "allow_usd_purchasing", self.product.enable_usd)
            if self.currency == CURRENCY_SYP and not allow_syp:
                raise ValidationError("SYP is not enabled for this product.")
            if self.currency == CURRENCY_USD and not allow_usd:
                raise ValidationError("USD is not enabled for this product.")


# ================================
# Commercial document: Return to Provider
# ================================

class ProviderReturn(models.Model):
    # NOTE: Debt state (collected/remaining/status) lives in CreditorEntry now.
    serial   = models.PositiveIntegerField(unique=True, db_index=True, null=True, blank=True)
    provider = models.ForeignKey(Provider, on_delete=models.PROTECT, related_name="returns")

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.PROTECT,
        related_name="created_provider_returns",
    )

     # 👇 NEW: link to the purchase bill via its serial number
    source_bill_serial = models.PositiveIntegerField(
        null=True,
        blank=True,
        db_index=True,
        help_text="سيريال فاتورة الشراء الأصلية إن وجد.",
    )

    total          = models.DecimalField(max_digits=14, decimal_places=3, default=Decimal("0.000"),
                                         validators=[MinValueValidator(0)])
    # per-currency totals (authoritative split)
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
    settlement_currency = models.CharField(
        max_length=3,
        choices=CURRENCY_CHOICES,
        default=CURRENCY_SYP,
        db_index=True,
    )
    # FX snapshot used for settlement conversion (SYP per 1 USD)
    fx_rate_used = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        null=True,
        blank=True,
    )

    class ValuationMode(models.TextChoices):
        HISTORICAL = "HISTORICAL", "Historical FX"
        CURRENT_FX = "CURRENT_FX", "Current FX"

    valuation_mode = models.CharField(
        max_length=16,
        choices=ValuationMode.choices,
        default=ValuationMode.HISTORICAL,
        db_index=True,
    )
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
        return self._creditor_entry_by_currency(CURRENCY_SYP)

    @property
    def creditor_entry_usd(self) -> "CreditorEntry | None":
        return self._creditor_entry_by_currency(CURRENCY_USD)

    @property
    def creditor_entries(self):
        cached = getattr(self, "_creditor_entries_cached", None)
        if cached is not None:
            return cached
        return list(
            CreditorEntry.objects.filter(
                source_app="billing",
                source_model="ProviderReturn",
            ).filter(
                Q(source_id=str(self.id))
                | Q(legacy_source_id=str(self.id))
                | Q(source_id=f"{self.id}:USD")
                | Q(legacy_source_id=f"{self.id}:USD")
            )
        )

    def _creditor_entry_by_currency(self, code: str):
        code = (code or "").upper()
        base_source_id = str(self.id)
        rows = []
        for e in self.creditor_entries:
            cur = (getattr(e, "currency_code", None) or "SYP").upper()
            if cur == code:
                rows.append(e)
        if rows:
            rows.sort(key=lambda e: _debt_entry_identity_priority(e, base_source_id=base_source_id))
            return rows[0]
        if code == CURRENCY_USD:
            legacy_rows = []
            for e in self.creditor_entries:
                if (e.source_id or "").endswith(":USD") or (getattr(e, "legacy_source_id", "") or "").endswith(":USD"):
                    legacy_rows.append(e)
            if legacy_rows:
                legacy_rows.sort(key=lambda e: _debt_entry_identity_priority(e, base_source_id=base_source_id))
                return legacy_rows[0]
        return None

    @property
    def collected_syp(self) -> Decimal:
        c = self.creditor_entry
        return (c.collected if c else DEC0) or DEC0

    @property
    def collected_usd(self) -> Decimal:
        c = self.creditor_entry_usd
        return (c.collected if c else DEC0) or DEC0

    @property
    def remaining_syp(self) -> Decimal:
        c = self.creditor_entry
        return (c.remaining if c else (self.total_syp or DEC0)) or DEC0

    @property
    def remaining_usd(self) -> Decimal:
        c = self.creditor_entry_usd
        return (c.remaining if c else (self.total_usd or DEC0)) or DEC0

    @property
    def paid_amount(self) -> Decimal:
        # Settlement-currency amount (legacy view)
        if (self.settlement_currency or "SYP") == CURRENCY_USD:
            return self.collected_usd
        return self.collected_syp

    @property
    def remaining(self) -> Decimal:
        # Settlement-currency remaining (legacy view)
        if (self.settlement_currency or "SYP") == CURRENCY_USD:
            return self.remaining_usd
        return self.remaining_syp

    @property
    def status(self) -> str:
        syp = self.creditor_entry
        usd = self.creditor_entry_usd

        if not syp and not usd:
            return ProviderReturn.Status.UNPAID

        rem_s = syp.remaining if syp else DEC0
        rem_u = usd.remaining if usd else DEC0
        if rem_s <= 0 and rem_u <= 0:
            return ProviderReturn.Status.PAID

        col_s = syp.collected if syp else DEC0
        col_u = usd.collected if usd else DEC0
        if (col_s and col_s > 0) or (col_u and col_u > 0):
            return ProviderReturn.Status.PARTIAL
        return ProviderReturn.Status.UNPAID

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
    product_name_at_txn = models.CharField(max_length=255, default="", blank=True)
    unit_index = models.IntegerField(choices=UnitIndex.choices, default=UnitIndex.PRIMARY)
    conv_factor_at_txn = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=Decimal("1.0000"),
    )
    unit_1_label_at_txn = models.CharField(max_length=32, default="")
    unit_2_label_at_txn = models.CharField(max_length=32, default="", blank=True)
    qty_used_at_txn = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    qty_primary= models.DecimalField(max_digits=14, decimal_places=3)
    currency   = models.CharField(
        max_length=3,
        choices=CURRENCY_CHOICES,
        default=CURRENCY_SYP,
        db_index=True,
    )
    cost       = models.DecimalField(max_digits=12, decimal_places=4, validators=[MinValueValidator(0)])
    line_total = models.DecimalField(max_digits=14, decimal_places=3, validators=[MinValueValidator(0)])
    fx_rate_at_txn = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["ret"]),
            models.Index(fields=["product"]),
        ]

    def __str__(self) -> str:
        name = (self.product_name_at_txn or "").strip() or getattr(self.product, "name", "")
        return f"{name} x {self.qty_primary} (#{self.ret.serial or self.ret_id})"

