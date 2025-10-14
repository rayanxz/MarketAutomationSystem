# catalog/models.py
from __future__ import annotations

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, RegexValidator
from django.db import IntegrityError, models, transaction
from django.db.models.functions import Lower


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

    # Global-unique name
    name = models.CharField(max_length=128, unique=True)

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

    # Money
    cost = models.DecimalField(max_digits=12, decimal_places=4, validators=[MinValueValidator(0)])
    price = models.DecimalField(max_digits=12, decimal_places=4, validators=[MinValueValidator(0)])

    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Guard against using '#' or '@' for products (must start with a digit)
    code_format_validator = RegexValidator(
        r'^(?![#@])\d', 'Product code must start with a digit (not # or @)'
    )

    class Meta:
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
        else:
            # when there is no second unit, wipe optional factor
            self.conversion_factor = None

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
        unique_together = [("product", "unit_index", "barcode")]

    def __str__(self):
        u = "U1" if self.unit_index == 1 else "U2"
        return f"{self.barcode} ({u} — {self.product.display_code})"
