from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Dict, Iterable, Tuple

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db.models.signals import pre_save

from core.formatters import round_money


_MODEL_MONEY_FIELDS: Dict[Tuple[str, str], Tuple[str, ...]] = {
    ("financials", "MoneyContainer"): ("balance_syp", "balance_usd"),
    ("financials", "PostingLine"): ("amount",),
    ("billing", "Bill"): (
        "creation_paid_syp",
        "creation_paid_usd",
        "total",
        "subtotal_syp",
        "subtotal_usd",
        "total_syp",
        "total_usd",
        "grand_total_syp",
        "grand_total_usd",
    ),
    ("billing", "BillItem"): ("cost", "price", "line_total"),
    ("billing", "ProviderReturn"): ("total", "total_syp", "total_usd", "initial_paid"),
    ("billing", "ProviderReturnItem"): ("cost", "line_total"),
    ("pos", "SalesBill"): ("total_amount", "total_syp", "total_usd", "paid_amount"),
    ("pos", "SalesBillRow"): ("unit_price", "unit_cost_at_txn", "disc_amount"),
    ("pos", "SalesReturn"): ("total_syp", "total_usd"),
    ("pos", "SalesReturnRow"): ("unit_price_at_sale", "unit_cost_at_txn", "discount_amount_at_txn", "line_total"),
    ("debts", "DebtRecord"): ("total_syp", "total_usd", "remaining_syp", "remaining_usd"),
    ("debts", "DebtSettlement"): ("payment_syp", "payment_usd", "applied_syp", "applied_usd"),
    ("debts", "DebtorDebt"): ("total", "paid_amount"),
    ("debts", "DebtorPayment"): ("amount",),
    ("debts", "CreditorDebt"): ("total", "collected"),
    ("debts", "CreditorReceipt"): ("amount",),
}


def _to_decimal(raw) -> Decimal:
    try:
        dec = Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError("Invalid monetary value.")
    if not dec.is_finite():
        raise ValidationError("Invalid monetary value.")
    return dec


def _decimal_equal(a, b) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    try:
        return Decimal(str(a)) == Decimal(str(b))
    except (InvalidOperation, TypeError, ValueError):
        return False


def _existing_values(instance, *, fields: Iterable[str]) -> dict[str, object]:
    if instance._state.adding or instance.pk is None:
        return {}
    model = instance.__class__
    row = model.objects.filter(pk=instance.pk).values(*fields).first()
    return row or {}


def _validate_instance_money_precision(instance, *, fields: Tuple[str, ...]) -> None:
    existing = _existing_values(instance, fields=fields)
    errors = {}

    for field_name in fields:
        raw = getattr(instance, field_name, None)
        if raw is None:
            continue
        dec = _to_decimal(raw)
        normalized = round_money(dec)
        if dec == normalized:
            continue
        # Allow untouched legacy rows with >2dp until schema migration hardens storage.
        if existing and field_name in existing and _decimal_equal(existing[field_name], dec):
            continue
        errors[field_name] = f"{field_name} supports at most 2 decimal digits."

    if errors:
        raise ValidationError(errors)


def _money_precision_pre_save(sender, instance, **kwargs):
    fields = getattr(sender, "_money_precision_fields", None)
    if not fields:
        return
    _validate_instance_money_precision(instance, fields=fields)


def register_money_precision_guards() -> None:
    for (app_label, model_name), fields in _MODEL_MONEY_FIELDS.items():
        model = apps.get_model(app_label, model_name)
        if model is None:
            continue
        model._money_precision_fields = fields
        pre_save.connect(
            _money_precision_pre_save,
            sender=model,
            dispatch_uid=f"money_precision_guard:{app_label}.{model_name}",
        )
