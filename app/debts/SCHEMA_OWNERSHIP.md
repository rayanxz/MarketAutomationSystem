# Debts Schema Ownership

## Current contract
- The `debts` app owns business logic (`services.py`, `views.py`, selectors, serializers).
- Debt persistence uses `managed = False` models that map to `billing_*` tables:
  - `billing_debtorentry`
  - `billing_debtorpayment`
  - `billing_creditorentry`
  - `billing_creditorreceipt`
- The `billing` app remains the historical table owner for these tables.

## Migration policy
- Any schema change to `billing_*` debt tables must be applied from `debts/migrations`.
- Migrations must be DB-vendor aware, with explicit SQLite handling for table rebuilds.
- Source identity is canonicalized as:
  - `(source_app, source_model, source_id, currency_code)`
  - legacy suffixed identifiers (example: `123:USD`) are retained only in `legacy_source_id`.

## Guardrails
- Do not set `managed = True` on debt models until table ownership is formally moved.
- Keep compatibility queries that check both `source_id` and `legacy_source_id` during transition.
- New writes must use canonical source identity (no runtime source-id mutation by DB vendor).
