from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from django.db import IntegrityError, transaction

from billing.models import Provider
from debts.models import (
    CreditorDebt,
    DebtRecord,
    DebtStatus,
    DebtorDebt,
    ProviderSettlementAction,
    ProviderSettlementActionStatus,
    ProviderSettlementAllocation,
)
from financials import services as FinSV
from financials.models import Receipt, ReceiptStatus


DEC0 = Decimal("0.00")


@dataclass(frozen=True)
class _NormalizedReversalPayload:
    action_id: int
    idempotency_key: str
    reason: str


def _normalize_payload(
    *,
    action_id: int,
    idempotency_key: str,
    reason: str,
) -> _NormalizedReversalPayload:
    try:
        aid = int(action_id)
    except Exception:
        raise ValueError("invalid action id")
    if aid <= 0:
        raise ValueError("invalid action id")

    idem = (idempotency_key or "").strip()
    if not idem:
        raise ValueError("idempotency key is required")

    return _NormalizedReversalPayload(
        action_id=aid,
        idempotency_key=idem,
        reason=(reason or "").strip(),
    )


def _central_status(*, debt: DebtRecord) -> str:
    rem_syp = Decimal(debt.remaining_syp or DEC0)
    rem_usd = Decimal(debt.remaining_usd or DEC0)
    return DebtStatus.CLOSED if rem_syp <= DEC0 and rem_usd <= DEC0 else DebtStatus.OPEN


def _legacy_debtor_status(*, entry: DebtorDebt) -> str:
    return DebtorDebt.Status.CLOSED if Decimal(entry.remaining or DEC0) <= DEC0 else DebtorDebt.Status.OPEN


def _legacy_creditor_status(*, entry: CreditorDebt) -> str:
    return CreditorDebt.Status.CLOSED if Decimal(entry.remaining or DEC0) <= DEC0 else CreditorDebt.Status.OPEN


def _reversal_marker_payload(
    *,
    original_action: ProviderSettlementAction,
    reason: str,
) -> dict[str, Any]:
    return {
        "kind": "provider_account_settlement_reversal",
        "target_action_id": int(original_action.id),
        "reason": reason,
        "original_receipt_id": int(original_action.receipt_id or 0),
    }


def _serialize_marker(marker: dict[str, Any]) -> str:
    return json.dumps(marker, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _parse_marker(row: ProviderSettlementAction) -> dict[str, Any] | None:
    raw = (row.diagnostics or "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except Exception:
        return None
    if isinstance(parsed, dict):
        return parsed
    return None


def _marker_matches(
    *,
    marker: dict[str, Any] | None,
    original_action_id: int,
    reason: str,
) -> bool:
    if not marker:
        return False
    if marker.get("kind") != "provider_account_settlement_reversal":
        return False
    try:
        target_id = int(marker.get("target_action_id") or 0)
    except Exception:
        return False
    return target_id == int(original_action_id) and (marker.get("reason") or "") == reason


def _serialize_result(
    *,
    original_action: ProviderSettlementAction,
    reversal_action: ProviderSettlementAction,
    reversal_receipt: Receipt,
) -> dict[str, Any]:
    return {
        "original_action_id": int(original_action.id),
        "reversal_action_id": int(reversal_action.id),
        "original_receipt_id": int(original_action.receipt_id or 0),
        "reversal_receipt_id": int(reversal_receipt.id),
        "status": "reversed",
        "idempotency_key": reversal_action.idempotency_key,
    }


def _lock_provider_for_action(*, action_id: int) -> Provider:
    ref = (
        ProviderSettlementAction.objects
        .filter(id=action_id)
        .values("provider_id")
        .first()
    )
    if not ref:
        raise ValueError("action not found")
    provider = (
        Provider.objects
        .select_for_update()
        .filter(id=int(ref["provider_id"] or 0))
        .first()
    )
    if provider is None:
        raise ValueError("provider not found")
    return provider


def _create_reversal_action(
    *,
    original_action: ProviderSettlementAction,
    idempotency_key: str,
    marker_json: str,
    user,
) -> ProviderSettlementAction:
    created_by = user if getattr(user, "is_authenticated", False) else None
    requested = Decimal(original_action.total_applied or DEC0)
    try:
        return ProviderSettlementAction.objects.create(
            provider_id=original_action.provider_id,
            action=original_action.action,
            currency=(original_action.currency or "").upper(),
            requested_amount=requested,
            eligible_total_remaining=requested,
            total_applied=requested,
            unallocated_amount=DEC0,
            money_container_id=original_action.money_container_id,
            idempotency_key=idempotency_key,
            status=ProviderSettlementActionStatus.PENDING,
            diagnostics=marker_json,
            created_by=created_by,
        )
    except IntegrityError:
        row = (
            ProviderSettlementAction.objects
            .select_for_update()
            .filter(
                provider_id=original_action.provider_id,
                idempotency_key=idempotency_key,
            )
            .order_by("id")
            .first()
        )
        if row is None:
            raise
        return row


def _lock_allocations(*, original_action_id: int) -> list[ProviderSettlementAllocation]:
    return list(
        ProviderSettlementAllocation.objects
        .select_for_update()
        .filter(action_id=original_action_id)
        .order_by("sequence", "id")
    )


def _lock_affected_debts(*, allocations: list[ProviderSettlementAllocation]) -> tuple[dict[int, DebtRecord], dict[int, DebtorDebt], dict[int, CreditorDebt]]:
    central_ids = sorted({int(row.central_debt_id) for row in allocations if row.central_debt_id})
    legacy_debtor_ids = sorted({int(row.legacy_debtor_debt_id) for row in allocations if row.legacy_debtor_debt_id})
    legacy_creditor_ids = sorted({int(row.legacy_creditor_debt_id) for row in allocations if row.legacy_creditor_debt_id})

    central_map = {
        int(row.id): row
        for row in (
            DebtRecord.objects
            .select_for_update()
            .filter(id__in=central_ids)
            .order_by("id")
        )
    }
    legacy_debtor_map = {
        int(row.id): row
        for row in (
            DebtorDebt.objects
            .select_for_update()
            .filter(id__in=legacy_debtor_ids)
            .order_by("id")
        )
    }
    legacy_creditor_map = {
        int(row.id): row
        for row in (
            CreditorDebt.objects
            .select_for_update()
            .filter(id__in=legacy_creditor_ids)
            .order_by("id")
        )
    }
    return central_map, legacy_debtor_map, legacy_creditor_map


def _lock_original_receipt(*, original_action: ProviderSettlementAction) -> Receipt:
    receipt = (
        Receipt.objects
        .select_for_update()
        .filter(id=original_action.receipt_id)
        .first()
    )
    if receipt is None:
        raise ValueError("original receipt not found")
    return receipt


def _assert_downstream_conflicts(
    *,
    allocations: list[ProviderSettlementAllocation],
    central_map: dict[int, DebtRecord],
    legacy_debtor_map: dict[int, DebtorDebt],
    legacy_creditor_map: dict[int, CreditorDebt],
) -> None:
    for row in allocations:
        expected_after = Decimal(row.after_remaining or DEC0)
        if row.central_debt_id:
            debt = central_map.get(int(row.central_debt_id))
            if debt is None:
                raise ValueError("downstream changes detected")
            if (row.currency or "").upper() == "SYP":
                current = Decimal(debt.remaining_syp or DEC0)
            else:
                current = Decimal(debt.remaining_usd or DEC0)
            if current != expected_after:
                raise ValueError("downstream changes detected")
            continue

        if row.legacy_debtor_debt_id:
            debt = legacy_debtor_map.get(int(row.legacy_debtor_debt_id))
            if debt is None:
                raise ValueError("downstream changes detected")
            if Decimal(debt.remaining or DEC0) != expected_after:
                raise ValueError("downstream changes detected")
            continue

        if row.legacy_creditor_debt_id:
            debt = legacy_creditor_map.get(int(row.legacy_creditor_debt_id))
            if debt is None:
                raise ValueError("downstream changes detected")
            if Decimal(debt.remaining or DEC0) != expected_after:
                raise ValueError("downstream changes detected")
            continue

        raise ValueError("downstream changes detected")


def _restore_debt_effects(
    *,
    allocations: list[ProviderSettlementAllocation],
    central_map: dict[int, DebtRecord],
    legacy_debtor_map: dict[int, DebtorDebt],
    legacy_creditor_map: dict[int, CreditorDebt],
) -> None:
    for row in allocations:
        applied = Decimal(row.applied or DEC0)
        if row.central_debt_id:
            debt = central_map[int(row.central_debt_id)]
            if (row.currency or "").upper() == "SYP":
                debt.remaining_syp = Decimal(debt.remaining_syp or DEC0) + applied
                if debt.remaining_syp > Decimal(debt.total_syp or DEC0):
                    raise ValueError("central debt reversal overflow")
            else:
                debt.remaining_usd = Decimal(debt.remaining_usd or DEC0) + applied
                if debt.remaining_usd > Decimal(debt.total_usd or DEC0):
                    raise ValueError("central debt reversal overflow")
            debt.status = _central_status(debt=debt)
            debt.save(update_fields=["remaining_syp", "remaining_usd", "status"])
            continue

        if row.legacy_debtor_debt_id:
            debt = legacy_debtor_map[int(row.legacy_debtor_debt_id)]
            debt.paid_amount = Decimal(debt.paid_amount or DEC0) - applied
            if debt.paid_amount < DEC0:
                raise ValueError("legacy payable reversal underflow")
            debt.status = _legacy_debtor_status(entry=debt)
            debt.save(update_fields=["paid_amount", "status"])
            continue

        if row.legacy_creditor_debt_id:
            debt = legacy_creditor_map[int(row.legacy_creditor_debt_id)]
            debt.collected = Decimal(debt.collected or DEC0) - applied
            if debt.collected < DEC0:
                raise ValueError("legacy receivable reversal underflow")
            debt.status = _legacy_creditor_status(entry=debt)
            debt.save(update_fields=["collected", "status"])
            continue

        raise ValueError("allocation has no debt link")


@transaction.atomic
def reverse_provider_account_settlement(
    *,
    action_id: int,
    idempotency_key: str,
    user=None,
    reason: str = "",
) -> dict[str, Any]:
    payload = _normalize_payload(
        action_id=action_id,
        idempotency_key=idempotency_key,
        reason=reason,
    )

    # Lock provider first (frozen lock order).
    provider = _lock_provider_for_action(action_id=payload.action_id)

    original_action = (
        ProviderSettlementAction.objects
        .select_for_update()
        .select_related("receipt")
        .filter(id=payload.action_id, provider_id=provider.id)
        .first()
    )
    if original_action is None:
        raise ValueError("action not found")

    marker = _reversal_marker_payload(
        original_action=original_action,
        reason=payload.reason,
    )
    marker_json = _serialize_marker(marker)

    existing_key_row = (
        ProviderSettlementAction.objects
        .select_for_update()
        .filter(provider_id=provider.id, idempotency_key=payload.idempotency_key)
        .order_by("id")
        .first()
    )
    if existing_key_row is not None:
        existing_marker = _parse_marker(existing_key_row)
        if not _marker_matches(
            marker=existing_marker,
            original_action_id=original_action.id,
            reason=payload.reason,
        ):
            raise ValueError("idempotency key conflict")

    if original_action.status == ProviderSettlementActionStatus.REVERSED:
        reversal_id = int(original_action.reversed_by_action_id or 0)
        if reversal_id <= 0:
            raise ValueError("action already reversed")

        reversal_action = (
            ProviderSettlementAction.objects
            .select_for_update()
            .filter(id=reversal_id)
            .first()
        )
        if reversal_action is None:
            raise ValueError("action already reversed")
        if reversal_action.idempotency_key != payload.idempotency_key:
            raise ValueError("action already reversed")
        if reversal_action.receipt_id is None:
            raise ValueError("action already reversed")
        reversal_receipt = (
            Receipt.objects
            .select_for_update()
            .filter(id=reversal_action.receipt_id)
            .first()
        )
        if reversal_receipt is None:
            raise ValueError("action already reversed")
        return _serialize_result(
            original_action=original_action,
            reversal_action=reversal_action,
            reversal_receipt=reversal_receipt,
        )

    if original_action.status != ProviderSettlementActionStatus.COMMITTED:
        raise ValueError("action must be committed")
    if not original_action.receipt_id:
        raise ValueError("action has no grouped receipt")

    allocations = _lock_allocations(original_action_id=original_action.id)
    if not allocations:
        raise ValueError("action has no allocations")

    central_map, legacy_debtor_map, legacy_creditor_map = _lock_affected_debts(allocations=allocations)
    original_receipt = _lock_original_receipt(original_action=original_action)

    _assert_downstream_conflicts(
        allocations=allocations,
        central_map=central_map,
        legacy_debtor_map=legacy_debtor_map,
        legacy_creditor_map=legacy_creditor_map,
    )

    reversal_action: ProviderSettlementAction
    if existing_key_row is not None:
        reversal_action = existing_key_row
    else:
        reversal_action = _create_reversal_action(
            original_action=original_action,
            idempotency_key=payload.idempotency_key,
            marker_json=marker_json,
            user=user,
        )

    # Normalize reused pending marker row payload.
    reversal_action.action = original_action.action
    reversal_action.currency = (original_action.currency or "").upper()
    reversal_action.requested_amount = Decimal(original_action.total_applied or DEC0)
    reversal_action.eligible_total_remaining = Decimal(original_action.total_applied or DEC0)
    reversal_action.total_applied = Decimal(original_action.total_applied or DEC0)
    reversal_action.unallocated_amount = DEC0
    reversal_action.money_container_id = original_action.money_container_id
    reversal_action.status = ProviderSettlementActionStatus.PENDING
    reversal_action.diagnostics = marker_json
    reversal_action.save(
        update_fields=[
            "action",
            "currency",
            "requested_amount",
            "eligible_total_remaining",
            "total_applied",
            "unallocated_amount",
            "money_container",
            "status",
            "diagnostics",
            "updated_at",
        ]
    )

    _restore_debt_effects(
        allocations=allocations,
        central_map=central_map,
        legacy_debtor_map=legacy_debtor_map,
        legacy_creditor_map=legacy_creditor_map,
    )

    reversal_receipt = FinSV.reverse_receipt(
        actor=user,
        receipt_id=original_receipt.id,
        reason_note=payload.reason or f"Reverse provider settlement {original_action.public_id}",
    )
    if reversal_receipt.status != ReceiptStatus.POSTED:
        raise ValueError("reversal receipt not posted")

    reversal_action.receipt_id = reversal_receipt.id
    reversal_action.status = ProviderSettlementActionStatus.COMMITTED
    reversal_action.save(update_fields=["receipt", "status", "updated_at"])

    original_action.status = ProviderSettlementActionStatus.REVERSED
    original_action.reversed_by_action = reversal_action
    original_action.save(update_fields=["status", "reversed_by_action", "updated_at"])

    return _serialize_result(
        original_action=original_action,
        reversal_action=reversal_action,
        reversal_receipt=reversal_receipt,
    )
