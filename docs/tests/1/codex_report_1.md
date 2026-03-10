Scope And Method
I scanned the repository end-to-end with focus on billing-integrated flows across billing, debts, financials, inventory, stock, pos, catalog, audit_log, accounts, templates/APIs/migrations/tests.
This is analysis/planning only. No files were modified, no migrations generated, no remote git operations were run.

I could not execute Django runtime checks in this environment because Django is not installed in the active shell, so migration/test conclusions are from static code inspection.

1) Full Repository Scan
Key modules inspected and why they matter:

Billing core
app/billing/models.py
Defines Provider, Bill, BillItem, ProviderReturn, ProviderReturnItem and document serial behavior.
app/billing/services.py
Main orchestration for create/delete bills and provider returns, debt sync, financial posting, stock/FIFO effects.
app/billing/views.py, app/billing/urls.py
Manager API/page entry points.
app/billing/migrations/
Critical migration graph and schema evolution risk area.
Debts subsystem
app/debts/models.py
Unmanaged models over billing_* tables (managed=False) with source linking and payment/receipt history.
app/debts/services.py
Debt creation/upsert/payment/collection/reminders and financial coupling.
app/debts/migrations/
Manual SQL migration logic and cross-DB behavior divergence.
Financial ledger
app/financials/models.py
Containers, currencies, counterparties, receipts, posting lines, FX settings.
app/financials/services.py
Posting/reversal primitives and balance mutation logic.
app/financials/manual_events.py, app/financials/views_api.py
Manual money operations and manager API surface.
Inventory/stock/FIFO
app/inventory/models.py, app/inventory/services.py
Canonical movement log + wrappers for purchase/sale/return.
app/stock/models.py, app/stock/services.py, app/stock/views.py
FIFO layers, stock cache, transfer flows, consistency repair logic.
POS
app/pos/models.py
Sales bills/rows, shifts/sessions, sales returns.
app/pos/api_bills.py, app/pos/services.py, app/pos/services_returns.py, app/pos/views_api.py, app/pos/api_shifts.py, app/pos/api_sessions.py
Operational POS write paths and permission boundary surface.
Catalog + policy coupling
app/catalog/models.py, app/catalog/services/deletion_policy.py, app/catalog/views.py
Product lifecycle constraints, lock-after-history behavior, identifier uniqueness.
Permissions/accounts/audit
app/accounts/models.py, app/accounts/decorators.py, app/accounts/views.py
Role source-of-truth model and role gate.
app/audit_log/models.py, app/audit_log/services.py, app/audit_log/middleware.py, app/audit_log/signals.py
Audit stream creation and request/session linking.
Project-level config/routing
app/marketpos/settings.py, app/marketpos/settings_prod.py, app/marketpos/urls.py, app/core/middleware.py
Security posture defaults and global auth gating.
Test baseline
billing tests: multicurrency purchase/returns/delete effects.
pos tests: multicurrency sales + return symmetry.
financials tests: manual events/container create/payments.
catalog tests: lifecycle/deletion/history/archival.
Gaps found in migration-graph integrity and POS permission-matrix tests.
2) Flow-Based Bug Hunt
Operational flow mapping and where risks appear:

Purchase bill creation
billing/views.api_bill_save -> billing/services.create_bill -> inventory/services.record_purchase_item -> stock/services.fifo_add_incoming + apply_movement -> debts/services.create_debtor_entry -> financials/services.post_counterparty_adjust_with_fx + post_settlement_with_fx -> audit log.
Main risks: H2, H3, H5, H8, M2.

Purchase bill payment
billing/views.pay_debt_* -> billing/services.pay_* -> debts/services.pay_debt -> financials/services.post_settlement_with_fx.
Main risks: H3, H8, M6.

Purchase bill deletion/reversal
billing/views.api_bill_delete -> billing/services.delete_bill -> FIFO scoped consume + reversal movement + financial receipt reversal + debt/payment delete.
Main risks: H3, H5, L4.

Provider return creation
billing/views.bill_return_wizard -> billing/services.create_return -> inventory/services.record_provider_return_item -> FIFO consume scoped -> creditor entries + financials posting.
Main risks: M3, H2, H3.

Provider return collection/deletion
billing/views.collect_return_* + billing/services.collect_* and delete_return.
Main risks: L1, H3, H8.

POS sale completion
pos/api_bills.api_bill_save -> pos/services.finalize_pos_bill -> inventory/services.record_sale_item -> stock/services.fifo_consume_with_parts -> financial receipt/debt sync.
Main risks: H4, H3, M6.

POS return
pos/returns_views -> pos/services_returns.create_sales_return_draft/post_sales_return -> FIFO in + movement + debt reduce/refund/credit entries.
Main risks: M5, H3, H2.

Debt create/pay/collect
debts/views -> debts/services.create_manual_debt/pay_debt/collect_debt -> financial settlements.
Main risks: H5, H8, H3, M6.

Stock transfer
stock/views.stock_move -> stock/services.transfer_from_batch (FIFO decrement source + FIFO incoming destination + dual movement logs).
Main risks: mostly stable; monitored by L4 style complexity risk.

Product archive/delete/reactivate
catalog/views -> catalog/services/deletion_policy -> history lock and stock checks -> identifier active flag sync.
Main risks: mostly stable; permission/decorator drift risk via M6/L3.

Audit logging critical ops
Mixed direct log_* and transaction.on_commit, with many except Exception: pass fallback paths.
Main risks: M8, L1.

Money container updates
financials/services and financials/manual_events; exchange path has extra weakness.
Main risks: H3, H6, M4.

3) Full Bug Inventory
A) HIGH-RISK BUGS
H1. Broken Billing Migration Chain (non-discoverable dependency)
Title: Broken billing migration graph via invalid migration filename.
Severity: High.
Area: Billing migrations / deployment reliability.
Location: app/billing/migrations/0001_providerreturn_source_bill_serial.py:9, app/billing/migrations/00xx_move_debts_to_debts_app.py:1.
Problem Description: A normal migration depends on 00xx_move_debts_to_debts_app, whose filename does not follow Django migration filename conventions.
Why It Is Dangerous: Fresh installs/recovery migrations can fail before system startup.
Trigger Scenario: New environment runs migrate from zero state.
Expected Correct Behavior: Every referenced migration should be discoverable and ordered by a valid migration name.
Probable Root Cause: Manual transitional migration inserted with ad-hoc naming during debts split.
Recommended Fix Direction: Rename/reissue transition migration with valid numeric prefix and update dependencies with a clean merge path.
Dependencies / Related Issues: Blocks H5, H8, M9.
Test Requirements: Integration: clean-db migrate test; regression: migration plan smoke in CI; restore test from production dump.
H2. Counterparty Identity Collision On (type, name)
Title: Counterparty uniqueness by name causes entity collisions/blocking.
Severity: High.
Area: Financial counterparty identity / billing-pos-debts integration.
Location: app/financials/models.py:190, app/pos/services.py:40, app/debts/services.py:41, app/billing/services.py:106.
Problem Description: Counterparty is unique on (type, name), while business entities are actually keyed by provider/customer IDs.
Why It Is Dangerous: Valid operations fail or incorrectly map financial exposure when different entities share the same name.
Trigger Scenario: Two customers with identical names, then partial POS debt flow requiring counterparty creation.
Expected Correct Behavior: Counterparty uniqueness should be tied to stable foreign keys (provider/customer), not display name.
Probable Root Cause: Early design treated name as identity before provider/customer link fields matured.
Recommended Fix Direction: Migrate uniqueness to FK-based constraints with safe fallback for non-provider/non-customer counterparties.
Dependencies / Related Issues: Related to M7 and M9; interacts with H5 data normalization.
Test Requirements: Unit: ensure_*_counterparty functions; integration: duplicate-name customer/provider payment flows; regression: existing counterparties backfill mapping.
H3. Monetary Precision Contract Is Inconsistent Across Subsystems
Title: SYP quantization mismatch between ledger and commercial/debt layers.
Severity: High.
Area: Accounting correctness (financials, billing, debts, pos).
Location: app/financials/migrations/0008_seed_currencies.py:7, app/financials/services.py:129, app/debts/services.py:359, app/billing/services.py:48.
Problem Description: Financial posting quantizes by currency decimals (SYP=0), while debts/billing/pos operate at 3 decimal places.
Why It Is Dangerous: Cash balances can diverge from debt/document balances, creating silent accounting mismatch.
Trigger Scenario: Fractional SYP totals from discounts/FX, then settlement posting.
Expected Correct Behavior: One consistent precision rule end-to-end for each currency.
Probable Root Cause: Ledger decimal policy introduced after commercial modules already standardized on q3.
Recommended Fix Direction: Define authoritative minor-unit policy, align all write paths and constraints, backfill/normalize existing values.
Dependencies / Related Issues: Coupled with H8; affects M4 and many regression tests.
Test Requirements: Integration: fractional billing/pos/debt settlement parity; unit: quantization helpers; reconciliation test container balances vs debt rollups.
H4. POS Sensitive APIs Lack Unified Role Enforcement
Title: POS API authorization relies on login/staff heuristics, not unified role policy.
Severity: High.
Area: Security and permission boundaries.
Location: app/pos/api_bills.py:233, app/pos/views_api.py:41, app/pos/api_shifts.py:19, app/pos/api_sessions.py:12.
Problem Description: Core POS write/read APIs are @login_required only and use ad-hoc staff checks.
Why It Is Dangerous: Permission drift can expose operational endpoints to unintended accounts when profile/staff flags diverge.
Trigger Scenario: Account with inconsistent profile/flags accesses POS APIs directly.
Expected Correct Behavior: Centralized role-based checks on all sensitive POS endpoints.
Probable Root Cause: POS app evolved with Django staff model while other apps migrated to AccountProfile roles.
Recommended Fix Direction: Standardize endpoint gates to one policy layer and add ownership checks consistently.
Dependencies / Related Issues: Related to M6, L7.
Test Requirements: Permission/security tests for owner/manager/cashier/no-profile across all POS endpoints; regression for allowed cashier flows.
H5. Debt Source Identity Diverges By Database Vendor
Title: SQLite-only :USD source-id suffix hack creates cross-DB identity drift.
Severity: High.
Area: Debts schema integrity and portability.
Location: app/debts/migrations/0002_add_currency_fields.py:69, app/debts/services.py:121, app/debts/services.py:173.
Problem Description: SQLite keeps legacy uniqueness; runtime mutates source IDs to id:USD while other DBs rely on proper (source, currency) uniqueness.
Why It Is Dangerous: Same business document can have different identity semantics depending on DB backend.
Trigger Scenario: Multi-currency debt creation on SQLite, then data export/migration or cross-environment debugging.
Expected Correct Behavior: Stable source identity independent of DB vendor.
Probable Root Cause: Transitional workaround for unmanaged legacy billing debt tables.
Recommended Fix Direction: Canonical source-key model and backend-consistent constraints, with one-time normalization migration.
Dependencies / Related Issues: Depends on H1; related to M9 and L5.
Test Requirements: Cross-DB integration tests (SQLite + PostgreSQL) for multi-currency debt creation/payment/query parity.
H6. Manual Exchange Can Operate On Inactive Containers
Title: Manual FX exchange path bypasses container usability checks.
Severity: High.
Area: Financials manual operations.
Location: app/financials/manual_events.py:144, app/financials/manual_events.py:152, app/financials/services.py:90.
Problem Description: Exchange uses private posting helpers directly and does not call _assert_container_usable.
Why It Is Dangerous: Disabled containers can still be mutated, violating operational controls.
Trigger Scenario: Manager posts exchange where source/target container is inactive but currency state exists.
Expected Correct Behavior: All manual money operations should reject inactive containers.
Probable Root Cause: Exchange implemented separately from public post_* service methods.
Recommended Fix Direction: Route exchange through validated service primitives or replicate full precondition checks.
Dependencies / Related Issues: Related to H4, M4.
Test Requirements: Integration tests for add/withdraw/transfer/exchange parity on inactive containers; regression for active case.
H7. Production-Unsafe Defaults In Primary Settings
Title: Base settings ship with hardcoded dev secret and DEBUG enabled.
Severity: High.
Area: Security/deployment.
Location: app/marketpos/settings.py:13, app/marketpos/settings.py:14.
Problem Description: Primary settings file has static SECRET_KEY and DEBUG=True.
Why It Is Dangerous: Accidental deployment with this module exposes sensitive debugging and predictable secret usage.
Trigger Scenario: Deployment command uses default settings module without explicit production override.
Expected Correct Behavior: Secrets from environment and debug disabled by default in deployable config.
Probable Root Cause: Development defaults remained in base configuration.
Recommended Fix Direction: Environment-driven base settings with fail-fast checks for unsafe production startup.
Dependencies / Related Issues: Related to H4 security posture.
Test Requirements: Config tests asserting production settings require non-dev secret and DEBUG=False; deploy smoke check.
H8. Missing DB-Level Debt Arithmetic Invariants
Title: Debt tables lack strict constraints for amount consistency.
Severity: High.
Area: Data integrity (debts/billing tables).
Location: app/debts/models.py:33, app/debts/models.py:98, app/debts/models.py:46.
Problem Description: No DB checks enforce non-negative totals and paid_amount<=total / collected<=total on unmanaged debt tables.
Why It Is Dangerous: Any buggy path/manual change can silently corrupt debt state and downstream status logic.
Trigger Scenario: Partial update or failed custom script writes invalid debt amounts.
Expected Correct Behavior: Database rejects invalid debt arithmetic.
Probable Root Cause: Legacy unmanaged-table transition prioritized compatibility over strict constraints.
Recommended Fix Direction: Add explicit check constraints on underlying billing_* tables and align service validations.
Dependencies / Related Issues: Depends on H1/H5 migration cleanup; linked to H3 precision policy.
Test Requirements: Migration-level constraint tests and service-level invalid write rejection tests.
B) MEDIUM-RISK BUGS
M1. Billing Status Filters Applied After Pagination
Title: Status filter is applied post-slice causing missing records/cursor drift.
Severity: Medium.
Area: Billing list APIs.
Location: app/billing/views.py:624, app/billing/views.py:664, app/billing/views.py:1489.
Problem Description: Query is paginated first, then Python status filtering is applied on truncated set.
Why It Is Dangerous: Users see incomplete/incorrect lists and can miss documents.
Trigger Scenario: Many bills exist; filter by paid/unpaid/partial.
Expected Correct Behavior: Status filtering should be applied before effective pagination.
Probable Root Cause: Status became computed property; filter moved to Python as a shortcut.
Recommended Fix Direction: Query-level status annotation or iterative pagination window until page is filled correctly.
Dependencies / Related Issues: Related to L4 (monolithic list logic).
Test Requirements: Integration pagination tests with mixed statuses and cursor progression.
M2. TypeError Compatibility Fallback Can Mask Real Failures
Title: API bill save retries legacy call on any TypeError.
Severity: Medium.
Area: Billing API robustness.
Location: app/billing/views.py:560, app/billing/views.py:574.
Problem Description: Any internal TypeError in create_bill triggers fallback to alternate call shape.
Why It Is Dangerous: Real defects can be misrouted and become harder to detect/diagnose.
Trigger Scenario: New bug in create_bill raises TypeError.
Expected Correct Behavior: Only genuine signature mismatch should trigger compatibility path.
Probable Root Cause: Temporary backward-compat bridge left broad.
Recommended Fix Direction: Remove fallback or narrow to explicit signature inspection at startup.
Dependencies / Related Issues: Related to L4.
Test Requirements: Unit test forcing internal TypeError to ensure it surfaces rather than fallback.
M3. Provider Return Split Containers Not Validated As Active
Title: Return split container resolution ignores is_active.
Severity: Medium.
Area: Billing provider return validation.
Location: app/billing/services.py:1025.
Problem Description: Split container lookup filters by code only, not active status.
Why It Is Dangerous: Returns can be posted against deactivated stock locations.
Trigger Scenario: Deactivated wh1 still referenced in return split payload.
Expected Correct Behavior: Only active containers should be accepted.
Probable Root Cause: Validation focused on existence only.
Recommended Fix Direction: Enforce is_active=True and explicit rejection message.
Dependencies / Related Issues: Related to H6.
Test Requirements: Integration test posting split return to inactive container should fail.
M4. Manual Event Exchange Ignores User-Provided FX Rate
Title: Exchange FX parsing uses wrong helper and drops supplied FX.
Severity: Medium.
Area: Financials manager API.
Location: app/financials/views_api.py:87.
Problem Description: q3(fx_raw) is called with string, causing exception and fallback to None.
Why It Is Dangerous: Operator-entered FX may be silently ignored; wrong valuation posted.
Trigger Scenario: Manager submits exchange with explicit custom FX.
Expected Correct Behavior: Explicit FX should parse and be used when valid.
Probable Root Cause: Reused quantizer helper on raw string input.
Recommended Fix Direction: Parse Decimal explicitly before quantization.
Dependencies / Related Issues: Related to H3 and H6.
Test Requirements: API integration tests for exchange with explicit FX vs default FX.
M5. POS Return Error Handling Flattens Exception Semantics
Title: Return posting wraps all exceptions into ValueError debug string.
Severity: Medium.
Area: POS returns reliability/operational safety.
Location: app/pos/services_returns.py:551, app/pos/returns_views.py:235.
Problem Description: Typed exceptions become one generic ValueError with internal state details in message.
Why It Is Dangerous: Error handling loses precision and may leak internal context to UI.
Trigger Scenario: Any exception inside return posting path.
Expected Correct Behavior: Domain exceptions preserved internally; user-facing messages sanitized.
Probable Root Cause: Debug instrumentation left in business path.
Recommended Fix Direction: Use structured exceptions and separate logging from user output.
Dependencies / Related Issues: Related to M8.
Test Requirements: Unit/integration tests asserting rollback + safe error surface + distinct exception categories.
M6. Permission Logic Is Split Between AccountProfile And is_staff
Title: Mixed permission systems create role-policy drift.
Severity: Medium.
Area: Cross-app authorization consistency.
Location: app/accounts/decorators.py:15, app/catalog/views.py:349, app/pos/views.py:207, app/debts/services.py:36.
Problem Description: Some modules use profile roles, others use staff/superuser checks.
Why It Is Dangerous: Small account state mismatches can create unexpected allow/deny behavior.
Trigger Scenario: User has role/capability mismatch versus is_staff.
Expected Correct Behavior: One consistent authorization policy source.
Probable Root Cause: Incremental migration from staff flags to role profiles.
Recommended Fix Direction: Standardize permission checks and centralize role policy utilities.
Dependencies / Related Issues: Supports H4; related to L3 and L7.
Test Requirements: Permission matrix regression suite across all manager/POS/debts endpoints.
M7. Provider Debt Summary Aggregates Mixed Currencies As One Number
Title: Provider total_debt statistic sums SYP and USD together.
Severity: Medium.
Area: Billing provider list analytics.
Location: app/billing/selectors.py:44.
Problem Description: Outstanding debt across currencies is merged into one decimal without currency context.
Why It Is Dangerous: Misleading operational decisions from non-comparable totals.
Trigger Scenario: Provider has both SYP and USD open debts.
Expected Correct Behavior: Separate per-currency totals or normalized reporting with explicit FX basis.
Probable Root Cause: Legacy single-currency summary logic retained in multicurrency model.
Recommended Fix Direction: Return currency-split debt metrics in selector/serializer/UI.
Dependencies / Related Issues: Related to H3.
Test Requirements: Selector tests for mixed-currency provider debt reporting.
M8. Audit Failures Are Frequently Silenced
Title: Critical paths swallow audit/log exceptions.
Severity: Medium.
Area: Audit reliability/observability.
Location: app/catalog/views.py:386, app/stock/views.py:484, app/billing/views.py:709.
Problem Description: Multiple flows catch broad exceptions and suppress failures without structured fallback logging.
Why It Is Dangerous: Audit trail can become incomplete with no operational alert.
Trigger Scenario: Audit service/storage temporary failure during critical operation.
Expected Correct Behavior: Business flow may continue, but failure must be reliably recorded/observable.
Probable Root Cause: UX-first “never break UI on audit failure” policy without observability counterpart.
Recommended Fix Direction: Keep non-blocking behavior but always emit structured error logs/metrics.
Dependencies / Related Issues: Related to L1 and M5.
Test Requirements: Monkeypatch audit failures and assert fallback logging + operation behavior.
M9. Debts App/Table Ownership Is Structurally Fragile
Title: Unmanaged debts models tied to billing tables increase schema drift risk.
Severity: Medium.
Area: Architecture/schema maintainability.
Location: app/debts/models.py:45, app/debts/models.py:110, app/debts/migrations/0002_add_currency_fields.py:8.
Problem Description: Debts domain logic is split from table ownership; schema changes rely on manual SQL and dual-app coupling.
Why It Is Dangerous: Future migrations are high-risk and easy to desynchronize.
Trigger Scenario: New debt field/constraint added in one app but not mirrored correctly in SQL patching.
Expected Correct Behavior: Single authoritative schema ownership model for debt tables.
Probable Root Cause: Mid-transition architecture not fully completed.
Recommended Fix Direction: Complete migration to dedicated managed debt tables or formalize robust compatibility layer.
Dependencies / Related Issues: Strongly related to H1/H5/H8.
Test Requirements: Migration state validation tests and backward compatibility migration rehearsals.
C) LOW-RISK BUGS
L1. delete_return Audit Logs Often Miss Reversed Receipt IDs
Title: Reversed receipt IDs are computed from a lazy queryset after mutation.
Severity: Low.
Area: Audit metadata correctness.
Location: app/billing/services.py:1517, app/billing/services.py:1529, app/billing/services.py:1570.
Problem Description: fin_qs is iterated for reversal, then reused to build IDs; second evaluation can return empty.
Why It Is Dangerous: Audit details become incomplete, weakening traceability.
Trigger Scenario: Delete provider return with posted receipts.
Expected Correct Behavior: Logged reversed receipt IDs should match actual reversals.
Probable Root Cause: Lazy queryset reuse after status changes.
Recommended Fix Direction: Capture IDs in list before reversal loop and reuse immutable list.
Dependencies / Related Issues: Related to M8.
Test Requirements: Regression test asserting audit meta includes actual reversed IDs.
L2. Widespread Mojibake/Corrupted Text Strings
Title: Corrupted text appears in messages/comments/templates.
Severity: Low.
Area: UX/readability/maintainability.
Location: Example app/billing/views.py:103, app/audit_log/views.py:35, app/stock/models.py:64.
Problem Description: Multiple strings contain encoding artifacts and unreadable Arabic.
Why It Is Dangerous: Confusing operator feedback and harder debugging.
Trigger Scenario: Rendering manager pages/messages or reading logs/comments.
Expected Correct Behavior: Clean UTF-8 readable strings everywhere.
Probable Root Cause: Prior encoding conversion damage.
Recommended Fix Direction: Repository-wide controlled text normalization pass with UTF-8 checks.
Dependencies / Related Issues: None.
Test Requirements: Lint/scan test for mojibake patterns and UTF-8 encoding consistency.
L3. Duplicate role_required Implementations
Title: Authorization decorator is duplicated across modules.
Severity: Low.
Area: Maintainability/permission consistency.
Location: app/accounts/decorators.py:15, app/catalog/views.py:349.
Problem Description: Two separate decorator implementations can diverge over time.
Why It Is Dangerous: Future security fixes may be applied to one path only.
Trigger Scenario: Policy change updates only one decorator.
Expected Correct Behavior: Single shared authorization primitive.
Probable Root Cause: Local copy for reuse convenience.
Recommended Fix Direction: Replace local copy with import from central accounts decorator.
Dependencies / Related Issues: Supports M6/H4 hardening.
Test Requirements: Permission regression tests after consolidation.
L4. Billing Service Functions Are Overly Monolithic
Title: Very large orchestration functions increase regression surface.
Severity: Low.
Area: Maintainability/testability.
Location: app/billing/services.py:157, app/billing/services.py:578, app/billing/services.py:940, app/billing/services.py:1411.
Problem Description: Single functions coordinate stock, debt, financials, and audit side effects.
Why It Is Dangerous: Small edits can unintentionally break distant side effects.
Trigger Scenario: Future patch in one subsection changes transaction behavior elsewhere.
Expected Correct Behavior: Explicit, composable sub-steps with stable interfaces.
Probable Root Cause: Incremental feature growth in-place.
Recommended Fix Direction: Incremental extraction of step functions without behavior changes.
Dependencies / Related Issues: Related to M2 and M8.
Test Requirements: Unit tests per sub-step and end-to-end regression suites.
L5. Legacy Source-ID Compatibility Logic Is Scattered
Title: Repeated legacy_source_id/suffix handling increases drift risk.
Severity: Low.
Area: Technical debt / query correctness.
Location: app/billing/services.py:627, app/billing/services.py:1542, app/debts/services.py:125.
Problem Description: Many flows duplicate source-id normalization logic.
Why It Is Dangerous: Easy to miss one path during fixes, causing inconsistent behavior.
Trigger Scenario: New query added without suffix/legacy fallback.
Expected Correct Behavior: Central canonical source identity resolver.
Probable Root Cause: Long migration transition period.
Recommended Fix Direction: Central helper + eventual deprecation after H5 cleanup.
Dependencies / Related Issues: Depends on H5/M9 resolution strategy.
Test Requirements: Regression tests for all legacy/new source-id permutations.
L6. No Automated Migration-Graph Integrity Test
Title: Migration graph breakage is not continuously guarded in tests.
Severity: Low.
Area: CI reliability.
Location: Test suite gap (no migration-plan smoke test found under app/*/tests).
Problem Description: Broken dependencies can ship undetected until deployment.
Why It Is Dangerous: Recovery/install failures discovered late.
Trigger Scenario: Developer adds malformed migration file/dependency.
Expected Correct Behavior: CI should fail immediately on invalid migration graph.
Probable Root Cause: Focus on behavioral tests, not migration health checks.
Recommended Fix Direction: Add CI step and/or test command for migration plan consistency.
Dependencies / Related Issues: Directly guards H1/H5/M9 classes of failures.
Test Requirements: CI command for migration graph + clean-migrate smoke in temporary DB.
L7. POS Permission Regression Coverage Is Incomplete
Title: POS endpoint access matrix is not comprehensively tested.
Severity: Low.
Area: Test coverage.
Location: Existing POS tests under app/pos/tests focus on flows but not full unauthorized matrix.
Problem Description: Security boundary regressions may go unnoticed.
Why It Is Dangerous: Permission mistakes can survive refactors.
Trigger Scenario: Future endpoint added/changed without role checks.
Expected Correct Behavior: Automated denial/allow checks per role and endpoint class.
Probable Root Cause: Business-flow testing prioritized over auth matrix testing.
Recommended Fix Direction: Add dedicated security test module for POS APIs/pages.
Dependencies / Related Issues: Supports H4/M6.
Test Requirements: Role matrix tests for owner/manager/cashier/no-profile over POS APIs.
4) Deduplication And Normalization
I merged overlapping findings into main issues to avoid duplicates:

Permission-related findings were normalized into H4 (sensitive POS API enforcement) and M6 (global policy drift), instead of listing every endpoint separately.
Debt schema instability was normalized into H5 (source identity divergence), H8 (missing arithmetic constraints), and M9 (unmanaged ownership architecture).
Currency/accounting inconsistencies were centralized in H3; flow-specific symptoms in billing/debts/pos are treated as manifestations.
Audit weakness split into M8 (silent failure behavior) and L1 (specific metadata bug), not repeated per module.
Migration fragility normalized under H1 and L6 (missing guardrail test).
5) Execution Plan
Plan To Fix ALL HIGH-RISK Bugs
Fix H1 first (migration graph recoverability).
Reason: All schema/data repairs depend on a valid migration chain.
Resolve H5 next (canonical debt source identity, DB-vendor consistency).
Reason: Identity model must be stable before enforcing stronger constraints.
Fix H3 (currency precision contract) and then H8 (DB debt arithmetic constraints).
Reason: Constraint formulas and backfills depend on chosen precision policy.
Fix H2 (counterparty identity uniqueness) after data identity stabilization.
Reason: Requires data migration and dedup handling tied to prior schema cleanup.
Fix H4 and H6 together (permission and inactive-container protection).
Reason: Both are operational safety controls in live write paths.
Apply H7 (settings security hardening) in same high-risk cycle with deployment validation.
Reason: Security exposure should not wait for medium/low batches.
Migration/schema notes: H1/H5/H8/H2 likely need migrations and data backfill scripts.
Regression tests to add before/during: full billing-pos-debts-financials integration tests with multicurrency and permission matrix.
Manual verification needed: manager/POS workflows and historical data compatibility.

Plan To Fix ALL MEDIUM-RISK Bugs
M1 first (list correctness/pagination) because it directly affects day-to-day operations.
M4 and M3 next (manual FX correctness and inactive container validation).
M2 next (remove dangerous TypeError fallback ambiguity).
M5 and M8 (error handling + audit observability hardening).
M6 then M7 (policy consistency + reporting correctness).
M9 as structured architecture cleanup after high-risk schema stabilization.
Plan To Fix ALL LOW-RISK Bugs
L1 and L2 first (audit metadata correctness + text clarity).
L3 then L5 (authorization and legacy-id helper consolidation).
L4 (service decomposition) as incremental non-behavioral hardening.
L6 and L7 last as CI/test harness reinforcement gates.
6) Fix Batching Strategy
Batch H1
Included issues: H1.
Why grouped: standalone migration graph unblocker.
Expected risk: Very high.
Retest required: clean migrate from zero, migrate on existing DB snapshot.
Ordering: Must run before all other schema-touching batches.
Batch H2
Included issues: H5, H3, H8.
Why grouped: identity + precision + arithmetic constraints are tightly coupled.
Expected risk: Very high.
Retest required: purchase/create/pay/delete, return/create/collect/delete, POS sale/return symmetry with fractional values.
Ordering: After H1, before permission and reporting batches.
Batch H3
Included issues: H2.
Why grouped: counterparty remapping and uniqueness migration is isolated but data-sensitive.
Expected risk: High.
Retest required: provider/customer duplicate-name scenarios across billing/debts/pos.
Ordering: After H2 because identity and precision assumptions must already be stable.
Batch H4
Included issues: H4, H6, H7.
Why grouped: security and operational safety hardening.
Expected risk: Medium-high.
Retest required: endpoint permission matrix, manual events on inactive containers, deployment startup checks.
Ordering: Parallelizable with medium batches once H1-H3 complete.
Batch M1
Included issues: M1, M7.
Why grouped: list/query/report correctness.
Expected risk: Medium.
Retest required: filtered pagination regression and provider analytics UI.
Batch M2
Included issues: M2, M3, M4.
Why grouped: input/validation correctness in billing/financial APIs.
Expected risk: Medium.
Retest required: API contract tests and wizard/manual-event flows.
Batch M3
Included issues: M5, M6, M8, M9.
Why grouped: policy consistency + error/audit + architecture stabilization.
Expected risk: Medium-high.
Retest required: permission matrix, audit continuity, debt schema compatibility tests.
Batch L1
Included issues: L1, L2.
Why grouped: audit/readability quick wins.
Expected risk: Low.
Retest required: audit card rendering and metadata assertions.
Batch L2
Included issues: L3, L5.
Why grouped: code-path consistency refactors with minimal behavior change.
Expected risk: Low.
Retest required: authorization decorators and source-id query regressions.
Batch L3
Included issues: L4, L6, L7.
Why grouped: maintainability and CI hardening.
Expected risk: Low-medium.
Retest required: full regression suite + CI migration/auth smoke stages.
7) Final Summary
Total high-risk issues found: 8

Total medium-risk issues found: 9

Total low-risk issues found: 7

Top 10 most dangerous issues:

H1 Broken billing migration chain.

H3 Monetary precision inconsistency.

H2 Counterparty identity collision.

H5 Cross-DB debt source identity drift.

H4 POS authorization inconsistency.

H8 Missing debt arithmetic DB constraints.

H6 Manual exchange inactive-container bypass.

H7 Insecure default settings.

M1 Post-pagination status filtering bug.

M4 Manual exchange FX parsing bug.

Best repair order (critical to least critical):

H1.

H5 + H3 + H8.

H2.

H4 + H6 + H7.

M1/M2/M3/M4.

Remaining medium issues.

Low-risk cleanup and CI hardening.

Areas that already look strong/stable:

Transaction usage is widespread in critical service flows.
FIFO consumption/traceability logic is explicit and generally consistent.
Billing and POS have meaningful integration tests for multicurrency and return symmetry.
Catalog lifecycle/history lock logic is comparatively robust.
Areas currently most fragile:

Migration topology and debt schema ownership.
Cross-app monetary precision contract.
Counterparty identity modeling.
POS/security permission consistency.
Legacy compatibility branches (legacy_source_id, suffix parsing, fallback code paths).
No code was modified in this task. No push/remote git operation was performed.