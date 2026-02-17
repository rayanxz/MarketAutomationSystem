# catalog/models.py
from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.apps import apps
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower

from core.currency import CURRENCY_CHOICES, SYP, USD

# =========================
#   Collections (Ø²ÙÙ…ÙŽØ±)
# =========================
class ProductCollection(models.Model):
    """
    Small, indexed table of product collections (folders).
    - name: unique (case-insensitive)
    - code: stored like '#001', unique, searchable
    """
    name = models.CharField(max_length=64, unique=True)

    code = models.CharField(
        max_length=8,
        unique=True,
        db_index=True,
        validators=[RegexValidator(r'^#\d{3,}$', message='Code must look like #001')],
        blank=True,  # left blank to allow first save() to generate it
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            # Case-insensitive uniqueness on name
            models.UniqueConstraint(
                Lower("name"),
                name="uq_collection_name_ci",
                violation_error_message="Ø§Ø³Ù… Ø§Ù„Ù…Ø¬Ù…ÙˆØ¹Ø© Ù…ÙˆØ¬ÙˆØ¯ Ù…Ø³Ø¨Ù‚Ø§Ù‹ (Ø­Ø³Ø§Ø³ÙŠØ© ØºÙŠØ± Ù…ÙØ¹Ù„Ø©).",
            ),
        ]
        indexes = [
            models.Index(Lower("name"), name="ix_collection_name_ci"),
        ]
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.code} â€” {self.name}"

    def save(self, *args, **kwargs):
        # validate first
        self.full_clean()
        is_new = self.pk is None
        super().save(*args, **kwargs)
        if is_new and not self.code:
            self.code = f"#{self.pk:03d}"
            super(ProductCollection, self).save(update_fields=["code"])


# =========================
#   Sets (Ø§Ù„Ù…Ø¬Ù…ÙˆØ¹Ø© Ø§Ù„Ø£Ø¨)
# =========================
class ProductSet(models.Model):
    collection = models.ForeignKey(
        ProductCollection, on_delete=models.PROTECT, related_name="sets"
    )
    name = models.CharField(max_length=64)
    code = models.CharField(
        max_length=8, unique=True, db_index=True, blank=True,
        validators=[RegexValidator(r'^@\d{3,}$', 'Set code must look like @001')],
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            # same set name cannot repeat within the same collection (case-insensitive)
            models.UniqueConstraint(
                Lower("name"), "collection", name="uq_set_name_ci_per_collection"
            ),
        ]
        indexes = [
            models.Index(fields=["collection"]),
            models.Index(Lower("name"), name="ix_set_name_ci"),
        ]
        ordering = ["collection__name", "name"]

    def __str__(self):
        return f"{self.code} â€” {self.name} ({self.collection.code})"

    def save(self, *args, **kwargs):
        # validate first
        self.full_clean()
        is_new = self.pk is None
        super().save(*args, **kwargs)  # get PK
        if is_new and not self.code:
            self.code = f"@{self.pk:03d}"
            super(ProductSet, self).save(update_fields=["code"])


# =========================
#   Units enum
# =========================
class UnitType(models.TextChoices):
    GRAM = "g", "ØºØ±Ø§Ù…"
    PIECE = "pc", "Ù‚Ø·Ø¹Ø©"
    LITER = "L", "Ù„ÙŠØªØ±"
    PKG = "PKG", "Ø·Ø±Ø¯"     # primary packages (new)
    BNDL = "BNDL", "Ø­Ø²Ù…Ø©"  # secondary packages (new)


# =========================
#   Product
# =========================
class Product(models.Model):
    is_active = models.BooleanField(default=True, db_index=True)

    # Name (case-insensitive unique enforced via DB constraint below)
    name = models.CharField(max_length=128)

    # Inventory
    stock_qty = models.DecimalField(
        max_digits=14, decimal_places=3, default=0,
        help_text="Current stock in primary unit; may be negative."
    )

    # Hierarchy: Product -> Set (@xxx) -> Collection (#xxx)
    set = models.ForeignKey(ProductSet, on_delete=models.PROTECT, related_name="products")

    # Units
    unit_primary = models.CharField(max_length=4, choices=UnitType.choices)
    unit_secondary = models.CharField(max_length=4, choices=UnitType.choices, blank=True)
    conversion_factor = models.DecimalField(
        max_digits=12, decimal_places=4, null=True, blank=True,
        validators=[MinValueValidator(0.0001)],
        help_text="How many primary units in one secondary unit (e.g., 1000 g per 1 L).",
    )

    # Money (legacy single-currency fields; kept for backward compatibility)
    cost = models.DecimalField(max_digits=12, decimal_places=4, validators=[MinValueValidator(0)])
    price = models.DecimalField(max_digits=12, decimal_places=4, validators=[MinValueValidator(0)])

    # Currency-aware defaults (legacy; kept to avoid breaking older code)
    cost_syp = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    cost_usd = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    price_syp = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    price_usd = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)

    # New: per-currency sales/purchasing controls
    allow_syp_sales = models.BooleanField(default=True)
    allow_syp_purchasing = models.BooleanField(default=True)
    allow_usd_sales = models.BooleanField(default=False)
    allow_usd_purchasing = models.BooleanField(default=False)

    default_purchase_currency = models.CharField(
        max_length=3,
        choices=CURRENCY_CHOICES,
        null=True,
        blank=True,
    )
    default_sale_currency = models.CharField(
        max_length=3,
        choices=CURRENCY_CHOICES,
        null=True,
        blank=True,
    )

    default_cost_syp = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal("0.0000"))
    default_cost_usd = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal("0.0000"))
    default_price_syp = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal("0.0000"))
    default_price_usd = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal("0.0000"))

    latest_cost_syp = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal("0.0000"))
    latest_cost_usd = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal("0.0000"))
    latest_price_syp = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal("0.0000"))
    latest_price_usd = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal("0.0000"))

    # Legacy flags/defaults (kept for backward compatibility)
    enable_syp = models.BooleanField(default=True)
    enable_usd = models.BooleanField(default=False)

    default_currency = models.CharField(
        max_length=3,
        choices=CURRENCY_CHOICES,
        null=True,
        blank=True,
    )

    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                Lower("name"),
                condition=Q(is_active=True),
                name="uq_product_name_ci_active",
                violation_error_message="Ø§Ø³Ù… Ø§Ù„Ù…Ù†ØªØ¬ Ù…ÙˆØ¬ÙˆØ¯ Ù…Ø³Ø¨Ù‚Ø§Ù‹ (Ø¨Ø¯ÙˆÙ† Ø­Ø³Ø§Ø³ÙŠØ© Ø­Ø§Ù„Ø© Ø§Ù„Ø£Ø­Ø±Ù).",
            ),
        ]
        indexes = [
            models.Index(Lower("name"), name="ix_product_name_ci"),
            models.Index(fields=["set"]),
        ]
        ordering = ["name"]

    def __str__(self):
        return f"{self.id} â€” {self.name}"

    @property
    def is_single_unit(self) -> bool:
        return not self.unit_secondary or self.unit_primary == self.unit_secondary

    def has_history(self) -> bool:
        """
        True if any historical usage exists for this product.
        """
        if not self.pk:
            return False
        checks = [
            ("inventory", "ProductMovement", {"product_id": self.pk}),
            ("billing", "BillItem", {"product_id": self.pk}),
            ("billing", "ProviderReturnItem", {"product_id": self.pk}),
            ("pos", "SalesBillRow", {"product_id": self.pk}),
            ("pos", "SalesReturnRow", {"product_id": self.pk}),
            # Derived cost layers (may be rebuilt); keep as a conservative signal.
            ("stock", "StockFifoLayer", {"product_id": self.pk}),
        ]
        for app_label, model_name, kwargs in checks:
            try:
                Model = apps.get_model(app_label, model_name)
            except LookupError:
                continue
            if Model.objects.filter(**kwargs).exists():
                return True
        return False

    def locked_fields_changed(self, old: "Product") -> list[str]:
        def _norm_secondary(val: str | None) -> str:
            return (val or "").strip()

        def _norm_conv(val, unit_secondary: str) -> Decimal | None:
            if not unit_secondary:
                return None
            if val in (None, ""):
                return None
            try:
                return Decimal(str(val))
            except Exception:
                return None

        changed: list[str] = []

        if (old.name or "").strip() != (self.name or "").strip():
            changed.append("name")
        if int(getattr(old, "set_id", 0) or 0) != int(getattr(self, "set_id", 0) or 0):
            changed.append("set")
        if (old.unit_primary or "") != (self.unit_primary or ""):
            changed.append("unit_primary")

        old_sec = _norm_secondary(old.unit_secondary)
        new_sec = _norm_secondary(self.unit_secondary)
        if old_sec != new_sec:
            changed.append("unit_secondary")

        old_requires_conv = bool(old_sec and old_sec != (old.unit_primary or ""))
        new_requires_conv = bool(new_sec and new_sec != (self.unit_primary or ""))
        if old_requires_conv or new_requires_conv:
            old_conv = _norm_conv(old.conversion_factor, old_sec)
            new_conv = _norm_conv(self.conversion_factor, new_sec)
            if old_conv != new_conv:
                changed.append("conversion_factor")

        return changed

    # ---- validation & normalization ----
    def clean(self):
        # Units rule
        if self.unit_secondary:
            if self.unit_primary == self.unit_secondary:
                self.conversion_factor = Decimal("1")
            else:
                if not self.conversion_factor or self.conversion_factor <= 0:
                    raise ValidationError(
                        {"conversion_factor": "Required and must be > 0 when second unit is set."}
                    )
        else:
            self.conversion_factor = None

        # Lock core identity fields once history exists
        if self.pk:
            try:
                old = Product.objects.get(pk=self.pk)
            except Product.DoesNotExist:
                old = None
            if old is not None:
                changed = self.locked_fields_changed(old)
                if changed and old.has_history():
                    raise ValidationError(
                        {f: "This field is locked after the product has history." for f in changed}
                    )

        # Currency enable/disable rules (new flags)
        purchase_syp = bool(self.allow_syp_purchasing)
        purchase_usd = bool(self.allow_usd_purchasing)
        sale_syp = bool(self.allow_syp_sales)
        sale_usd = bool(self.allow_usd_sales)

        # Keep legacy flags in sync for older code paths
        self.enable_syp = bool(purchase_syp or sale_syp)
        self.enable_usd = bool(purchase_usd or sale_usd)

        # Normalize defaults for purchasing
        if purchase_syp and not purchase_usd:
            self.default_purchase_currency = SYP
        elif purchase_usd and not purchase_syp:
            self.default_purchase_currency = USD
        elif not (purchase_syp or purchase_usd):
            self.default_purchase_currency = None

        if self.default_purchase_currency and self.default_purchase_currency not in (SYP, USD):
            raise ValidationError("Default purchase currency must be SYP or USD.")
        if self.default_purchase_currency == SYP and not purchase_syp:
            raise ValidationError("Default purchase currency must be enabled for purchasing.")
        if self.default_purchase_currency == USD and not purchase_usd:
            raise ValidationError("Default purchase currency must be enabled for purchasing.")

        # Normalize defaults for sales
        if sale_syp and not sale_usd:
            self.default_sale_currency = SYP
        elif sale_usd and not sale_syp:
            self.default_sale_currency = USD
        elif not (sale_syp or sale_usd):
            self.default_sale_currency = None

        if self.default_sale_currency and self.default_sale_currency not in (SYP, USD):
            raise ValidationError("Default sale currency must be SYP or USD.")
        if self.default_sale_currency == SYP and not sale_syp:
            raise ValidationError("Default sale currency must be enabled for sales.")
        if self.default_sale_currency == USD and not sale_usd:
            raise ValidationError("Default sale currency must be enabled for sales.")

        # Force defaults to zero when currency is disabled
        if not purchase_syp:
            self.default_cost_syp = Decimal("0.0000")
        if not purchase_usd:
            self.default_cost_usd = Decimal("0.0000")
        if not sale_syp:
            self.default_price_syp = Decimal("0.0000")
        if not sale_usd:
            self.default_price_usd = Decimal("0.0000")

        # Keep legacy default_currency aligned with purchase defaults when possible
        if self.default_purchase_currency:
            self.default_currency = self.default_purchase_currency
        elif self.enable_syp and not self.enable_usd:
            self.default_currency = SYP
        elif self.enable_usd and not self.enable_syp:
            self.default_currency = USD

    def save(self, *args, **kwargs):
        # validate + normalize first
        self.full_clean()
        super().save(*args, **kwargs)

    # ---- currency helpers ----
    def get_effective_default_purchase_currency(self) -> str:
        if self.allow_syp_purchasing and not self.allow_usd_purchasing:
            return SYP
        if self.allow_usd_purchasing and not self.allow_syp_purchasing:
            return USD
        if self.allow_syp_purchasing and self.allow_usd_purchasing:
            return self.default_purchase_currency if self.default_purchase_currency in (SYP, USD) else SYP
        return SYP

    def get_effective_default_sale_currency(self) -> str:
        if self.allow_syp_sales and not self.allow_usd_sales:
            return SYP
        if self.allow_usd_sales and not self.allow_syp_sales:
            return USD
        if self.allow_syp_sales and self.allow_usd_sales:
            return self.default_sale_currency if self.default_sale_currency in (SYP, USD) else SYP
        return SYP

    def get_default_cost_for_currency(self, curr: str) -> Decimal:
        cur = (curr or SYP).upper()
        return self.default_cost_usd if cur == USD else self.default_cost_syp

    def get_default_price_for_currency(self, curr: str) -> Decimal:
        cur = (curr or SYP).upper()
        return self.default_price_usd if cur == USD else self.default_price_syp


# =========================
#   Product unit IDs (lists)
# =========================
class ProductUnitId(models.Model):
    class UnitIndex(models.IntegerChoices):
        PRIMARY = 1, "Ø§Ù„ÙˆØ­Ø¯Ø© Ø§Ù„Ø£ÙˆÙ„Ù‰"
        SECONDARY = 2, "Ø§Ù„ÙˆØ­Ø¯Ø© Ø§Ù„Ø«Ø§Ù†ÙŠØ©"

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="unit_ids")
    unit_index = models.IntegerField(choices=UnitIndex.choices)  # 1 or 2
    value = models.CharField(max_length=32, unique=False, db_index=True)  # unique among active
    is_active = models.BooleanField(default=True, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["value"],
                condition=Q(is_active=True),
                name="uq_unit_id_value_active",
            ),
        ]
        indexes = [
            models.Index(fields=["product"]),
            models.Index(fields=["unit_index"]),
        ]

    def __str__(self):
        u = "U1" if self.unit_index == self.UnitIndex.PRIMARY else "U2"
        return f"{self.value} ({u} â€” {self.product_id})"


# =========================
#   Product barcodes (lists)
# =========================
class ProductBarcode(models.Model):
    class UnitIndex(models.IntegerChoices):
        PRIMARY = 1, "Ø§Ù„ÙˆØ­Ø¯Ø© Ø§Ù„Ø£ÙˆÙ„Ù‰"
        SECONDARY = 2, "Ø§Ù„ÙˆØ­Ø¯Ø© Ø§Ù„Ø«Ø§Ù†ÙŠØ©"

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="barcodes")
    unit_index = models.IntegerField(choices=UnitIndex.choices)  # 1 or 2
    barcode = models.CharField(max_length=64, unique=False, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["barcode"],
                condition=Q(is_active=True),
                name="uq_barcode_active",
            ),
        ]
        indexes = [
            models.Index(fields=["product"]),
            models.Index(fields=["unit_index"]),
        ]

    def __str__(self):
        u = "U1" if self.unit_index == self.UnitIndex.PRIMARY else "U2"
        return f"{self.barcode} ({u} â€” {self.product_id})"
    


