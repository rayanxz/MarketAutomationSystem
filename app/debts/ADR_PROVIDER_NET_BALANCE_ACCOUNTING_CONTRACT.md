# ADR: Provider Net Balance + Account-Level Settlement Contract (Phase 1A)

- Status: Accepted (contract freeze for implementation phases)
- Date: 2026-05-14
- Scope: Provider obligations, derived provider position, and future account-level settlement behavior
- Change type: Documentation only (no runtime behavior, schema, or API write-path changes)

## Section 1 - Terminology

- Payable debt: obligation where the store owes money to the provider (`DebtDirection.PAYABLE`).
- Receivable debt: obligation where the provider owes money to the store (`DebtDirection.RECEIVABLE`).
- Canonical debt: the source-of-truth debt obligation record plus its settlement trail. In this codebase today this includes central debt records where present and legacy debt records where central representation does not yet exist.
- Provider net balance: a derived projection for a provider, computed from open obligations. It is not an independent mutable ledger.
- Account settlement: an explicit user action that applies cash settlement against provider obligations at account scope instead of choosing one debt manually.
- Reconciliation: an explicit user action that offsets opposite-direction obligations without implicit automation.
- Allocation: deterministic distribution of one settlement action across multiple eligible debts.
- Settlement action: the high-level user intent (for example: "pay provider account 10,000 SYP").
- Settlement allocation: each per-debt application produced by a settlement action.
- Explicit offsetting: a user-triggered operation to offset payable vs receivable obligations with full audit traceability.

## Section 2 - Current Architecture

### 2.1 Central debt layer

- Models: `DebtRecord`, `DebtSettlement` in [`app/debts/models.py`](/c:/MarketAutomationSystem/app/debts/models.py).
- `DebtRecord` stores:
  - direction (`payable` / `receivable`)
  - cause (`purchase_bill`, `provider_return`, `pos_bill`, `manual`)
  - party linkage (`provider`, `customer`, `other_party_type/id`)
  - totals and remaining by currency leg (`*_syp`, `*_usd`)
  - status (`open` / `closed`)
  - stable public id (`D-###`)
- `DebtSettlement` stores:
  - payment components
  - applied components
  - FX snapshot used
  - linked `Receipt` and `MoneyContainer`
- Central purchase-bill debt settlement write path is in `settle_central_debt` [`app/debts/services.py`](/c:/MarketAutomationSystem/app/debts/services.py).

### 2.2 Legacy debt layer (billing-owned tables, debts-owned behavior)

- Mirror models in debts app mapped to `billing_*` tables with `managed = False`:
  - `DebtorDebt`, `DebtorPayment`, `CreditorDebt`, `CreditorReceipt`
  - See [`app/debts/models.py`](/c:/MarketAutomationSystem/app/debts/models.py), [`app/debts/SCHEMA_OWNERSHIP.md`](/c:/MarketAutomationSystem/app/debts/SCHEMA_OWNERSHIP.md)
- Table ownership is still historically billing-side; business logic is now concentrated in `debts` services.
- Compatibility source identity supports canonical and legacy variants (`source_id`, `legacy_source_id`, historical `:USD` suffix handling).

### 2.3 Provider linkage and coexistence behavior

- Provider is linked in both layers through `provider_id`.
- Purchase bills currently create central payable debt (`DebtCauseType.PURCHASE_BILL`) in billing service.
- Provider returns currently create legacy creditor entries and receipts; no central `DebtRecord` create path is active for provider returns yet.
- Manual debts are created in legacy tables and synchronized into central `DebtRecord` (`DebtCauseType.MANUAL`).

### 2.4 Canonical direction and migration philosophy

- Strategic direction: central debt layer is the long-term canonical debt abstraction.
- Transitional reality: canonical truth is coexistence-aware:
  - use central when a central representation exists for that obligation
  - use legacy when central representation does not yet exist
- Migration philosophy:
  - no silent backfill that mutates accounting meaning
  - centralization by cause type, tested incrementally
  - deterministic precedence rules to prevent double counting

### 2.5 Known coexistence risks

- Double counting risk when aggregating central + legacy without source-level dedup.
- Cause coverage mismatch (for example provider returns represented in legacy but not central).
- Stale comments/assumptions in some modules may still describe older "legacy-first" behavior.

## Section 3 - Provider Net Balance Contract

### 3.1 Formula and sign convention (frozen)

- Formula per currency:
  - `net = receivable - payable`
- Sign meaning:
  - positive net: provider owes store
  - negative net: store owes provider
  - zero net: balanced net position only; does not imply all debts are closed

### 3.2 Derived-only rule (frozen)

- Provider net balance is read-model only.
- It MUST NOT be a writable accounting source.
- It MUST NOT replace debt-level or settlement-level records.
- Canonical truth remains:
  - debt records
  - settlement/payment rows
  - receipts
  - audit logs

### 3.3 Open/closed and partial handling

- Include only open debt remaining amounts.
- Closed debts contribute zero.
- Partial settlements reduce remaining; net projection reflects reduced remaining only.
- If no open debts remain in a currency, net for that currency is zero.

### 3.4 FX behavior for net balance

- Canonical net is per-currency only.
- Any equivalent conversion is informational view data only.
- Informational equivalent MUST include explicit FX source/timestamp and MUST NOT feed canonical settlement math.

### 3.5 Zero-net with open debts

- `net == 0` is allowed while open debts exist in opposite directions.
- UI/API must present both:
  - net projection
  - open payable/receivable detail counts/amounts

## Section 4 - Non-Auto-Cancel Rule

- Opposite-direction debts MUST NOT silently cancel.
- Opposite-direction debts MUST NOT auto-close.
- Opposite-direction debts MUST NOT mutate without explicit user action.

Reasoning:

- Auditability: every state change must map to a user action and durable records.
- Operational traceability: finance staff must see what happened and why.
- User trust: balances cannot change invisibly.
- Accounting clarity: obligation lifecycle stays explicit per document/debt.

## Section 5 - Future Account-Level Settlement Model (design contract only)

- Account-level settlement is explicit user action, not background behavior.
- Allocation policy (default v1):
  - deterministic ordering
  - oldest debt first (`created_at`, then stable tiebreaker `id`)
  - close full debts first
  - partially reduce the next debt when funds exhaust
  - leave later debts untouched
- Allocation scope is direction-specific per action:
  - "pay provider account" targets open payables
  - "receive from provider account" targets open receivables
- No hidden balancing/cancellation outside requested action.
- Every allocation must be linkable from the high-level action to per-debt application rows and resulting receipt(s).

## Section 6 - Currency Policy

- Canonical balances are per currency (`SYP`, `USD`) with no forced conversion.
- Net balance is shown per currency first-class.
- Optional equivalent display is secondary, informational only.
- v1 account-level settlement is currency-isolated:
  - no cross-currency allocation
  - one settlement action applies only to debts in the selected currency lane

Reasoning and risk control:

- Avoids FX drift and hidden conversion effects.
- Keeps user intent explicit.
- Simplifies deterministic allocation and audit interpretation.

## Section 7 - Deduplication Contract (critical)

### 7.1 Unification objective

- Provider obligation views must produce one economic truth without double counting.
- During coexistence, dedup is mandatory at source-obligation identity level.

### 7.2 Precedence contract (frozen for read models)

- For any obligation represented in both layers, central record is preferred.
- Legacy record is used only when no central representation exists for that same obligation identity.

### 7.3 Initial practical dedup assumptions for provider views

- Purchase-bill obligations:
  - prefer central `DebtRecord` (`cause_type=purchase_bill`, payable)
  - treat legacy debtor entries for same source as compatibility mirrors when both exist
- Provider-return obligations:
  - currently sourced from legacy creditor entries until central provider-return debts exist
- Manual debts:
  - when central manual debt exists (synced from legacy), central is preferred for provider position projections

### 7.4 Overlap handling requirements

- Dedup identity must use stable source keys (app/model/source id plus compatible legacy variants where needed).
- If both layers contain non-equivalent amounts for same economic source, system must surface discrepancy in diagnostics/audit tooling; never silently sum both.

### 7.5 Explicit unresolved migration ambiguity

- Provider-return centralization is not yet implemented; therefore provider receivable side is currently legacy-backed.
- Until provider-return central debts exist, provider net balance service must support mixed-source read composition with explicit dedup policy.

## Section 8 - Implementation Safety Rules (mandatory for future phases)

- No write-path changes before read-only projection validation is complete.
- Settlement writes must remain atomic transactions.
- Allocation must be deterministic and stable under retries.
- Concurrency protection is required (`select_for_update` or equivalent locking strategy).
- Idempotency is required for externally repeatable actions.
- Monetary parsing/rounding must use existing strict money/fx quantization rules.
- No auto-cancel logic in any settlement path unless user explicitly invoked reconcile action.
- Tests must land before enabling new write paths in production.
- Rollback strategy must exist per phase (feature flag or guarded endpoint rollout).
- Audit payloads must preserve:
  - initiating action
  - affected debts
  - per-debt applied amounts
  - receipt linkage
  - FX snapshot metadata where relevant

## Section 9 - Open Questions / Blockers

- Reconcile action scope:
  - should explicit offsetting be allowed only same currency in v1, or deferred entirely?
- Settlement header model:
  - do we introduce a first-class `ProviderSettlementAction` header now, or rely on receipt/audit linkage first?
- Receipt grouping strategy:
  - one receipt per account settlement action vs multiple receipts per currency/component.
- Provider-return migration timeline:
  - when to centralize provider-return debts into `DebtRecord` and how to backfill safely.
- Mixed-source discrepancy policy:
  - how to detect/report central vs legacy mismatches in operations UI.
- Caching strategy:
  - on-demand calculation only vs optional materialized snapshot for heavy listing screens.
- Historical FX equivalent policy for display:
  - current FX only vs selectable snapshot FX mode for reporting.

## Contract Stability Decision for Next Phases

- Phase 1B (failing tests for contract assertions): Ready.
- Phase 2 (read-only provider position service): Ready with explicit mixed-source dedup safeguards.

## Contradictions Observed in Current Codebase (must be acknowledged)

- Some compatibility comments still describe legacy debt as the main debt state while purchase bills already create central debt records.
- Provider stats aggregation currently combines legacy and central payable amounts directly; without source-level dedup this can double count.
- `DebtCauseType.PROVIDER_RETURN` exists in central enums/selectors, but provider-return create flow currently persists receivable debt in legacy layer.

## Phase 1A Recommendation

- Proceed with unresolved risks.
- Blocking condition before any write-path expansion:
  - finalize dedup identity implementation spec for mixed central/legacy provider views.
