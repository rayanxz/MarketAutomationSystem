from __future__ import annotations
from decimal import Decimal
from typing import List

from django import forms

from financials.models import MoneyContainer, Currency


class MoneyContainerForm(forms.ModelForm):
    class Meta:
        model = MoneyContainer
        fields = ["name", "is_active", "note"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "input", "placeholder": "مثال: خزنة الصباح / درج الكاشير"}),
            "note": forms.Textarea(attrs={"class": "input", "rows": 3, "placeholder": "ملاحظات اختيارية"}),
        }


class OpeningBalanceRowForm(forms.Form):
    currency_id = forms.IntegerField(widget=forms.HiddenInput())
    code = forms.CharField(required=False, widget=forms.TextInput(attrs={"class": "input", "readonly": "readonly"}))
    amount = forms.DecimalField(
        required=False,
        max_digits=18,
        decimal_places=6,
        widget=forms.NumberInput(attrs={"class": "input", "step": "any", "placeholder": "0"}),
    )

    def clean_amount(self):
        v = self.cleaned_data.get("amount")
        if v is None:
            return Decimal("0")
        return v


def build_opening_formset(*, currencies: List[Currency], data=None) -> forms.BaseFormSet:
    """
    Simple "formset-like" list. We keep it simple and explicit to avoid Django formset complexity.
    """
    forms_list: List[OpeningBalanceRowForm] = []
    for i, cur in enumerate(currencies):
        prefix = f"cur_{cur.id}"
        if data is None:
            f = OpeningBalanceRowForm(
                prefix=prefix,
                initial={"currency_id": cur.id, "code": cur.code, "amount": Decimal("0")},
            )
        else:
            f = OpeningBalanceRowForm(data=data, prefix=prefix)
        forms_list.append(f)
    return forms_list
