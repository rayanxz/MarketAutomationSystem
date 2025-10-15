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
    Pass `instance=Product(...)` when editing so name uniqueness excludes self.
    """

    # ------------- ctor: allow instance/exclude_pk for CI name check -------------
    def __init__(
        self,
        *args,
        instance: Optional[Product] = None,
        exclude_pk: Optional[int] = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self._exclude_pk = exclude_pk if exclude_pk is not None else (
            instance.pk if instance else None
        )

        # Tiny UX touches
        self.fields["collection_name"].widget.attrs.setdefault("class", "input")
        self.fields["collection_name"].widget.attrs.setdefault("placeholder", "اسم الزمرة")
        self.fields["set_name"].widget.attrs.setdefault("class", "input")
        self.fields["set_name"].widget.attrs.setdefault("placeholder", "اسم المجموعة الأب")
        self.fields["name"].widget.attrs.setdefault("class", "input")
        self.fields["name"].widget.attrs.setdefault("placeholder", "اسم المنتج")
        self.fields["notes"].widget.attrs.setdefault("class", "input")
        self.fields["notes"].widget.attrs.setdefault("dir", "rtl")

        for n in ("cost", "price", "conversion_factor", "stock_qty"):
            if n in self.fields:
                self.fields[n].widget.attrs.setdefault("class", "input")
                # numeric inputs
                if n in ("cost", "price", "conversion_factor"):
                    self.fields[n].widget.attrs.setdefault("step", "0.01")

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

        # Friendly duplicate barcode check (DB unique remains the source of truth)
        for field in ("barcodes_u1", "barcodes_u2"):
            for bc in self.parse_barcodes(cleaned.get(field)):
                if ProductBarcode.objects.filter(barcode=bc).exists():
                    self.add_error(field, f"الباركود {bc} مستخدم مسبقاً.")

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