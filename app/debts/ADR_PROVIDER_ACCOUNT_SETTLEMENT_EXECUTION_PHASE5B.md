# ADR Addendum: Provider Account Settlement Execution Contract (Phase 5B)

- Status: Accepted (contract freeze before implementation)
- Date: 2026-05-14
- Scope: Transactional write-path architecture for provider account settlement execution
- Change type: Documentation + failing tests only (no runtime write behavior changed)

## 1) Final Write-Path Architecture

Future provider account settlement execution will be implemented as one orchestration service:

- `execute_provider_account_settlement(...)`

Supported actions:

- `pay_provider` (targets payable obligations only)
- `receive_from_provider` (targets receivable obligations only)

Execution principles:

- explicit user action only
- deterministic allocation (same rules as dry-run preview)
- currency-isolated in v1 (`SYP` and `USD` independent)
- no auto-cancel / no reconciliation side effects
- full atomicity across accounting write effects

## 2) Frozen Execution Service Contract

Expected future function signature:

```python
execute_provider_account_settlement(
    *,
    provider_id: int,
    action: str,                  # pay_provider | receive_from_provider
    currency: str,                # SYP | USD
    amount: Decimal | str | int,
    money_container_id: int,
    idempotency_key: str,
    preview_fingerprint: str | None = None,
    user=None,
) -> dict
```

Expected future response contract:

```python
{
  "action_id": int,
  "provider_id": int,
  "receipt_id": int,
  "action": str,
  "currency": str,
  "requested_amount": Decimal,
  "eligible_total_remaining": Decimal,
  "total_applied": Decimal,
  "allocation_count": int,
  "idempotency_key": str,
  "preview_fingerprint": str | None,
  "allocations": [...],  # applied allocations only, deterministic order
}
```

## 3) Frozen Transaction Sequence

Required write order:

1. Validate input (provider/action/currency/amount/container/idempotency key).
2. Begin single `transaction.atomic`.
3. Lock provider row (`select_for_update`).
4. Lock or resolve idempotency scope for provider+idempotency key.
5. Lock targeted debt rows in deterministic order.
6. Lock money container row.
7. Recompute allocation snapshot under lock using shared allocator core.
8. If `preview_fingerprint` is provided and does not match locked snapshot, reject.
9. If diagnostics include unresolved identities, reject.
10. Validate execution-only over-amount rule (`amount` must be `<= eligible_total_remaining`).
11. Create one grouped settlement receipt for this action.
12. Apply all per-debt mutations + per-debt settlement/payment rows.
13. Persist action + allocation rows as committed.
14. Persist audit linkage payload.
15. Commit transaction.

Atomicity guarantee:

- receipt creation, debt mutations, per-debt history rows, action header, allocation rows must commit or rollback together.

## 4) Persistence Model Recommendation (Frozen)

Introduce:

- `ProviderSettlementAction` (header)
- `ProviderSettlementAllocation` (per debt application rows)

Canonical accounting truth remains:

- `DebtRecord`/legacy debt rows
- `DebtSettlement` / `DebtorPayment` / `CreditorReceipt`
- `Receipt` and posting lines

Rationale:

- idempotent replay anchor
- one user action trace across many debt rows
- explicit reversal target
- operator/debug visibility

## 5) Grouped Receipt Strategy (Frozen)

Use one grouped receipt per provider account settlement action in v1.

Invariant:

- all applied allocation effects of one action must link to the same receipt.

Rationale:

- clearer cash trace
- simpler reversibility
- cleaner audit and operator view

## 6) Idempotency, Concurrency, and Stale-Preview Rules

Idempotency:

- key scope: `provider_id + idempotency_key`
- same key + same payload => replay existing committed action result (no double mutation)
- same key + different payload => reject conflict

Concurrency:

- lock order must be stable to avoid deadlocks:
  - provider
  - idempotency/action scope
  - targeted debts
  - money container

Stale preview:

- if supplied `preview_fingerprint` does not match locked execution snapshot, reject with no writes.

## 7) Execution Validation Rules (Frozen)

Reject execution for:

- invalid provider/action/currency
- amount <= 0
- money precision > 2dp
- inaccessible/invalid money container
- no eligible debts
- diagnostics unresolved identities present
- `amount > eligible_total_remaining` (execution contract is exact-intent, not over-apply)

Note:

- Preview may report unallocated remainder for over-amount.
- Execution must reject over-amount in v1.

## 8) Rollback Guarantees (Frozen)

On any error during write execution:

- no debt remaining/status mutation persists
- no grouped receipt persists
- no action row commits
- no allocation rows commit
- no orphan per-debt settlement/payment rows persist

## 9) Preview/Execution Consistency (Frozen)

Execution must reuse preview allocation core, not re-implement ordering/dedup logic.

Required:

- shared allocation core logic
- execution-time locked recomputation
- optional fingerprint match enforcement

## 10) Reversal Philosophy (Frozen)

Future reversal must be action-centric:

- reverse grouped receipt using financial reversal primitive
- persist explicit reversal action metadata linked to original action
- unwind per-debt effects in one atomic transaction
- keep original + reversal history (no destructive deletion)

## 11) Explicit Non-Negotiable Rule

Execution MUST reject when allocation/provider diagnostics include unresolved identities.

No silent write execution is allowed in unresolved mixed-source identity states.

## 12) Known Remaining Risks (Pre-Implementation)

- provider-return coexistence remains mixed central/legacy until migration.
- unresolved identity diagnostics handling must remain strict in write path.
- grouped receipt + per-debt row linkage must be validated with dedicated contract tests before rollout.
