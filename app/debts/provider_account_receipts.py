from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256

from django.db import IntegrityError, transaction

from core.formatters import parse_money_strict
from debts import services as DebtSV
from debts.models import (
    ProviderSettlementAction,
    ProviderSettlementActionType,
)
from financials import services as FinSV
from financials.models import PostingLine, PostingTargetType, Receipt


_FEATURE_CODE = "provider_account_settlement"


@dataclass(frozen=True)
class ProviderAccountReceiptResult:
    action_id: int
    receipt_id: int
    receipt_serial: str
    receipt_status: str
    reused: bool
    action_key: str
    currency: str
    amount: Decimal
    cash_amount_signed: Decimal


def _normalize_action(value: str) -> str:
    normalized = (value or "").strip().lower()
    if normalized not in {
        ProviderSettlementActionType.PAY_PROVIDER,
        ProviderSettlementActionType.RECEIVE_FROM_PROVIDER,
    }:
        raise ValueError("invalid action")
    return normalized


def _normalize_currency(value: str) -> str:
    code = (value or "").strip().upper()
    if code not in {"SYP", "USD"}:
        raise ValueError("invalid currency")
    return code


def _receipt_action_key(*, provider_id: int, idempotency_key: str) -> str:
    raw = f"provider-settlement:{int(provider_id)}:{(idempotency_key or '').strip()}"
    digest = sha256(raw.encode("utf-8")).hexdigest()[:32]
    return f"debts:provider-settlement:{provider_id}:{digest}"


def _cash_signed_for_action(*, action: str, amount: Decimal) -> Decimal:
    if action == ProviderSettlementActionType.PAY_PROVIDER:
        return -amount
    if action == ProviderSettlementActionType.RECEIVE_FROM_PROVIDER:
        return amount
    raise ValueError("invalid action")


def _assert_receipt_matches_payload(
    *,
    receipt: Receipt,
    container_id: int,
    currency: str,
    cash_amount_signed: Decimal,
) -> None:
    lines = list(
        PostingLine.objects
        .filter(receipt_id=receipt.id)
        .select_related("currency")
        .order_by("id")
    )
    container_lines = [ln for ln in lines if ln.target_type == PostingTargetType.CONTAINER]
    counterparty_lines = [ln for ln in lines if ln.target_type == PostingTargetType.COUNTERPARTY]
    if len(container_lines) != 1 or len(counterparty_lines) != 1:
        raise ValueError("idempotency key conflict")

    container_line = container_lines[0]
    counterparty_line = counterparty_lines[0]
    if int(container_line.container_id or 0) != int(container_id):
        raise ValueError("idempotency key conflict")
    if (container_line.currency.code or "").upper() != currency:
        raise ValueError("idempotency key conflict")
    if Decimal(container_line.amount or Decimal("0.00")) != cash_amount_signed:
        raise ValueError("idempotency key conflict")
    if Decimal(counterparty_line.amount or Decimal("0.00")) != -cash_amount_signed:
        raise ValueError("idempotency key conflict")


def _validate_payload_matches_action(
    *,
    settlement_action: ProviderSettlementAction,
    action: str,
    currency: str,
    amount: Decimal,
    money_container_id: int,
    idempotency_key: str,
) -> None:
    if (
        settlement_action.action != action
        or (settlement_action.currency or "").upper() != currency
        or Decimal(settlement_action.requested_amount or Decimal("0.00")) != amount
        or int(settlement_action.money_container_id or 0) != int(money_container_id)
        or (settlement_action.idempotency_key or "") != idempotency_key
    ):
        raise ValueError("idempotency key conflict")


@transaction.atomic
def post_provider_account_settlement_receipt(
    *,
    settlement_action_id: int,
    actor,
    action: str | None = None,
    currency: str | None = None,
    amount: Decimal | str | int | None = None,
    money_container_id: int | None = None,
    idempotency_key: str | None = None,
    fx_syp_per_usd: Decimal | None = None,
    note: str = "",
) -> ProviderAccountReceiptResult:
    """
    Post/reuse a grouped financial receipt for one provider settlement action.

    This adapter is intentionally limited to receipt posting/idempotent replay and
    action->receipt linkage. It does not mutate debts or allocations.
    """
    settlement_action = (
        ProviderSettlementAction.objects
        .select_for_update()
        .select_related("provider", "receipt")
        .get(pk=settlement_action_id)
    )

    action_value = _normalize_action(action or settlement_action.action)
    currency_value = _normalize_currency(currency or settlement_action.currency)
    amount_value = parse_money_strict(
        amount if amount is not None else settlement_action.requested_amount,
        field_name="amount",
        error_cls=ValueError,
    )
    if amount_value <= 0:
        raise ValueError("amount must be positive")

    container_id = int(money_container_id or settlement_action.money_container_id or 0)
    if container_id <= 0:
        raise ValueError("invalid money container")
    idem_key = (idempotency_key or settlement_action.idempotency_key or "").strip()
    if not idem_key:
        raise ValueError("idempotency key is required")

    _validate_payload_matches_action(
        settlement_action=settlement_action,
        action=action_value,
        currency=currency_value,
        amount=amount_value,
        money_container_id=container_id,
        idempotency_key=idem_key,
    )

    # Enforce access/feature guard early; reuses existing lock-aware primitive.
    FinSV.require_money_container_for_user(
        user=actor,
        container_id=container_id,
        feature_code=_FEATURE_CODE,
        for_update=True,
    )

    counterparty = DebtSV._ensure_provider_counterparty(provider=settlement_action.provider)
    signed_cash = _cash_signed_for_action(action=action_value, amount=amount_value)
    action_key = _receipt_action_key(
        provider_id=settlement_action.provider_id,
        idempotency_key=idem_key,
    )

    reused = False
    receipt = settlement_action.receipt
    if receipt is None:
        try:
            receipt = FinSV.post_settlement_components_with_fx(
                actor=actor,
                container_id=container_id,
                counterparty_id=counterparty.id,
                cash_by_code_signed={currency_value: signed_cash},
                fx_syp_per_usd=fx_syp_per_usd,
                note=note or f"Provider account settlement {settlement_action.public_id}",
                source_app="debts",
                source_model="ProviderSettlementAction",
                source_id=(settlement_action.public_id or str(settlement_action.id)),
                action_key=action_key,
            )
        except IntegrityError:
            receipt = (
                Receipt.objects
                .select_for_update()
                .filter(action_key=action_key)
                .first()
            )
            if receipt is None:
                raise
            reused = True
    else:
        reused = True

    if settlement_action.receipt_id and settlement_action.receipt_id != receipt.id:
        raise ValueError("idempotency key conflict")

    if (receipt.action_key or "") != action_key:
        raise ValueError("idempotency key conflict")

    _assert_receipt_matches_payload(
        receipt=receipt,
        container_id=container_id,
        currency=currency_value,
        cash_amount_signed=signed_cash,
    )

    if settlement_action.receipt_id is None:
        settlement_action.receipt = receipt
        settlement_action.save(update_fields=["receipt", "updated_at"])

    return ProviderAccountReceiptResult(
        action_id=settlement_action.id,
        receipt_id=receipt.id,
        receipt_serial=receipt.serial or "",
        receipt_status=receipt.status,
        reused=reused,
        action_key=action_key,
        currency=currency_value,
        amount=amount_value,
        cash_amount_signed=signed_cash,
    )
