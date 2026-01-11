# catalog/forms.py
from __future__ import annotations

from typing import Optional, List

from django import forms
from django.core.exceptions import ValidationError
from django.db.models.functions import Lower

from catalog.models import (
    ProductCollection,
    UnitType,
    ProductBarcode,
    Product,
)
from core.currency import CURRENCY_CHOICES, SYP

# =========================
#    Collections (زُمَر)
# =========================
class CollectionCreateForm(forms.ModelForm):
    class Meta:
        model = ProductCollection
        fields = ["name"]
        widgets = {
            "name": forms.TextInput(
                attrs={
                    "class": "input",
                    "placeholder": "اسم الزمرة (مثال: منظفات، سجائر)",
                }
            )
        }
        labels = {"name": "اسم الزمرة"}

    def clean_name(self) -> str:
        name = (self.cleaned_data.get("name") or "").strip()
        if not name:
            raise ValidationError("يرجى إدخال اسم الزمرة.")
        # Defensive CI uniqueness (DB constraint also exists)
        exists = (
            ProductCollection.objects.annotate(n=Lower("name"))
            .filter(n=name.lower())
            .exists()
        )
        if exists:
            raise ValidationError("اسم الزمرة موجود مسبقاً.")
        return name


# =========================
#     Product Create/Edit
# =========================
class ProductCreateForm(forms.Form):
    """
    Used for both create & edit.
    Pass `instance=Product(...)` or `exclude_pk=<id>` so CI name/barcode checks ignore self on edit.
    """

    # ---------- ctor: allow instance/exclude_pk for CI checks ----------
    def __init__(
        self,
        *args,
        instance: Optional[Product] = None,
        exclude_pk: Optional[int] = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self._exclude_pk = exclude_pk if exclude_pk is not None else (instance.pk if instance else None)

        # Tiny UX touches
        self.fields["collection_name"].widget.attrs.setdefault("class", "input")
        self.fields["collection_name"].widget.attrs.setdefault("placeholder", "اسم الزمرة")
        self.fields["set_name"].widget.attrs.setdefault("class", "input")
        self.fields["set_name"].widget.attrs.setdefault("placeholder", "اسم المجموعة الأب")
        self.fields["name"].widget.attrs.setdefault("class", "input")
        self.fields["name"].widget.attrs.setdefault("placeholder", "اسم المنتج")
        self.fields["notes"].widget.attrs.setdefault("class", "input")
        self.fields["notes"].widget.attrs.setdefault("dir", "rtl")

        # Match decimal_places=4 so browsers don't fight the user
        for n in ("cost", "price", "cost_syp", "cost_usd", "price_syp", "price_usd", "conversion_factor", "stock_qty"):
            if n in self.fields:
                self.fields[n].widget.attrs.setdefault("class", "input")
                if n in ("cost", "price", "conversion_factor", "cost_syp", "cost_usd", "price_syp", "price_usd"):
                    self.fields[n].widget.attrs.setdefault("step", "0.0001")

    # ---------- Hierarchy ----------
    collection_name = forms.CharField(label="اسم الزمرة", max_length=64)
    set_name = forms.CharField(label="اسم المجموعة الأب", max_length=64, required=False)
    create_parent = forms.BooleanField(label="إنشاء مجموعة أب جديدة", required=False)

    # ---------- Product core ----------
    name = forms.CharField(label="اسم المنتج", max_length=128)

    unit_primary = forms.ChoiceField(label="الوحدة الأولى", choices=UnitType.choices)
    unit_secondary = forms.ChoiceField(
        label="الوحدة الثانية (اختياري)", choices=UnitType.choices, required=False
    )
    conversion_factor = forms.DecimalField(
        label="عامل التحويل (عدد الوحدات الأولى في الثانية)",
        required=False,
        max_digits=12,
        decimal_places=4,
        min_value=0.0001,
    )

    cost = forms.DecimalField(label="الكلفة", max_digits=12, decimal_places=4, min_value=0)
    price = forms.DecimalField(label="السعر", max_digits=12, decimal_places=4, min_value=0)

    cost_syp = forms.DecimalField(label="Cost (SYP)", max_digits=12, decimal_places=4, min_value=0, required=False)
    cost_usd = forms.DecimalField(label="Cost (USD)", max_digits=12, decimal_places=4, min_value=0, required=False)
    price_syp = forms.DecimalField(label="Price (SYP)", max_digits=12, decimal_places=4, min_value=0, required=False)
    price_usd = forms.DecimalField(label="Price (USD)", max_digits=12, decimal_places=4, min_value=0, required=False)

    enable_syp = forms.BooleanField(label="Enable SYP", required=False, initial=True)
    enable_usd = forms.BooleanField(label="Enable USD", required=False)
    default_currency = forms.ChoiceField(
        label="Default currency",
        choices=[("", "----")] + list(CURRENCY_CHOICES),
        required=False,
    )

    notes = forms.CharField(
        label="ملاحظات",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    # ---------- Barcodes (textarea fallback; UI may also send lists) ----------
    barcodes_u1 = forms.CharField(
        label="باركودات الوحدة الأولى (اختياري)",
        required=False,
        widget=forms.Textarea(attrs={"rows": 2, "dir": "ltr"}),
    )
    barcodes_u2 = forms.CharField(
        label="باركودات الوحدة الثانية (اختياري)",
        required=False,
        widget=forms.Textarea(attrs={"rows": 2, "dir": "ltr"}),
    )

    # ---------------- Field-level validation ----------------
    def clean_name(self) -> str:
        """Normalize & enforce CI uniqueness (excluding self if provided)."""
        raw = (self.cleaned_data.get("name") or "").strip()
        name_norm = " ".join(raw.split())
        if not name_norm:
            raise ValidationError("يرجى إدخال اسم المنتج.")
        qs = Product.objects.filter(name__iexact=name_norm)
        if self._exclude_pk:
            qs = qs.exclude(pk=self._exclude_pk)
        if qs.exists():
            raise ValidationError("اسم المنتج موجود مسبقاً.")
        return name_norm

    # ---------------- Form-wide validation ----------------
    def clean(self) -> dict:
        cleaned = super().clean()

        # Secondary unit rules
        u1 = cleaned.get("unit_primary") or ""
        u2 = cleaned.get("unit_secondary") or ""
        cf = cleaned.get("conversion_factor")

        # If a secondary unit is chosen -> conversion factor is required
        if u2 and not cf:
            self.add_error("conversion_factor", "مطلوب عند تحديد الوحدة الثانية.")
        # If no secondary unit -> nullify conversion factor
        if not u2:
            cleaned["conversion_factor"] = None

        # Prevent setting the same unit as primary/secondary
        if u2 and u1 == u2:
            self.add_error("unit_secondary", "لا يجوز أن تكون الوحدة الثانية مطابقة للأولى.")

        # --- Friendly duplicate barcode check (bulk + ignore self when editing) ---
        field_barcodes_u1 = self.parse_barcodes(cleaned.get("barcodes_u1"))
        field_barcodes_u2 = self.parse_barcodes(cleaned.get("barcodes_u2"))
        all_barcodes: List[str] = list(dict.fromkeys(field_barcodes_u1 + field_barcodes_u2))

        if all_barcodes:
            qs = ProductBarcode.objects.filter(barcode__in=all_barcodes)
            if self._exclude_pk:
                qs = qs.exclude(product_id=self._exclude_pk)
            taken = set(qs.values_list("barcode", flat=True))

            if taken:
                offending_u1 = [bc for bc in field_barcodes_u1 if bc in taken]
                offending_u2 = [bc for bc in field_barcodes_u2 if bc in taken]
                if offending_u1:
                    self.add_error("barcodes_u1", f"الباركودات التالية مستخدمة مسبقاً: {', '.join(offending_u1)}")
                if offending_u2:
                    self.add_error("barcodes_u2", f"الباركودات التالية مستخدمة مسبقاً: {', '.join(offending_u2)}")

        # Currency rules
        enable_syp = bool(cleaned.get("enable_syp"))
        enable_usd = bool(cleaned.get("enable_usd"))
        default_currency = cleaned.get("default_currency") or None

        if not enable_syp and not enable_usd:
            self.add_error(None, "At least one currency must be enabled.")
        if enable_syp and not enable_usd:
            cleaned["default_currency"] = SYP
        if default_currency and default_currency not in ("SYP", "USD"):
            self.add_error("default_currency", "Invalid currency.")
        if default_currency == "SYP" and not enable_syp:
            self.add_error("default_currency", "Default currency must be enabled.")
        if default_currency == "USD" and not enable_usd:
            self.add_error("default_currency", "Default currency must be enabled.")

        # Price vs cost (optional business rule)
        cost = cleaned.get("cost")
        price = cleaned.get("price")
        if cost is not None and price is not None and price < cost:
            self.add_error("price", "تحذير: السعر أقل من الكلفة.")

        return cleaned

    # ---------------- Utilities ----------------
    @staticmethod
    def parse_barcodes(s: Optional[str]) -> List[str]:
        """Split on commas/spaces/newlines; strip; drop empties/dups; preserve order."""
        if not s:
            return []
        raw_tokens: List[str] = []
        # Normalize newlines to spaces, then split on commas and spaces
        for chunk in s.replace("\n", " ").split(","):
            raw_tokens.extend(chunk.split(" "))
        seen, out = set(), []
        for token in (t.strip() for t in raw_tokens):
            if not token or token in seen:
                continue
            seen.add(token)
            out.append(token)
        return out


