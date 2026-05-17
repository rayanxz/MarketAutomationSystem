# Provider Net Balance Stabilization Plan

- Status: Draft for maintenance/stabilization/integration phase
- Date: 2026-05-17
- Scope: Provider Net Balance projection, coexistence-aware obligation collector, allocation preview/execution stack, and system-wide consistency hardening
- Type: Report + documentation only (no runtime/accounting behavior changes)

## Current Baseline (What Exists Today)

Implemented and active:
- Read model collector + projection:
  - `app/debts/provider_position.py`
  - `collect_provider_open_obligations(...)`
  - `get_provider_net_position(...)`
- Read-only APIs:
  - Net position API (`app/debts/views.py`)
  - Allocation preview API (`app/debts/views.py`)
- Settlement execution architecture (feature-flagged internal exposure):
  - Execution orchestrator: `app/debts/provider_account_settlement_execution.py`
  - Grouped receipt adapter: `app/debts/provider_account_receipts.py`
  - Action/allocation persistence models: `ProviderSettlementAction`, `ProviderSettlementAllocation` in `app/debts/models.py`
- Internal test page and manager-only endpoints behind `ENABLE_PROVIDER_ACCOUNT_SETTLEMENT_EXECUTION`.

Critical known caveat carried into stabilization:
- Provider list totals are still produced by a separate selector path (`app/billing/selectors.py:providers_with_stats`) that can drift from coexistence-aware collector semantics.

---

## Phase M1 - Consistency / Totals Unification Audit

Goal:
- Inventory every provider totals/balance display and classify alignment to the frozen coexistence-aware projection contract.

### M1 Inventory and Classification

1) `app/debts/provider_position.py` (`collect_provider_open_obligations`, `get_provider_net_position`)
- Role: canonical read projection for provider account position
- Classification: SAFE
- Reason: coexistence-aware, central-over-legacy precedence, diagnostics for unresolved identities/mismatches, per-currency net formula.

2) `app/debts/views.py` (`api_provider_net_position`, `api_provider_account_allocation_preview`, execution response before/after buckets)
- Role: API exposure of projection and simulation/execution summaries
- Classification: SAFE
- Reason: delegates to projection/allocator; no ad-hoc provider-balance SQL sums.

3) `app/templates/debts/provider_account_settlement_test.html`
- Role: internal test UI summaries (before/after, allocations, diagnostics)
- Classification: SAFE
- Reason: API-driven summaries; no independent accounting math for canonical totals.

4) `app/billing/selectors.py` (`providers_with_stats`)
- Role: providers list debt totals and unpaid counters
- Classification: CRITICAL
- Reason:
  - Uses independent aggregation logic (legacy sums + central sums) instead of coexistence-aware collector.
  - Can double-count in overlap scenarios.
  - Presents payable-only debt totals; does not represent full account net position contract.

5) `app/billing/serializers.py` (`provider_row`)
- Role: serializes provider list totals
- Classification: RISKY
- Reason:
  - Relies on `providers_with_stats` output.
  - Backward-compat `total_debt` field mirrors SYP only and can be misinterpreted as full debt position.

6) `app/templates/billing/providers_list.html`
- Role: manager provider list display (`debt_totals` SYP/USD)
- Classification: RISKY
- Reason: shows selector-computed totals that currently come from non-unified aggregation path.

7) `app/billing/views.py` (`api_provider_delete` open-balance checks)
- Role: deletion safeguard for outstanding obligations
- Classification: SAFE
- Reason: existence checks across legacy + central are conservative; does not compute/account totals.

8) `app/billing/views.py` (`bill_return_wizard` source debt panel)
- Role: source purchase-bill debt visibility and optional settlement amount
- Classification: LIMITED (not full provider position)
- Reason: intentionally source-document scoped, not provider-account scoped. Correct for wizard’s current purpose, but not a provider net-balance consumer.

### M1 Output Summary

SAFE:
- `provider_position` collector/projection and APIs that delegate to it.

RISKY:
- Provider list serialization/display pipeline.
- Compatibility `total_debt` semantics.

CRITICAL:
- `providers_with_stats` independent aggregation and potential double counting under central+legacy coexistence.

---

## Phase M2 - Selector / Service Consistency (Drift Map)

Goal:
- Identify duplicated aggregation logic and exact files where future drift can occur.

### Duplicated or Divergent Logic Locations

1) Provider totals aggregation divergence
- `app/debts/provider_position.py` (coexistence-aware collector)
- vs `app/billing/selectors.py` (`providers_with_stats` custom sums)
- Drift risk: HIGH

2) Provider totals serialization compatibility field
- `app/billing/serializers.py` (`total_debt` mirrors SYP only)
- Drift risk: MEDIUM (consumer confusion + semantic mismatch)

3) Purchase-bill source debt lookup in wizard
- `app/billing/views.py` (`bill_return_wizard` source debt query)
- Drift risk: LOW for source-bill flow, HIGH if reused as provider-account balance proxy

4) Multiple mutation paths feeding the same provider obligations
- Central: `DebtRecord`/`DebtSettlement` via `settle_central_debt`
- Legacy: `DebtorDebt`/`CreditorDebt` via `pay_debt`/`collect_debt`
- Account-level execution: `execute_provider_account_settlement`
- Drift risk: MEDIUM if new mutations are introduced without collector compatibility tests

5) Coexistence source identity and dedup assumptions spread across modules
- `app/debts/provider_position.py`
- `app/debts/source_identity.py`
- `app/debts/services.py` source-resolution helpers
- Drift risk: MEDIUM (contract exists, but identity logic is multi-module)

### M2 Required Stabilization Direction

- Provider-facing totals screens must consume one collector/projection truth (or a strict subset derived from it) rather than independent selector sums.
- Backward-compatible fields must be explicitly labeled non-authoritative where retained.

---

## Phase M3 - Invariant Test Strategy (Design Only)

Goal:
- Freeze end-to-end invariants so provider totals and provider projection cannot diverge silently.

### Invariants to Add (no implementation in this phase)

1) Projection-vs-screen invariant
- For a provider in mixed central/legacy state:
  - payable totals displayed in provider list == payable totals derived from coexistence-aware collector subset policy.
- Prevents selector drift.

2) Lifecycle invariants (after each mutation type)
- After purchase bill create
- After provider return create
- After manual debt create
- After individual debt settlement
- After account settlement execution
- After delete/reverse for purchase bill and provider return
- Assert provider net projection and open-obligation composition remain consistent.

3) Dedup invariants under coexistence
- Central+legacy same obligation counted once
- Legacy-only obligations still counted
- Public-id and numeric-id identity forms equivalent
- Mismatch diagnostics emitted without affecting totals

4) No-auto-cancel invariants
- Net can be zero while opposite-direction open debts remain.
- Opposite direction remains untouched by directional settlement action.

5) Currency isolation invariants
- SYP operations do not mutate USD legs and vice versa.
- No forced conversion in canonical provider position.

6) Reversal invariants
- Reversing source docs restores obligations in a way reflected immediately by projection.
- No orphan action/allocation/receipt linkages after rollback/reversal paths.

### Suggested Test Placement

- `app/debts/tests/` for projection + execution invariants
- `app/billing/tests/` for provider list totals alignment and wizard-related source debt expectations
- Cross-module integration tests where mutation happens in billing and assertion happens in debts projection.

---

## Phase M4 - Manual Operator Test Plan (Human Validation)

Goal:
- Provide deterministic manual scenarios for internal operator verification.

### Manual Scenarios

1) Pure payable provider
- Create purchase bill payable only.
- Verify net is negative and payable bucket only.

2) Pure receivable provider
- Create provider return receivable only.
- Verify net is positive and receivable bucket only.

3) Mixed directions
- One payable + one receivable.
- Verify net = receivable - payable and both debts remain open.

4) Net zero with open debts
- Equal payable and receivable balances.
- Verify net zero but open counts both > 0.

5) Partial individual settlement
- Settle part of one debt.
- Verify remaining reduces only that debt and projection updates.

6) Account allocation preview
- Preview pay/receive flows and confirm oldest-first deterministic ordering.

7) Stale preview rejection
- Generate preview, mutate debt state, execute with old fingerprint.
- Expect rejection before writes.

8) Idempotent replay
- Execute same payload/key twice.
- Expect same action/receipt result, no double mutation.

9) Idempotency conflict
- Reuse key with different payload.
- Expect clean rejection.

10) Dedup coexistence
- Construct central+legacy duplicate and legacy-only obligations.
- Verify one-count for duplicate and inclusion for legacy-only.

11) Diagnostics blocking
- Create unresolved identity condition.
- Verify execution blocked and warning visible.

12) Currency isolation
- Execute SYP action while USD debt exists.
- Verify USD untouched.

13) Over-amount execution reject
- Try amount > eligible remaining.
- Expect rejection (execution), not silent partial apply.

14) Receipt and linkage inspection
- Verify one grouped receipt per account action.
- Verify all per-debt mutation rows link to same grouped receipt.

15) Post-action projection check
- Reload provider net position after execution.
- Verify exact before/after delta for selected direction/currency.

---

## Phase M5 - bill_return_wizard Integration Readiness

Goal:
- Decide if wizard can safely consume provider net-balance/account-settlement architecture now.

### Current State

- `bill_return_wizard` currently supports source purchase-bill debt settlement (`settle_purchase_debt`) and source debt amount constraints.
- It does not run provider-account settlement execution path.
- This is intentionally source-document-scoped today.

### Readiness Verdict for Wizard Integration

Can `bill_return_wizard` safely use provider net balance today?
- Answer: NO (not yet for broad integration).

### Exact Remaining Work Before Safe Wizard Integration

1) Provider totals unification first
- Replace/align provider-list aggregation path (`providers_with_stats`) with coexistence-aware projection policy.

2) PostgreSQL concurrency proof completion
- Execute Phase 5G PostgreSQL concurrency suite in real PG environment and review outcomes.

3) Wizard-to-account-settlement intent contract
- Freeze explicit UX/API contract for when wizard uses source-document settlement vs provider-account settlement.

4) Execution diagnostic UX contract in wizard
- Unresolved identity and dedup warning handling must be explicit before any wizard-triggered execution.

5) Reversal strategy maturity
- Account-settlement reversal behavior should be frozen/implemented before high-volume wizard-triggered writes.

6) End-to-end invariant coverage
- Add lifecycle invariants that include wizard create/delete flows plus provider-account settlement actions.

---

## Final Section

### 1) Current Architecture Maturity
- Score: 7.2 / 10

Reasoning:
- Strong core: projection, dedup diagnostics, allocator, orchestrator, idempotency, rollback patterns are in place.
- Main gap: system-wide totals consumers are not yet fully unified to the same collector contract.
- Operational readiness still gated by PostgreSQL concurrency verification execution.

### 2) Biggest Remaining Risk
- Highest risk: divergent provider totals logic (`app/billing/selectors.py`) causing screen/API totals drift from canonical coexistence-aware collector, especially under central+legacy overlap.

### 3) Recommended Execution Order

1. M1: Finish totals inventory with explicit SAFE/RISKY/CRITICAL ownership list.
2. M2: Unify provider totals selectors/services to one collector-derived policy.
3. M3: Land invariant tests across create/settle/delete/reverse/coexistence paths.
4. Run PostgreSQL concurrency suite and close Phase 5G verification gap.
5. M4: Execute internal manual operator test script on flagged environment.
6. M5: Only then design/enable wizard integration behind internal feature flag.

---

## Stabilization Exit Criteria (Recommended)

The subsystem is ready for broader integration when all are true:
- Provider totals screens no longer use divergent aggregation logic.
- Invariant tests pass for all mutation paths (including coexistence and reversals).
- PostgreSQL concurrency suite is executed and passing.
- Diagnostics and unresolved identity blocks are validated in operator flows.
- Wizard integration contract is frozen and implemented behind controlled rollout.
