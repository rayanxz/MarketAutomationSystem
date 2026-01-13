# catalog/models.py
from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, RegexValidator
from django.db import IntegrityError, models, transaction
from django.db.models.functions import Lower

from core.currency import CURRENCY_CHOICES, SYP, USD

# =========================
#   Collections (زُمَر)
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
                violation_error_message="اسم المجموعة موجود مسبقاً (حساسية غير مفعلة).",
            ),
        ]
        indexes = [
            models.Index(Lower("name"), name="ix_collection_name_ci"),
        ]
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.code} — {self.name}"

    def save(self, *args, **kwargs):
        # validate first
        self.full_clean()
        is_new = self.pk is None
        super().save(*args, **kwargs)
        if is_new and not self.code:
            self.code = f"#{self.pk:03d}"
            super(ProductCollection, self).save(update_fields=["code"])


# =========================
#   Sets (المجموعة الأب)
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
        return f"{self.code} — {self.name} ({self.collection.code})"

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
    GRAM = "g", "غرام"
    PIECE = "pc", "قطعة"
    LITER = "L", "ليتر"
    PKG = "PKG", "طرد"     # primary packages (new)
    BNDL = "BNDL", "حزمة"  # secondary packages (new)


# =========================
#   Product
# =========================
class Product(models.Model):
    # System-assigned sequential number; displayed as 3+ digit zero-padded string
    product_number = models.PositiveIntegerField(
        unique=True, db_index=True, blank=True, null=True
    )
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

    # Guard against using '#' or '@' for products (must start with a digit)
    code_format_validator = RegexValidator(
        r'^(?![#@])\d', 'Product code must start with a digit (not # or @)'
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                Lower("name"),
                name="uq_product_name_ci",
                violation_error_message="اسم المنتج موجود مسبقاً (بدون حساسية حالة الأحرف).",
            ),
        ]
        indexes = [
            models.Index(Lower("name"), name="ix_product_name_ci"),
            models.Index(fields=["set"]),
        ]
        ordering = ["name"]

    def __str__(self):
        return f"{self.display_code} — {self.name}"

    @property
    def display_code(self) -> str:
        # human format like 001, 010, 1000 (min 3 digits)
        n = self.product_number or 0
        return f"{n:03d}"

    # ---- validation & normalization ----
    def clean(self):
        # Ensure product codes don’t start with # or @ if someone sets product_number
        if self.product_number is not None:
            self.code_format_validator(str(self.product_number))

        # Units rule
        if self.unit_secondary:
            if not self.conversion_factor or self.conversion_factor <= 0:
                raise ValidationError(
                    {"conversion_factor": "Required and must be > 0 when second unit is set."}
                )
            # Prevent setting the same unit as primary/secondary
            if self.unit_primary == self.unit_secondary:
                raise ValidationError({"unit_secondary": "لا يجوز أن تكون الوحدة الثانية مطابقة للأولى."})
        else:
            # when there is no second unit, wipe optional factor
            self.conversion_factor = None

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

        # Assign the next product_number if not provided (importers can set it explicitly)
        if not self.product_number:
            # Simple max+1 with retry for rare race conditions
            for _ in range(5):
                try:
                    with transaction.atomic():
                        last = (
                            Product.objects.select_for_update()
                            .order_by("-product_number")
                            .values_list("product_number", flat=True)
                            .first()
                        )
                        self.product_number = 1 if last is None else last + 1
                        super().save(*args, **kwargs)
                        return
                except IntegrityError:
                    # retry on unique race
                    continue
            # final attempt without lock
            super().save(*args, **kwargs)
        else:
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
        PRIMARY = 1, "الوحدة الأولى"
        SECONDARY = 2, "الوحدة الثانية"

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="unit_ids")
    unit_index = models.IntegerField(choices=UnitIndex.choices)  # 1 or 2
    value = models.CharField(max_length=32, unique=True, db_index=True)  # globally unique

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["product"]),
            models.Index(fields=["unit_index"]),
        ]

    def __str__(self):
        u = "U1" if self.unit_index == self.UnitIndex.PRIMARY else "U2"
        return f"{self.value} ({u} — {self.product.display_code})"


# =========================
#   Product barcodes (lists)
# =========================
class ProductBarcode(models.Model):
    class UnitIndex(models.IntegerChoices):
        PRIMARY = 1, "الوحدة الأولى"
        SECONDARY = 2, "الوحدة الثانية"

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="barcodes")
    unit_index = models.IntegerField(choices=UnitIndex.choices)  # 1 or 2
    barcode = models.CharField(max_length=64, unique=True, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["product"]),
            models.Index(fields=["unit_index"]),
        ]

    def __str__(self):
        u = "U1" if self.unit_index == self.UnitIndex.PRIMARY else "U2"
        return f"{self.barcode} ({u} — {self.product.display_code})"
    

