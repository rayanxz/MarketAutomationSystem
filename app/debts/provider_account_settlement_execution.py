from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from typing import Any

from django.db import IntegrityError, transaction

from billing.models import Provider
from core.formatters import parse_money_strict
from debts.models import (
    CreditorDebt,
    CreditorReceipt,
    DebtRecord,
    DebtSettlement,
    DebtStatus,
    DebtorDebt,
    DebtorPayment,
    ProviderSettlementAction,
    ProviderSettlementActionStatus,
    ProviderSettlementActionType,
    ProviderSettlementAllocation,
    ProviderSettlementDebtSource,
)
from debts.provider_account_allocator import simulate_provider_account_allocation
from debts.provider_account_receipts import post_provider_account_settlement_receipt
from financials import services as FinSV


DEC0 = Decimal("0.00")


@dataclass(frozen=True)
class _NormalizedExecutionPayload:
    provider_id: int
    action: str
    currency: str
    amount: Decimal
    money_container_id: int
    idempotency_key: str


def _jsonable(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def _serialize_diagnostics_payload(diagnostics: dict[str, Any]) -> str:
    normalized = _jsonable(diagnostics or {})
    return json.dumps(normalized, ensure_ascii=False, sort_keys=True)


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


def _normalize_payload(
    *,
    provider_id: int,
    action: str,
    currency: str,
    amount,
    money_container_id: int,
    idempotency_key: str,
) -> _NormalizedExecutionPayload:
    try:
        pid = int(provider_id)
    except Exception:
        raise ValueError("invalid provider id")
    if pid <= 0:
        raise ValueError("invalid provider id")

    normalized_action = _normalize_action(action)
    normalized_currency = _normalize_currency(currency)
    normalized_amount = parse_money_strict(amount, field_name="amount", error_cls=ValueError)
    if normalized_amount <= DEC0:
        raise ValueError("amount must be positive")
    try:
        container_id = int(money_container_id)
    except Exception:
        raise ValueError("invalid money container")
    if container_id <= 0:
        raise ValueError("invalid money container")

    idem = (idempotency_key or "").strip()
    if not idem:
        raise ValueError("idempotency key is required")

    return _NormalizedExecutionPayload(
        provider_id=pid,
        action=normalized_action,
        currency=normalized_currency,
        amount=normalized_amount,
        money_container_id=container_id,
        idempotency_key=idem,
    )


def _allocation_fingerprint(snapshot: dict[str, Any]) -> str:
    rows = []
    for row in snapshot.get("allocations", []):
        rows.append(
            {
                "debt_source": row.get("debt_source"),
                "debt_id": int(row.get("debt_id") or 0),
                "direction": row.get("direction"),
                "cause_type": row.get("cause_type"),
                "cause_id": str(row.get("cause_id") or ""),
                "before_remaining": format(Decimal(row.get("before_remaining") or DEC0), "f"),
                "applied": format(Decimal(row.get("applied") or DEC0), "f"),
                "after_remaining": format(Decimal(row.get("after_remaining") or DEC0), "f"),
                "would_close": bool(row.get("would_close")),
            }
        )
    payload = {
        "provider_id": int(snapshot.get("provider_id") or 0),
        "action": str(snapshot.get("action") or ""),
        "currency": str(snapshot.get("currency") or ""),
        "requested_amount": format(Decimal(snapshot.get("requested_amount") or DEC0), "f"),
        "eligible_total_remaining": format(Decimal(snapshot.get("eligible_total_remaining") or DEC0), "f"),
        "rows": rows,
    }
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return sha256(text.encode("utf-8")).hexdigest()


def _action_payload_matches(
    *,
    action_row: ProviderSettlementAction,
    payload: _NormalizedExecutionPayload,
) -> bool:
    return (
        int(action_row.provider_id or 0) == payload.provider_id
        and (action_row.action or "") == payload.action
        and (action_row.currency or "").upper() == payload.currency
        and Decimal(action_row.requested_amount or DEC0) == payload.amount
        and int(action_row.money_container_id or 0) == payload.money_container_id
        and (action_row.idempotency_key or "") == payload.idempotency_key
    )


def _get_or_create_action_locked(
    *,
    provider: Provider,
    payload: _NormalizedExecutionPayload,
    user,
) -> ProviderSettlementAction:
    existing_qs = (
        ProviderSettlementAction.objects
        .select_for_update()
        .filter(provider_id=provider.id, idempotency_key=payload.idempotency_key)
        .order_by("id")
    )
    row = existing_qs.first()
    if row is not None:
        return row

    try:
        return ProviderSettlementAction.objects.create(
            provider=provider,
            action=payload.action,
            currency=payload.currency,
            requested_amount=payload.amount,
            eligible_total_remaining=DEC0,
            total_applied=DEC0,
            unallocated_amount=payload.amount,
            money_container_id=payload.money_container_id,
            idempotency_key=payload.idempotency_key,
            status=ProviderSettlementActionStatus.PENDING,
            created_by=user if getattr(user, "is_authenticated", False) else None,
        )
    except IntegrityError:
        row = existing_qs.first()
        if row is None:
            raise
        return row


def _lock_provider_debts(*, provider_id: int) -> None:
    list(
        DebtRecord.objects.select_for_update()
        .filter(provider_id=provider_id, status=DebtStatus.OPEN)
        .order_by("created_at", "id")
        .values_list("id", flat=True)
    )
    list(
        DebtorDebt.objects.select_for_update()
        .filter(provider_id=provider_id, status=DebtorDebt.Status.OPEN)
        .order_by("created_at", "id")
        .values_list("id", flat=True)
    )
    list(
        CreditorDebt.objects.select_for_update()
        .filter(provider_id=provider_id, status=CreditorDebt.Status.OPEN)
        .order_by("created_at", "id")
        .values_list("id", flat=True)
    )


def _central_status(*, debt: DebtRecord) -> str:
    rem_syp = Decimal(debt.remaining_syp or DEC0)
    rem_usd = Decimal(debt.remaining_usd or DEC0)
    return DebtStatus.CLOSED if rem_syp <= DEC0 and rem_usd <= DEC0 else DebtStatus.OPEN


def _legacy_debtor_status(*, entry: DebtorDebt) -> str:
    return DebtorDebt.Status.CLOSED if Decimal(entry.remaining or DEC0) <= DEC0 else DebtorDebt.Status.OPEN


def _legacy_creditor_status(*, entry: CreditorDebt) -> str:
    return CreditorDebt.Status.CLOSED if Decimal(entry.remaining or DEC0) <= DEC0 else CreditorDebt.Status.OPEN


def _serialize_result(
    *,
    action_row: ProviderSettlementAction,
    allocations: list[ProviderSettlementAllocation],
) -> dict[str, Any]:
    rows = []
    for row in allocations:
        rows.append(
            {
                "debt_source": row.debt_source,
                "debt_id": int(row.debt_id or 0),
                "public_id": (row.debt_public_id or None),
                "direction": row.direction,
                "cause_type": row.cause_type,
                "cause_id": row.source_identity or row.debt_id,
                "before_remaining": Decimal(row.before_remaining or DEC0),
                "applied": Decimal(row.applied or DEC0),
                "after_remaining": Decimal(row.after_remaining or DEC0),
                "would_close": bool(row.would_close),
            }
        )

    return {
        "action_id": action_row.id,
        "provider_id": int(action_row.provider_id or 0),
        "receipt_id": int(action_row.receipt_id or 0),
        "action": action_row.action,
        "currency": (action_row.currency or "").upper(),
        "requested_amount": Decimal(action_row.requested_amount or DEC0),
        "eligible_total_remaining": Decimal(action_row.eligible_total_remaining or DEC0),
        "total_applied": Decimal(action_row.total_applied or DEC0),
        "allocation_count": len(rows),
        "idempotency_key": action_row.idempotency_key,
        "preview_fingerprint": action_row.preview_fingerprint or None,
        "allocations": rows,
    }


@transaction.atomic
def execute_provider_account_settlement(
    *,
    provider_id: int,
    action: str,
    currency: str,
    amount,
    money_container_id: int,
    idempotency_key: str,
    preview_fingerprint: str | None = None,
    user=None,
) -> dict[str, Any]:
    payload = _normalize_payload(
        provider_id=provider_id,
        action=action,
        currency=currency,
        amount=amount,
        money_container_id=money_container_id,
        idempotency_key=idempotency_key,
    )

    # Lock provider first (frozen lock order rule).
    provider = (
        Provider.objects
        .select_for_update()
        .filter(pk=payload.provider_id)
        .first()
    )
    if provider is None:
        raise ValueError("provider not found")

    action_row = _get_or_create_action_locked(provider=provider, payload=payload, user=user)
    if not _action_payload_matches(action_row=action_row, payload=payload):
        raise ValueError("idempotency key conflict")

    committed_statuses = {
        ProviderSettlementActionStatus.COMMITTED,
        ProviderSettlementActionStatus.REVERSED,
    }
    if action_row.status in committed_statuses and action_row.receipt_id:
        allocations = list(
            action_row.allocations.order_by("sequence", "id")
        )
        return _serialize_result(action_row=action_row, allocations=allocations)

    # Targeted debts are locked before allocation snapshot rebuild.
    _lock_provider_debts(provider_id=provider.id)

    snapshot = simulate_provider_account_allocation(
        provider_id=provider.id,
        action=payload.action,
        currency=payload.currency,
        amount=payload.amount,
    )
    diagnostics = dict(snapshot.get("diagnostics") or {})
    unresolved = list(diagnostics.get("unresolved_identities") or [])
    if unresolved:
        raise ValueError("execution blocked: unresolved identities")

    current_fp = _allocation_fingerprint(snapshot)
    expected_fp = (preview_fingerprint or "").strip()
    if expected_fp and expected_fp != current_fp:
        raise ValueError("stale preview fingerprint")

    eligible_total = Decimal(snapshot.get("eligible_total_remaining") or DEC0)
    if eligible_total <= DEC0:
        raise ValueError("action/currency has zero remaining balance")
    if payload.amount > eligible_total:
        raise ValueError("amount exceeds eligible total")

    # Reset any stale pre-commit rows for this idempotency scope.
    action_row.allocations.all().delete()
    action_row.receipt = None
    action_row.status = ProviderSettlementActionStatus.PENDING
    action_row.eligible_total_remaining = eligible_total
    action_row.total_applied = DEC0
    action_row.unallocated_amount = payload.amount
    action_row.preview_fingerprint = expected_fp or current_fp
    action_row.diagnostics = _serialize_diagnostics_payload(diagnostics)
    action_row.save(
        update_fields=[
            "receipt",
            "status",
            "eligible_total_remaining",
            "total_applied",
            "unallocated_amount",
            "preview_fingerprint",
            "diagnostics",
            "updated_at",
        ]
    )

    fx_used = FinSV.get_current_fx_syp_per_usd()
    receipt_result = post_provider_account_settlement_receipt(
        settlement_action_id=action_row.id,
        actor=user,
        action=payload.action,
        currency=payload.currency,
        amount=payload.amount,
        money_container_id=payload.money_container_id,
        idempotency_key=payload.idempotency_key,
        fx_syp_per_usd=fx_used,
        note=f"Provider account settlement {action_row.public_id}",
    )
    action_row.refresh_from_db()

    central_map = {
        int(row.id): row
        for row in (
            DebtRecord.objects
            .select_for_update()
            .filter(provider_id=provider.id, status=DebtStatus.OPEN)
            .order_by("id")
        )
    }
    legacy_debtor_map = {
        int(row.id): row
        for row in (
            DebtorDebt.objects
            .select_for_update()
            .filter(provider_id=provider.id, status=DebtorDebt.Status.OPEN)
            .order_by("id")
        )
    }
    legacy_creditor_map = {
        int(row.id): row
        for row in (
            CreditorDebt.objects
            .select_for_update()
            .filter(provider_id=provider.id, status=CreditorDebt.Status.OPEN)
            .order_by("id")
        )
    }

    actor_username = (getattr(user, "username", "") or "").strip()
    created_allocations: list[ProviderSettlementAllocation] = []
    allocations_rows = list(snapshot.get("allocations") or [])

    for index, row in enumerate(allocations_rows, start=1):
        debt_source = str(row.get("debt_source") or "").strip().lower()
        debt_id = int(row.get("debt_id") or 0)
        applied = Decimal(row.get("applied") or DEC0)
        before_remaining = Decimal(row.get("before_remaining") or DEC0)
        after_remaining = Decimal(row.get("after_remaining") or DEC0)
        direction = str(row.get("direction") or "").strip().lower()

        allocation_payload: dict[str, Any] = {
            "action": action_row,
            "sequence": index,
            "debt_source": debt_source,
            "direction": direction,
            "currency": payload.currency,
            "debt_id": str(debt_id),
            "debt_public_id": str(row.get("public_id") or ""),
            "cause_type": str(row.get("cause_type") or ""),
            "source_identity": str(row.get("cause_id") or ""),
            "before_remaining": before_remaining,
            "applied": applied,
            "after_remaining": after_remaining,
            "would_close": bool(row.get("would_close")),
            "closed_after": bool(row.get("would_close")),
        }

        if debt_source == ProviderSettlementDebtSource.CENTRAL:
            debt = central_map.get(debt_id)
            if debt is None:
                raise ValueError("eligible debt disappeared during execution")
            if payload.currency == "SYP":
                debt.remaining_syp = Decimal(debt.remaining_syp or DEC0) - applied
                if debt.remaining_syp < DEC0:
                    raise ValueError("allocation underflow")
            else:
                debt.remaining_usd = Decimal(debt.remaining_usd or DEC0) - applied
                if debt.remaining_usd < DEC0:
                    raise ValueError("allocation underflow")
            debt.status = _central_status(debt=debt)
            debt.save(update_fields=["remaining_syp", "remaining_usd", "status"])

            settlement = DebtSettlement.objects.create(
                debt=debt,
                actor_username=actor_username,
                payment_syp=(applied if payload.currency == "SYP" else DEC0),
                payment_usd=(applied if payload.currency == "USD" else DEC0),
                applied_syp=(applied if payload.currency == "SYP" else DEC0),
                applied_usd=(applied if payload.currency == "USD" else DEC0),
                fx_syp_per_usd_used=fx_used,
                receipt_id=receipt_result.receipt_id,
                money_container_id=payload.money_container_id,
                note=f"Provider account settlement {action_row.public_id}",
            )
            allocation_payload["central_debt"] = debt
            allocation_payload["debt_settlement"] = settlement

        elif debt_source == ProviderSettlementDebtSource.LEGACY:
            if payload.action == ProviderSettlementActionType.PAY_PROVIDER:
                entry = legacy_debtor_map.get(debt_id)
                if entry is None:
                    raise ValueError("eligible debt disappeared during execution")
                entry.paid_amount = Decimal(entry.paid_amount or DEC0) + applied
                entry.status = _legacy_debtor_status(entry=entry)
                entry.save(update_fields=["paid_amount", "status"])

                payment = DebtorPayment.objects.create(
                    entry=entry,
                    amount=applied,
                    currency_code=payload.currency,
                    receipt_id=receipt_result.receipt_id,
                    money_container_id=payload.money_container_id,
                    fx_syp_per_usd_used=fx_used,
                )
                allocation_payload["legacy_debtor_debt"] = entry
                allocation_payload["debtor_payment"] = payment
            else:
                entry = legacy_creditor_map.get(debt_id)
                if entry is None:
                    raise ValueError("eligible debt disappeared during execution")
                entry.collected = Decimal(entry.collected or DEC0) + applied
                entry.status = _legacy_creditor_status(entry=entry)
                entry.save(update_fields=["collected", "status"])

                receipt_row = CreditorReceipt.objects.create(
                    entry=entry,
                    amount=applied,
                    currency_code=payload.currency,
                    receipt_id=receipt_result.receipt_id,
                    money_container_id=payload.money_container_id,
                    fx_syp_per_usd_used=fx_used,
                )
                allocation_payload["legacy_creditor_debt"] = entry
                allocation_payload["creditor_receipt"] = receipt_row
        else:
            raise ValueError("unsupported debt source")

        created_allocations.append(ProviderSettlementAllocation.objects.create(**allocation_payload))

    total_applied = Decimal(snapshot.get("total_applied") or DEC0)
    unallocated_amount = Decimal(snapshot.get("unallocated_amount") or DEC0)
    action_row.total_applied = total_applied
    action_row.unallocated_amount = unallocated_amount
    action_row.eligible_total_remaining = eligible_total
    action_row.status = ProviderSettlementActionStatus.COMMITTED
    action_row.receipt_id = receipt_result.receipt_id
    action_row.preview_fingerprint = expected_fp or current_fp
    action_row.diagnostics = _serialize_diagnostics_payload(diagnostics)
    action_row.save(
        update_fields=[
            "total_applied",
            "unallocated_amount",
            "eligible_total_remaining",
            "status",
            "receipt",
            "preview_fingerprint",
            "diagnostics",
            "updated_at",
        ]
    )

    return _serialize_result(action_row=action_row, allocations=created_allocations)
