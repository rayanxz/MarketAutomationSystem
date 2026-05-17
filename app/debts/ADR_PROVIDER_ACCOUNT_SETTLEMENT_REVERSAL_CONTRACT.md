# ADR: Provider Account Settlement Reversal Contract (Phase M5B)

- Status: Accepted (contract freeze before reversal implementation)
- Date: 2026-05-17
- Scope: Reversal architecture for committed provider account settlement actions
- Change type: Documentation + failing contract tests only (no runtime behavior changes)

## 1) Reversal Model (Frozen)

Reversal is action-centric and must never be a raw receipt-only operation.

Rules:

1. Reversal target is one committed `ProviderSettlementAction`.
2. Reversal must run in one atomic transaction.
3. Reversal must unwind debt effects and grouped receipt together.
4. Original action/allocation history is immutable and preserved.
5. Original action is marked reversed and linked to reversal marker/action.
6. Reversal must be idempotent.
7. Reversal must block on downstream conflicts.

## 2) Future Service Contract (Frozen)

Expected service:

```python
reverse_provider_account_settlement(
    *,
    action_id: int,
    idempotency_key: str,
    user=None,
    reason: str = "",
) -> dict
```

Expected behavior:

- validates target action exists and is committed
- rejects if already reversed (unless safe idempotent replay of same reversal key)
- replays safely for same reversal idempotency key and same payload
- rejects idempotency key conflict for different payload/target

Expected response contract (minimum):

```python
{
  "original_action_id": int,
  "reversal_action_id": int,
  "original_receipt_id": int,
  "reversal_receipt_id": int,
  "status": "reversed",
  "idempotency_key": str,
}
```

## 3) Trace Policy Decision (Frozen)

Selected policy: **Option A (minimal, no schema redesign)**.

- Keep existing forward `ProviderSettlementAllocation` rows as immutable execution trace.
- Represent reversal via action status/linkage + reversal receipt linkage.
- Do not create signed inverse allocation rows in v1.

Tradeoffs:

- Pros: no schema redesign, lower risk, simpler rollout.
- Cons: reverse per-row math is computed from original allocations at runtime rather than persisted as inverse rows.

## 4) Downstream Conflict Policy (Frozen)

Conservative v1 rule:

- For each original allocation row, reversal requires:
  - current remaining on target debt equals original `allocation.after_remaining`
- If any mismatch exists, reversal rejects with:
  - `"downstream changes detected"`

Reason:

- prevents unsafe unwind when later actions touched same obligations
- preserves deterministic reversibility

## 5) Atomic Reversal Sequence (Frozen)

Inside one `transaction.atomic`:

1. Lock provider row.
2. Lock original action row.
3. Lock reversal idempotency scope.
4. Lock original allocation rows in deterministic order.
5. Lock targeted debt rows in deterministic order.
6. Validate downstream conflict policy.
7. Restore debt effects from original allocations (reverse debt mutations).
8. Reverse grouped receipt using `financials.services.reverse_receipt(...)`.
9. Persist original action status/link (`reversed`, `reversed_by_action`).
10. Persist reversal marker/action metadata.
11. Commit.

Rollback guarantee:

- if any step fails, no partial debt restore and no partial receipt reversal persists.

## 6) Explicit Safety Rule

Raw `reverse_receipt(...)` is not a valid provider-settlement reversal path by itself.

Reason:

- receipt-only reversal would unwind money postings but leave debt balances unchanged.

Provider-settlement reversal must use dedicated orchestrator that unwinds both:

- financial postings
- debt settlement effects

## 7) Non-Deletion Rule

Reversal must not hard-delete:

- original `ProviderSettlementAction`
- original `ProviderSettlementAllocation`
- original `DebtSettlement` / `DebtorPayment` / `CreditorReceipt` history rows

History must remain auditable with reversal linkage.

