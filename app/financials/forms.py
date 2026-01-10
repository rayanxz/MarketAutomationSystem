# financials/forms.py
from __future__ import annotations
from decimal import Decimal
from typing import List

from django import forms
from django.contrib.auth import get_user_model

from financials.models import MoneyContainer, Currency, ContainerFeature, FxSettings


User = get_user_model()


class MoneyContainerForm(forms.ModelForm):
    # currencies checkboxes (still from Currency table)
    currencies = forms.ModelMultipleChoiceField(
        queryset=Currency.objects.filter(is_active=True, code__in=["SYP", "USD"]).order_by("code"),
        required=True,
        widget=forms.CheckboxSelectMultiple,
        label="العملات",
    )

    features = forms.ModelMultipleChoiceField(
        queryset=ContainerFeature.objects.filter(is_active=True).order_by("sort_order", "id"),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="المهام",
    )

    allowed_users = forms.ModelMultipleChoiceField(
        queryset=User.objects.all().order_by("username"),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="الحسابات المسموحة",
    )

    class Meta:
        model = MoneyContainer
        fields = ["ref_code", "name", "container_type", "is_active", "note"]
        widgets = {
            "ref_code": forms.TextInput(attrs={"class": "input", "readonly": "readonly", "autocomplete": "off",}),
            "name": forms.TextInput(attrs={"class": "input", "placeholder": "مثال: درج الكاشير / خزنة الصباح"}),
            "container_type": forms.Select(attrs={"class": "input", "id": "id_container_type"}),
            "note": forms.Textarea(attrs={"class": "input", "rows": 3, "placeholder": "ملاحظات اختيارية"}),
        }

    def clean_currencies(self):
        cur = self.cleaned_data.get("currencies")
        if not cur or cur.count() == 0:
            raise forms.ValidationError("لا يمكن إنشاء حاوية بدون عملات. اختر عملة واحدة على الأقل.")
        return cur
    

class FxSettingsForm(forms.ModelForm):
    class Meta:
        model = FxSettings
        fields = ["rate_syp_per_usd"]
        widgets = {
            "rate_syp_per_usd": forms.NumberInput(attrs={
                "class": "input",
                "step": "any",
                "placeholder": "مثال: 20000"
            }),
        }

    def clean_rate_syp_per_usd(self):
        v = self.cleaned_data.get("rate_syp_per_usd")
        if v is None:
            raise forms.ValidationError("أدخل سعر الصرف.")
        if v <= 0:
            raise forms.ValidationError("سعر الصرف يجب أن يكون أكبر من صفر.")
        return v



class OpeningBalanceRowForm(forms.Form):
    currency_code = forms.CharField(widget=forms.HiddenInput())
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


def build_opening_formset(*, currencies: List[Currency], data=None):
    forms_list: List[OpeningBalanceRowForm] = []
    for cur in currencies:
        prefix = f"cur_{cur.code}"
        if data is None:
            f = OpeningBalanceRowForm(
                prefix=prefix,
                initial={"currency_code": cur.code, "code": cur.code, "amount": Decimal("0")},
            )
        else:
            f = OpeningBalanceRowForm(data=data, prefix=prefix)
        forms_list.append(f)
    return forms_list
