# catalog/forms.py
from __future__ import annotations

from typing import Optional, List
from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.db.models.functions import Lower

from catalog.models import (
    ProductCollection,
    UnitType,
    ProductBarcode,
    Product,
)
from core.currency import CURRENCY_CHOICES, SYP, USD

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
        self.instance = instance
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
        for n in (
            "cost",
            "price",
            "cost_syp",
            "cost_usd",
            "price_syp",
            "price_usd",
            "default_cost_syp",
            "default_cost_usd",
            "default_price_syp",
            "default_price_usd",
            "conversion_factor",
            "stock_qty",
        ):
            if n in self.fields:
                self.fields[n].widget.attrs.setdefault("class", "input")
                if n in (
                    "cost",
                    "price",
                    "conversion_factor",
                    "cost_syp",
                    "cost_usd",
                    "price_syp",
                    "price_usd",
                    "default_cost_syp",
                    "default_cost_usd",
                    "default_price_syp",
                    "default_price_usd",
                ):
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

    cost = forms.DecimalField(label="Oچیلفة", max_digits=12, decimal_places=4, min_value=0, required=False)
    price = forms.DecimalField(label="Oپرح", max_digits=12, decimal_places=4, min_value=0, required=False)

    # Legacy currency-aware fields (kept for backward compatibility)
    cost_syp = forms.DecimalField(label="Cost (SYP)", max_digits=12, decimal_places=4, min_value=0, required=False)
    cost_usd = forms.DecimalField(label="Cost (USD)", max_digits=12, decimal_places=4, min_value=0, required=False)
    price_syp = forms.DecimalField(label="Price (SYP)", max_digits=12, decimal_places=4, min_value=0, required=False)
    price_usd = forms.DecimalField(label="Price (USD)", max_digits=12, decimal_places=4, min_value=0, required=False)

    # New: currency permissions
    allow_syp_sales = forms.BooleanField(label="Allow SYP sales", required=False, initial=True)
    allow_syp_purchasing = forms.BooleanField(label="Allow SYP purchasing", required=False, initial=True)
    allow_usd_sales = forms.BooleanField(label="Allow USD sales", required=False)
    allow_usd_purchasing = forms.BooleanField(label="Allow USD purchasing", required=False)

    default_purchase_currency = forms.ChoiceField(
        label="Default purchase currency",
        choices=[("", "----")] + list(CURRENCY_CHOICES),
        required=False,
    )
    default_sale_currency = forms.ChoiceField(
        label="Default sale currency",
        choices=[("", "----")] + list(CURRENCY_CHOICES),
        required=False,
    )

    default_cost_syp = forms.DecimalField(label="Default cost (SYP)", max_digits=12, decimal_places=4, min_value=0, required=False)
    default_cost_usd = forms.DecimalField(label="Default cost (USD)", max_digits=12, decimal_places=4, min_value=0, required=False)
    default_price_syp = forms.DecimalField(label="Default price (SYP)", max_digits=12, decimal_places=4, min_value=0, required=False)
    default_price_usd = forms.DecimalField(label="Default price (USD)", max_digits=12, decimal_places=4, min_value=0, required=False)

    # Unit IDs are handled as repeated inputs in the UI; fields exist for error binding only.
    unit_primary_ids = forms.CharField(label="Unit IDs (primary)", required=False)
    unit_secondary_ids = forms.CharField(label="Unit IDs (secondary)", required=False)

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
        qs = Product.objects.filter(name__iexact=name_norm, is_active=True)
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

        if u2 and u1 == u2:
            if cleaned.get("conversion_factor") in (None, ""):
                if self._exclude_pk and getattr(self.instance, "conversion_factor", None):
                    cleaned["conversion_factor"] = self.instance.conversion_factor
                else:
                    cleaned["conversion_factor"] = Decimal("1")
        else:
            # If a secondary unit is chosen -> conversion factor is required
            if u2 and not cf:
                self.add_error("conversion_factor", "مطلوب عند تحديد الوحدة الثانية.")
            # If no secondary unit -> nullify conversion factor
            if not u2:
                cleaned["conversion_factor"] = None

        # --- Friendly duplicate barcode check (bulk + ignore self when editing) ---
        field_barcodes_u1 = self.parse_barcodes(cleaned.get("barcodes_u1"))
        field_barcodes_u2 = self.parse_barcodes(cleaned.get("barcodes_u2"))
        all_barcodes: List[str] = list(dict.fromkeys(field_barcodes_u1 + field_barcodes_u2))

        if all_barcodes:
            qs = ProductBarcode.objects.filter(barcode__in=all_barcodes, is_active=True)
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

        # Currency rules (new flags)
        allow_syp_purch = bool(cleaned.get("allow_syp_purchasing"))
        allow_usd_purch = bool(cleaned.get("allow_usd_purchasing"))
        allow_syp_sales = bool(cleaned.get("allow_syp_sales"))
        allow_usd_sales = bool(cleaned.get("allow_usd_sales"))

        default_purchase_currency = cleaned.get("default_purchase_currency") or None
        default_sale_currency = cleaned.get("default_sale_currency") or None

        if not allow_syp_purch and not allow_usd_purch:
            cleaned["default_purchase_currency"] = None
        if not allow_syp_sales and not allow_usd_sales:
            cleaned["default_sale_currency"] = None

        if allow_syp_purch and not allow_usd_purch:
            cleaned["default_purchase_currency"] = SYP
        if allow_usd_purch and not allow_syp_purch:
            cleaned["default_purchase_currency"] = USD

        if allow_syp_purch or allow_usd_purch:
            if default_purchase_currency and default_purchase_currency not in ("SYP", "USD"):
                self.add_error("default_purchase_currency", "Invalid currency.")
            if default_purchase_currency == "SYP" and not allow_syp_purch:
                self.add_error("default_purchase_currency", "Default purchase currency must be enabled.")
            if default_purchase_currency == "USD" and not allow_usd_purch:
                self.add_error("default_purchase_currency", "Default purchase currency must be enabled.")

        if allow_syp_sales and not allow_usd_sales:
            cleaned["default_sale_currency"] = SYP
        if allow_usd_sales and not allow_syp_sales:
            cleaned["default_sale_currency"] = USD

        if allow_syp_sales or allow_usd_sales:
            if default_sale_currency and default_sale_currency not in ("SYP", "USD"):
                self.add_error("default_sale_currency", "Invalid currency.")
            if default_sale_currency == "SYP" and not allow_syp_sales:
                self.add_error("default_sale_currency", "Default sale currency must be enabled.")
            if default_sale_currency == "USD" and not allow_usd_sales:
                self.add_error("default_sale_currency", "Default sale currency must be enabled.")

        if not allow_syp_purch:
            cleaned["default_cost_syp"] = Decimal("0.0000")
        if not allow_usd_purch:
            cleaned["default_cost_usd"] = Decimal("0.0000")
        if not allow_syp_sales:
            cleaned["default_price_syp"] = Decimal("0.0000")
        if not allow_usd_sales:
            cleaned["default_price_usd"] = Decimal("0.0000")

        for fname in (
            "default_cost_syp",
            "default_cost_usd",
            "default_price_syp",
            "default_price_usd",
            "cost",
            "price",
            "cost_syp",
            "cost_usd",
            "price_syp",
            "price_usd",
        ):
            if cleaned.get(fname) in (None, ""):
                cleaned[fname] = Decimal("0.0000")

        # Legacy cost/price fallbacks (keep DB fields non-null)
        if cleaned.get("cost") in (None, ""):
            if not allow_syp_purch and not allow_usd_purch:
                cleaned["cost"] = Decimal("0.0000")
            else:
                eff_cur = cleaned.get("default_purchase_currency") or (SYP if allow_syp_purch else USD)
                cleaned["cost"] = cleaned.get("default_cost_usd") if eff_cur == "USD" else cleaned.get("default_cost_syp")
        if cleaned.get("price") in (None, ""):
            if not allow_syp_sales and not allow_usd_sales:
                cleaned["price"] = Decimal("0.0000")
            else:
                eff_cur = cleaned.get("default_sale_currency") or (SYP if allow_syp_sales else USD)
                cleaned["price"] = cleaned.get("default_price_usd") if eff_cur == "USD" else cleaned.get("default_price_syp")

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


