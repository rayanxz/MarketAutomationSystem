from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from billing.models import Provider
from core.formatters import parse_money_strict
from debts.models import DebtDirection
from debts.provider_position import SUPPORTED_CURRENCIES, collect_provider_open_obligations


ACTION_PAY_PROVIDER = "pay_provider"
ACTION_RECEIVE_FROM_PROVIDER = "receive_from_provider"
ALLOWED_ACTIONS = (ACTION_PAY_PROVIDER, ACTION_RECEIVE_FROM_PROVIDER)
DEC0 = Decimal("0.00")
MIN_AWARE_DATETIME = datetime.min.replace(tzinfo=timezone.utc)


def _safe_decimal(raw) -> Decimal:
    if raw is None:
        return DEC0
    if isinstance(raw, Decimal):
        return raw
    return Decimal(str(raw))


def _parse_amount(amount) -> Decimal:
    dec = parse_money_strict(amount, field_name="amount", error_cls=ValueError)
    if dec <= DEC0:
        raise ValueError("amount must be positive")
    return dec


def _target_direction_for_action(*, action: str) -> str:
    normalized = (action or "").strip().lower()
    if normalized == ACTION_PAY_PROVIDER:
        return DebtDirection.PAYABLE
    if normalized == ACTION_RECEIVE_FROM_PROVIDER:
        return DebtDirection.RECEIVABLE
    raise ValueError("invalid action")


def _normalize_currency(*, currency: str) -> str:
    code = (currency or "").strip().upper()
    if code not in SUPPORTED_CURRENCIES:
        raise ValueError("invalid currency")
    return code


def _remaining_for_currency(*, obligation: dict[str, Any], currency: str) -> Decimal:
    if currency == "SYP":
        return _safe_decimal(obligation.get("remaining_syp"))
    if currency == "USD":
        return _safe_decimal(obligation.get("remaining_usd"))
    return DEC0


def _remaining_other_currency(*, obligation: dict[str, Any], currency: str) -> Decimal:
    if currency == "SYP":
        return _safe_decimal(obligation.get("remaining_usd"))
    if currency == "USD":
        return _safe_decimal(obligation.get("remaining_syp"))
    return DEC0


def _sort_key(obligation: dict[str, Any]) -> tuple:
    created_at = obligation.get("created_at") or MIN_AWARE_DATETIME
    source_rank = 0 if (obligation.get("debt_source") == "central") else 1
    return (
        created_at,
        int(obligation.get("debt_id") or 0),
        source_rank,
    )


def simulate_provider_account_allocation(
    *,
    provider_id: int,
    action: str,
    currency: str,
    amount,
) -> dict[str, Any]:
    """
    Read-only dry-run allocation simulator for provider account settlements.

    This function never mutates debts, settlements, receipts, or audits.
    """
    try:
        pid = int(provider_id)
    except Exception:
        raise ValueError("invalid provider id")
    if pid <= 0:
        raise ValueError("invalid provider id")

    if not Provider.objects.filter(id=pid).exists():
        raise ValueError("provider not found")

    target_direction = _target_direction_for_action(action=action)
    selected_currency = _normalize_currency(currency=currency)
    requested_amount = _parse_amount(amount)

    collected = collect_provider_open_obligations(provider_id=pid)
    obligations = list(collected["obligations"])
    diagnostics = dict(collected["diagnostics"])

    direction_obligations = [
        row
        for row in obligations
        if (row.get("direction") or "") == target_direction
    ]
    if not direction_obligations:
        raise ValueError("no eligible debts")

    eligible = [
        row
        for row in direction_obligations
        if _remaining_for_currency(obligation=row, currency=selected_currency) > DEC0
    ]
    if not eligible:
        raise ValueError("action/currency has zero remaining balance")

    ordered = sorted(eligible, key=_sort_key)
    eligible_total_remaining = sum(
        (_remaining_for_currency(obligation=row, currency=selected_currency) for row in ordered),
        DEC0,
    )
    allocatable_amount = min(requested_amount, eligible_total_remaining)

    allocations: list[dict[str, Any]] = []
    remaining_to_apply = requested_amount
    for row in ordered:
        if remaining_to_apply <= DEC0:
            break
        before_remaining = _remaining_for_currency(obligation=row, currency=selected_currency)
        if before_remaining <= DEC0:
            continue
        applied = min(before_remaining, remaining_to_apply)
        after_remaining = before_remaining - applied
        other_remaining = _remaining_other_currency(obligation=row, currency=selected_currency)
        would_close = (after_remaining <= DEC0) and (other_remaining <= DEC0)

        allocations.append(
            {
                "debt_source": row.get("debt_source"),
                "debt_id": int(row.get("debt_id") or 0),
                "public_id": row.get("public_id"),
                "direction": row.get("direction"),
                "cause_type": row.get("cause_type"),
                "cause_id": row.get("cause_id"),
                "created_at": row.get("created_at"),
                "before_remaining": before_remaining,
                "applied": applied,
                "after_remaining": after_remaining,
                "would_close": bool(would_close),
            }
        )
        remaining_to_apply -= applied

    total_applied = requested_amount - remaining_to_apply
    unallocated_amount = remaining_to_apply

    return {
        "provider_id": pid,
        "action": (action or "").strip().lower(),
        "currency": selected_currency,
        "requested_amount": requested_amount,
        "allocatable_amount": allocatable_amount,
        "unallocated_amount": unallocated_amount,
        "total_applied": total_applied,
        "eligible_debt_count": len(eligible),
        "allocated_debt_count": len(allocations),
        "eligible_total_remaining": eligible_total_remaining,
        "allocations": allocations,
        "diagnostics": diagnostics,
    }

