
# Project Report — MarketAutomationSystem (MarketPOS)

This report maps **UI-visible** features only (pages/templates/routes a user can actually reach), links them to the exact files, and identifies gaps for a simple market automation system.

---

## A) PROJECT MAP (installed apps)

- **accounts** — Auth + role dashboards + staff management.
- **catalog** — Product catalog (collections/sets/products) + import/export.
- **billing** — Purchase bills + providers + provider returns (multi-currency).
- **debts** — Debtor/creditor subledger + reminders + payments/collections.
- **inventory** — Product movement log + inventory movement services.
- **stock** — Stock containers, FIFO layers, stock list, and transfers.
- **financials** — Money containers, receipts, FX settings, manual cash events.
- **pos** — POS sales screen, POS shifts, sales bills, customer profiles.
- **ledger** (legacy) — Volt “safe” ledger UI + journal services (not unified).
- **audit_log** — Audit sessions + owner audit dashboard.
- **notifications** — Manager notifications + reminder generation.
- **core** — Global middleware (login enforcement, shared utilities).
- **printing** — Installed but no UI routes detected.
- **io_ops** — Installed; no UI routes detected.
- **backups_app** — Installed; no UI routes detected.

Primary URL router: `app/marketpos/urls.py`.

---

## B) FEATURE MAP (WORKING + UI‑VISIBLE ONLY)

### Accounts / Auth
1) **Login + role selection + logout**
- **Roles allowed**: Owner / Manager / Cashier
- **UI entry**: Root redirect → `/login/` (login screen); logout button in top bar.
- **Views**: `app/accounts/views.py` (`login_view`, `logout_view`)
- **Templates**: `app/templates/login.html`, `app/templates/base_dash.html`, `app/templates/pos/base_pos.html`
- **JS/CSS**: Base styles `app/static/app.css`, `app/static/rtl.css`; POS styles `app/static/pos.css`
- **Services**: n/a
- **Models**: Django `User`, `accounts.AccountProfile`
- **Data effects**: Session create/clear; audit session creation on login/logout
- **Audit**: **Yes** — `app/audit_log/signals.py` (login/logout events)
- **Flow**:
  - Open `/login/` → select role → submit credentials → redirect to dashboard/POS

2) **First‑time owner setup**
- **Roles allowed**: First owner only
- **UI entry**: `/setup/owner/` (direct)
- **Views**: `app/accounts/views.py` (`owner_setup_view`)
- **Templates**: `app/templates/accounts/owner.html`
- **JS/CSS**: Base styles only
- **Services**: n/a
- **Models**: `accounts.AccountProfile` + Django `User`
- **Data effects**: Creates owner user + AccountProfile
- **Audit**: **No** explicit audit event
- **Flow**:
  - Fill owner form → create owner → redirect to `/login/`

3) **Owner dashboards + Manager/Cashier landing pages**
- **Roles allowed**: Owner / Manager / Cashier
- **UI entry**: Owner menu → dashboard (`/owner/`), Manager menu → dashboard (`/manager/`), Cashier menu → dashboard (`/cashier/`)
- **Views**: `app/accounts/views.py` (`owner_dashboard`, `manager_dashboard`, `cashier_dashboard`)
- **Templates**: `app/templates/owner/owner_dash.html`, `app/templates/manager/manager_dash.html`, `app/templates/cashier/cashier_dash.html`
- **JS/CSS**: Base styles only
- **Data effects**: Read‑only
- **Audit**: **No**

4) **Owner: create staff accounts**
- **Roles allowed**: Owner
- **UI entry**: Owner menu → “Accounts” → `/accounts/`
- **Views**: `app/accounts/views.py` (`manage_accounts`)
- **Templates**: `app/templates/accounts/manage_accounts.html`
- **Services**: n/a
- **Models**: `accounts.AccountProfile`, Django `User`
- **Data effects**: Create new user + profile
- **Audit**: **No**
- **Flow**:
  - Owner re‑auth → enter staff creds + role → create user

5) **Owner: staff list / edit / delete**
- **Roles allowed**: Owner
- **UI entry**: Owner menu → “Accounts” → `/accounts/list/`
- **Views**: `app/accounts/views.py` (`accounts_list`, `staff_edit`, `staff_delete`)
- **Templates**: `app/templates/owner/accounts_list.html`
- **Services**: n/a
- **Models**: `accounts.AccountProfile`, Django `User`
- **Data effects**: Update username/password; delete user
- **Audit**: **No**

6) **Owner: edit own account**
- **Roles allowed**: Owner
- **UI entry**: Owner menu → “Edit Account” → `/owner/edit/`
- **Views**: `app/accounts/views.py` (`owner_edit_account`)
- **Templates**: `app/templates/owner/edit_account.html`
- **Data effects**: Update username/password; forces logout
- **Audit**: **No**

---

### Catalog (Products / Collections)
7) **Collections list + create/rename/delete**
- **Roles allowed**: Manager (Owner allowed)
- **UI entry**: Manager menu → “Products” → `/manager/products/`
- **Views**: `app/catalog/views.py` (`manager_collections`, `collection_rename`, `collection_delete`)
- **Templates**: `app/templates/manager/collections_list.html` (+ `app/templates/manager/_products_tabs.html`)
- **JS/CSS**: `app/static/js/manager_browser.js`, `app/static/js/manager_search.js`
- **Services**: `app/audit_log/services.py` for audit
- **Models**: `catalog.ProductCollection`, `catalog.ProductSet`, `catalog.Product`
- **Data effects**: Create/update/delete collections + sets; delete blocked if products exist
- **Audit**: **Yes** — `audit_log.services.log_create/log_update/log_delete`
- **Flow**:
  - Open collections → add/rename/delete collection → updates tree

8) **Products list + browse/search**
- **Roles allowed**: Manager (Owner allowed)
- **UI entry**: Manager menu → “Products” → `/manager/products/`
- **Views**: `app/catalog/views.py` (`api_collection_products`, `api_product_search`, browser APIs)
- **Templates**: `app/templates/manager/collections_list.html`
- **JS/CSS**: `app/static/js/manager_browser.js`, `app/static/js/manager_search.js`
- **Services**: n/a
- **Models**: `catalog.Product`, `catalog.ProductSet`, `catalog.ProductCollection`, `catalog.ProductBarcode`, `catalog.ProductUnitId`
- **Data effects**: Read‑only
- **Audit**: **No**

9) **Create / edit product**
- **Roles allowed**: Manager (Owner allowed)
- **UI entry**: Products tabs → “New Product” → `/manager/products/new/` or “Edit” link → `/manager/products/edit/<id>/`
- **Views**: `app/catalog/views.py` (`manager_product_new`, `manager_product_edit`)
- **Templates**: `app/templates/manager/product_new.html`
- **JS/CSS**: `app/static/js/product_new.js`
- **Services**: `app/audit_log/services.py` (audit)
- **Models**: `catalog.Product`, `catalog.ProductSet`, `catalog.ProductCollection`, `catalog.ProductBarcode`, `catalog.ProductUnitId`
- **Data effects**: Create/update product, barcodes, unit IDs, currency flags/defaults
- **Audit**: **Yes** — create/update events

10) **Archive (soft delete) product**
- **Roles allowed**: Manager (Owner allowed)
- **UI entry**: Products list → delete action → `/manager/products/delete/<id>/`
- **Views**: `app/catalog/views.py` (`manager_product_delete`)
- **Templates**: `app/templates/manager/collections_list.html`
- **Data effects**: Sets `Product.is_active=False`
- **Audit**: **Yes**

11) **Batch edit (rename/adjust/delete collections/sets)**
- **Roles allowed**: Manager (Owner allowed)
- **UI entry**: Products UI (batch actions) → POST `/manager/products/api/edit/apply/`
- **Views**: `app/catalog/edit_api.py` (`edit_apply_batch`)
- **Templates**: `app/templates/manager/collections_list.html`
- **JS/CSS**: `app/static/js/manager_browser.js`
- **Services**: `app/audit_log/services.py`
- **Models**: `catalog.ProductCollection`, `catalog.ProductSet`, `catalog.Product`
- **Data effects**: Rename sets/collections; bulk price/cost updates; delete sets/collections
- **Audit**: **Yes**

12) **Import products (Excel/ODS wizard)**
- **Roles allowed**: Manager (Owner allowed)
- **UI entry**: Manager menu → “Import Products” → `/manager/products/import/`
- **Views**: `app/catalog/import_views.py` (`import_*`)
- **Templates**: `app/templates/manager/products_import.html`, `app/templates/manager/products_import_report.html`
- **JS/CSS**: `app/static/js/products_import.js`
- **Services**: `app/catalog/import_engine.py`
- **Models**: `catalog.Product*`, `catalog.CatalogDataJob`
- **Data effects**: Bulk create/update products/sets/collections; staging files
- **Audit**: **Partial** — commit logs via `CatalogDataJob` (no explicit per‑row audit)

13) **Export products (Excel/ODS)**
- **Roles allowed**: Manager (Owner allowed)
- **UI entry**: Manager menu → “Export Products” → `/manager/products/export/`
- **Views**: `app/catalog/export_views.py` (`export_start`, `export_prepare`, `export_download`)
- **Templates**: `app/templates/manager/products_export.html`
- **JS/CSS**: `app/static/js/manager_export.js`
- **Services**: `app/catalog/export_engine.py`
- **Models**: `catalog.Product*`, `catalog.CatalogDataJob`
- **Data effects**: Export files generated + job record
- **Audit**: **Yes** — `audit_log.services.log_create` for export job

---

### Stock / Inventory
14) **Stock list (per container, with FIFO batches view)**
- **Roles allowed**: Manager (Owner allowed)
- **UI entry**: Manager menu → “Stock” → `/manager/stock/`
- **Views**: `app/stock/views.py` (`stock_list`)
- **Templates**: `app/templates/stock/stock_list.html`
- **JS/CSS**: `app/static/stock/stock_list.js`
- **Services**: `app/stock/services.py` (FIFO reads)
- **Models**: `stock.StockEntry`, `stock.StockFifoLayer`, `stock.ProductContainer`, `catalog.Product`
- **Data effects**: Read‑only
- **Audit**: **No**

15) **Move stock between containers (batch transfer)**
- **Roles allowed**: Manager (Owner allowed)
- **UI entry**: Stock tabs → “Move” → `/manager/stock/move/`
- **Views**: `app/stock/views.py` (`stock_move`)
- **Templates**: `app/templates/stock/stock_move.html`
- **JS/CSS**: `app/static/stock/stock_move.js`
- **Services**: `app/stock/services.py` (`transfer_from_batch`)
- **Models**: `stock.StockFifoLayer`, `inventory.ProductMovement`, `stock.StockEntry`
- **Data effects**: FIFO layer move; creates OUT/IN adjustment movements; updates stock entry per container
- **Audit**: **Yes** — `audit_log.services.log_event` in `stock_move`

16) **Product movements log**
- **Roles allowed**: Manager (Owner allowed)
- **UI entry**: Stock pages → “Product movements” tab → `/manager/products/movements/`
- **Views**: `app/inventory/views.py` (`manager_product_movements`)
- **Templates**: `app/templates/manager/product_movements.html`
- **JS/CSS**: `app/static/inventory/product_movements.js`, `app/static/inventory/product_movements.css`
- **Services**: n/a
- **Models**: `inventory.ProductMovement`, `billing.Bill/ProviderReturn`, `stock.ProductContainer`
- **Data effects**: Read‑only
- **Audit**: **No**

---

### Billing / Purchases
17) **Purchase bills list**
- **Roles allowed**: Manager (Owner allowed)
- **UI entry**: Billing tabs → “Bills list” → `/manager/billing/list/`
- **Views**: `app/billing/views.py` (`bills_list`, `api_bills_list`)
- **Templates**: `app/templates/billing/bills_list.html`
- **JS/CSS**: `app/static/js/billing_bills.js`
- **Services**: `app/billing/selectors.py`, `app/billing/serializers.py`
- **Models**: `billing.Bill`, `billing.Provider`, `debts.DebtorDebt`
- **Data effects**: Read‑only
- **Audit**: **No**

18) **Create purchase bill (multi‑currency)**
- **Roles allowed**: Manager (Owner allowed)
- **UI entry**: Billing tabs → “Add bill” → `/manager/billing/add/`
- **Views**: `app/billing/views.py` (`add_bill`, `api_bill_save`)
- **Templates**: `app/templates/billing/add_bill.html`
- **JS/CSS**: `app/static/js/billing_add_bill.js`
- **Services**: `app/billing/services.py` (`create_bill`)
- **Models**: `billing.Bill`, `billing.BillItem`, `billing.Provider`, `catalog.Product`,
  `inventory.ProductMovement`, `stock.StockFifoLayer`, `debts.DebtorDebt`, `financials.Receipt`
- **Data effects**: Create bill + items; stock increase (FIFO + movement); create debtor entries; post financial receipts; update money container balances
- **Audit**: **Yes** — `audit_log.services.log_create`

19) **Bill detail + payments (full/partial)**
- **Roles allowed**: Manager (Owner allowed)
- **UI entry**: Bills list → open bill → `/manager/billing/bills/<id>/`
- **Views**: `app/billing/views.py` (`bill_view`, `pay_debt_full`, `pay_debt_batch`)
- **Templates**: `app/templates/billing/bill_view.html`
- **JS/CSS**: `app/static/js/billing_bill_view.js`
- **Services**: `app/billing/services.py` (`pay_full`, `pay_partial`)
- **Models**: `billing.Bill`, `debts.DebtorDebt`, `debts.DebtorPayment`, `financials.Receipt`
- **Data effects**: Debtor payments; receipts; money container balances
- **Audit**: **Yes** — `log_update` on payment

20) **Providers list + create/delete**
- **Roles allowed**: Manager (Owner allowed)
- **UI entry**: Billing tabs → “Providers” → `/manager/billing/providers/`
- **Views**: `app/billing/views.py` (`providers_list`, `api_provider_create`, `api_provider_delete`)
- **Templates**: `app/templates/billing/providers_list.html`
- **JS/CSS**: Inline JS in template (no external file)
- **Services**: `app/billing/services_provider.py`
- **Models**: `billing.Provider`, `debts.DebtorDebt`, `debts.CreditorDebt`
- **Data effects**: Create provider; soft‑delete provider (blocked if open debts)
- **Audit**: **Yes** — create/delete logged

21) **Provider returns list + detail**
- **Roles allowed**: Manager (Owner allowed)
- **UI entry**: Billing tabs → “Provider returns” → `/manager/billing/returns/list/`
- **Views**: `app/billing/views.py` (`providers_returns_list_page`, `api_returns_list`, `return_view`)
- **Templates**: `app/templates/billing/providers_returns_list.html`, `app/templates/billing/return_view.html`
- **JS/CSS**: `app/static/js/billing_returns_list.js`
- **Services**: `app/billing/selectors.py`, `app/billing/serializers.py`
- **Models**: `billing.ProviderReturn`, `inventory.ProductMovement`, `stock.StockFifoLayer`, `debts.CreditorDebt`
- **Data effects**: Read‑only on list/detail
- **Audit**: **No** (list/detail)

22) **Provider return wizard (from bill)**
- **Roles allowed**: Manager (Owner allowed)
- **UI entry**: Bill detail → “Return wizard” → `/manager/billing/bills/<id>/returns-wizard/`
- **Views**: `app/billing/views.py` (`bill_return_wizard`)
- **Templates**: `app/templates/billing/bill_return_wizard.html`
- **JS/CSS**: None (server‑side form)
- **Services**: `app/billing/services.py` (`create_return`)
- **Models**: `billing.ProviderReturn`, `billing.ProviderReturnItem`, `stock.StockFifoLayer`, `inventory.ProductMovement`, `debts.CreditorDebt`, `financials.Receipt`
- **Data effects**: Stock decrease (FIFO consume); creditor debt; receipts; money container balances
- **Audit**: **Yes** — return create logged

23) **Collect provider return (full/partial)**
- **Roles allowed**: Manager (Owner allowed)
- **UI entry**: Provider return detail → buttons → `/manager/billing/returns/<id>/collect-*`
- **Views**: `app/billing/views.py` (`collect_return_full`, `collect_return_batch`)
- **Templates**: `app/templates/billing/return_view.html`
- **Services**: `app/billing/services.py` (`collect_full`, `collect_partial`)
- **Models**: `debts.CreditorDebt`, `debts.CreditorReceipt`, `financials.Receipt`
- **Data effects**: Creditor receipts; money container balances
- **Audit**: **Yes** — payment logged

---

### Debts (AP/AR)
24) **Debtor debts list**
- **Roles allowed**: Manager (Owner allowed)
- **UI entry**: Billing tabs → “Debts (debtor)” → `/manager/debts/`
- **Views**: `app/debts/views.py` (`debts_page`, `api_debts_list`)
- **Templates**: `app/templates/debts/debts_list.html`
- **JS/CSS**: `app/static/js/debts_debtors.js`
- **Services**: `app/debts/selectors.py`, `app/debts/serializers.py`
- **Models**: `debts.DebtorDebt`
- **Data effects**: Read‑only
- **Audit**: **No**

25) **Creditor debts list**
- **Roles allowed**: Manager (Owner allowed)
- **UI entry**: Billing tabs → “Debts (creditor)” → `/manager/debts/creditors/`
- **Views**: `app/debts/views.py` (`creditors_page`, `api_creditors_list`)
- **Templates**: `app/templates/debts/creditors_list.html`
- **JS/CSS**: `app/static/js/debts_creditors.js`
- **Services**: `app/debts/selectors.py`, `app/debts/serializers.py`
- **Models**: `debts.CreditorDebt`
- **Data effects**: Read‑only
- **Audit**: **No**

26) **Manual debt creation**
- **Roles allowed**: Manager (Owner allowed)
- **UI entry**: Billing tabs → “Add debt” → `/manager/debts/add/`
- **Views**: `app/debts/views.py` (`add_debt`, `api_manual_debt_save`)
- **Templates**: `app/templates/debts/add_debt.html`
- **JS/CSS**: `app/static/js/billing_add_debt.js`
- **Services**: `app/debts/services.py` (`create_manual_debt`)
- **Models**: `debts.DebtorDebt` / `debts.CreditorDebt`, `financials.Receipt`
- **Data effects**: Create manual debt; optional initial payment/collection; financial receipts
- **Audit**: **Yes** — payments/collections logged

27) **Debt detail + pay/collect + reminders**
- **Roles allowed**: Manager (Owner allowed)
- **UI entry**: Debts list → open entry → `/manager/debts/view/<direction>/<id>/`
- **Views**: `app/debts/views.py` (`view_debt`, `api_entry_*`, `api_entry_set_reminder`)
- **Templates**: `app/templates/debts/view_debt.html`
- **JS/CSS**: `app/static/js/debts_view_debt.js`
- **Services**: `app/debts/services.py` (`pay_debt`, `collect_debt`, `set_reminder`)
- **Models**: `debts.DebtorDebt`, `debts.CreditorDebt`, `debts.DebtReminder`, `financials.Receipt`
- **Data effects**: Update paid/collected; receipts; reminder rows
- **Audit**: **Yes** — payment/collection logged

---

### Financials (Money Containers)
28) **Money container list**
- **Roles allowed**: Manager (Owner allowed)
- **UI entry**: Manager menu → “Money Containers” → `/financials/manager/containers/`
- **Views**: `app/financials/views_manager.py` (`container_list`)
- **Templates**: `app/templates/financials/manager/container_list.html`, `app/templates/financials/manager/base_financials.html`, `app/templates/financials/manager/_secondary_menu.html`
- **JS/CSS**: Base styles only
- **Services**: `app/financials/services.py` (`container_balance`)
- **Models**: `financials.MoneyContainer`, `financials.PostingLine`
- **Data effects**: Read‑only
- **Audit**: **No**

29) **Create/edit money container**
- **Roles allowed**: Manager (Owner allowed)
- **UI entry**: Financials secondary menu → “Create container” → `/financials/manager/containers/create/`, edit via `/financials/manager/containers/<id>/edit/`
- **Views**: `app/financials/views_manager.py` (`container_create`, `container_edit`)
- **Templates**: `app/templates/financials/manager/container_form.html`, `app/templates/financials/manager/container_edit.html`
- **Services**: `app/financials/services.py` (`alloc_ref_code`, `ensure_currency_states`, `post_initial_balance`)
- **Models**: `financials.MoneyContainer`, `financials.MoneyContainerCurrency`, `financials.ContainerFeature`
- **Data effects**: Create/update container; optional opening balance receipt
- **Audit**: **No** explicit audit events

30) **Container movements (posting lines)**
- **Roles allowed**: Manager (Owner allowed)
- **UI entry**: Financials secondary menu → “Movements” → `/financials/manager/movements/`
- **Views**: `app/financials/views_manager.py` (`container_movements`)
- **Templates**: `app/templates/financials/manager/container_movements.html`
- **Models**: `financials.PostingLine`, `financials.Receipt`
- **Data effects**: Read‑only
- **Audit**: **No**

31) **Manual container events (add/withdraw/transfer/exchange)**
- **Roles allowed**: Manager (Owner allowed)
- **UI entry**: Financials secondary menu → “Manual events” → `/financials/manager/manual/`
- **Views**: `app/financials/views_manager.py` (`container_manual_events`), `app/financials/views_api.py` (`manual_event`)
- **Templates**: `app/templates/financials/manager/container_manual_events.html`
- **JS/CSS**: `app/static/js/financials_manual_events.js`
- **Services**: `app/financials/manual_events.py`, `app/financials/services.py`
- **Models**: `financials.Receipt`, `financials.PostingLine`, `financials.MoneyContainer`
- **Data effects**: Receipts + posting lines; updates container balances
- **Audit**: **No** explicit audit events

32) **FX settings**
- **Roles allowed**: Manager (Owner allowed)
- **UI entry**: Financials secondary menu → “FX settings” → `/financials/manager/fx/`
- **Views**: `app/financials/views_manager.py` (`fx_settings`)
- **Templates**: `app/templates/financials/manager/fx_settings.html`
- **Models**: `financials.FxSettings`
- **Data effects**: Update active FX rate
- **Audit**: **No**

---

### POS / Sales
33) **POS sales screen**
- **Roles allowed**: Cashier (Manager allowed)
- **UI entry**: Login as Cashier → `/pos/`
- **Views**: `app/pos/views.py` (`pos_screen`)
- **Templates**: `app/templates/pos/screen.html` (extends `app/templates/pos/base_pos.html`)
- **JS/CSS**: `app/static/pos.js`, `app/static/pos_customers.js`, `app/static/pos.css`
- **Services**: `app/pos/api_bills.py` (save bill), `app/pos/services.py` (`finalize_pos_bill`)
- **Models**: `pos.SalesBill`, `pos.SalesBillRow`, `pos.CustomerProfile`, `inventory.ProductMovement`, `stock.StockFifoLayer`, `debts.DebtorDebt`, `financials.Receipt`
- **Data effects**: Create/park/finalize POS bills; FIFO stock out; receipts; customer debt entries for unpaid/partial
- **Audit**: **Yes** — POS bill saved/parked/deleted events via `audit_log.services.log_event`

34) **POS shift start/end**
- **Roles allowed**: Cashier (Manager allowed)
- **UI entry**: POS top bar “Shift” (buttons in `pos/base_pos.html`)
- **Views**: `app/pos/api_shifts.py` (`api_shift_start`, `api_shift_end`)
- **JS/CSS**: `app/static/pos.js`
- **Models**: `pos.PosShift`, `pos.PosLoginSession`, `pos.PosDay`
- **Data effects**: Create/update shift rows; session tracking
- **Audit**: **Yes** — shift start/end events

35) **POS bills today list (left panel)**
- **Roles allowed**: Cashier (Manager allowed)
- **UI entry**: POS screen left panel
- **Views**: `app/pos/api_bills.py` (`api_bills_today`, `api_bill_detail`)
- **Templates**: `app/templates/pos/screen.html`
- **JS/CSS**: `app/static/pos.js`
- **Data effects**: Read‑only
- **Audit**: **No**

36) **POS manager overview (timeline)**
- **Roles allowed**: Manager (staff/superuser)
- **UI entry**: Manager menu → “POS” → `/pos/manager/overview/`
- **Views**: `app/pos/views.py` (`pos_manager_overview`, `pos_manager_overview_timeline`)
- **Templates**: `app/templates/pos/manager_overview.html`, `app/templates/pos/_manager_overview_timeline_items.html`
- **JS/CSS**: Inline JS in template (no external file)
- **Models**: `pos.PosDay`, `pos.PosShift`, `pos.PosLoginSession`, `pos.SalesBill`
- **Data effects**: Read‑only
- **Audit**: **No**

37) **POS manager bill detail**
- **Roles allowed**: Manager (staff/superuser)
- **UI entry**: POS overview → bill detail → `/pos/manager/bill/<id>/`
- **Views**: `app/pos/views.py` (`pos_manager_bill_detail`)
- **Templates**: `app/templates/pos/manager_bill_detail.html`
- **Models**: `pos.SalesBill`, `pos.SalesBillRow`, `inventory.SaleCostPart`, `stock.StockFifoLayer`
- **Data effects**: Read‑only
- **Audit**: **No**

38) **POS customer profiles list**
- **Roles allowed**: Manager (staff/superuser)
- **UI entry**: POS overview → “Customers” → `/pos/manager/customers/`
- **Views**: `app/pos/views.py` (`pos_manager_customers`)
- **Templates**: `app/templates/pos/manager_customers.html`
- **Models**: `pos.CustomerProfile`, `pos.SalesBill`, `debts.DebtorDebt`
- **Data effects**: Read‑only
- **Audit**: **No**

39) **POS customer debts list**
- **Roles allowed**: Manager (staff/superuser)
- **UI entry**: POS overview → “Customers Debts” → `/pos/manager/customers/debts/`
- **Views**: `app/pos/views.py` (`pos_manager_customer_debts`)
- **Templates**: `app/templates/pos/manager_customer_debts.html`
- **Models**: `debts.DebtorDebt`
- **Data effects**: Read‑only
- **Audit**: **No**

---

### Audit Log
40) **Owner audit dashboard**
- **Roles allowed**: Owner only
- **UI entry**: Owner menu → “Audit” → `/audit/owner/`
- **Views**: `app/audit_log/views.py` (`owner_audit_dashboard`)
- **Templates**: `app/templates/audit_log/owner_dashboard.html`, `app/templates/audit_log/_event_card.html`
- **JS/CSS**: `app/static/audit_log/audit_ui.css`, `app/static/audit_log/audit_ui.js`
- **Models**: `audit_log.AuditLog`, `audit_log.AuditSession`
- **Data effects**: Read‑only
- **Audit**: **N/A** (this is the audit UI)

---

### Notifications

41) **Manager notifications dropdown**
- **Roles allowed**: Manager
- **UI entry**: Top bar bell (manager dashboard and manager pages)
- **Views**: `app/notifications/views.py` (`list_notifications`, `mark_read`)
- **Templates**: `app/templates/manager/base_manager.html`, `app/templates/base_dash.html`
- **JS/CSS**: `app/static/js/notifications.js`
- **Services**: `app/notifications/services.py` (daily reminders)
- **Models**: `notifications.Notification`, `debts.DebtReminder`
- **Data effects**: Read‑only; mark_read updates `Notification.is_read`
- **Audit**: **No**

42) **Notifications page (placeholder)**
- **Roles allowed**: Manager
- **UI entry**: Direct URL only (no menu link): `/manager/notifications/`
- **Views**: `app/notifications/views.py` (`notifications_page`)
- **Templates**: `app/templates/notifications/notifications_list.html`
- **Data effects**: Read‑only placeholder
- **Audit**: **No**

---

### Legacy Ledger (Volt)

43) **Volt movements page (legacy ledger)**
- **Roles allowed**: Manager (staff/superuser)
- **UI entry**: Manager menu → “Volt” → `/manager/volt/`
- **Views**: `app/ledger/views.py` (`volt_home`, `volt_movements_page`, `api_volt_*`)
- **Templates**: `app/templates/ledger/volt_movement.html`
- **JS/CSS**: `app/static/js/ledger/volt_movement.js`, `app/static/js/ledger/volt.css`
- **Models**: `ledger.JournalEntry`, `ledger.JournalLine`, `ledger.Account`
- **Data effects**: Read‑only
- **Audit**: **No**

---

## C) FILE‑BY‑FILE INDEX (CORE)

Legend: ✅ used in UI flow, 🟡 reachable but partial/incomplete, ❌ not used by any UI flow (or service‑only without UI).

### accounts
- ✅ `app/accounts/views.py` — auth/dashboards/staff management; called by `app/marketpos/urls.py`; uses `accounts.forms`, `accounts.models`
- ✅ `app/accounts/forms.py` — login/owner/staff forms; used by `views.py`
- ✅ `app/accounts/models.py` — `AccountProfile`; used across apps
- ✅ `app/accounts/utils.py` — role helpers; used by `views.py`, `decorators.py`
- ✅ `app/accounts/decorators.py` — role_required; used by multiple apps
- ❌ `app/accounts/services.py` — empty placeholder
- ✅ `app/accounts/templatetags/account_tags.py` — used in templates (role labels)
- 🟡 `app/accounts/management/commands/factory_reset_accounts.py` — CLI only
- 🟡 `app/accounts/tests.py` — tests only
- ✅ Templates: `app/templates/login.html`, `app/templates/accounts/manage_accounts.html`, `app/templates/accounts/owner.html`, `app/templates/owner/*.html`, `app/templates/manager/manager_dash.html`, `app/templates/cashier/cashier_dash.html`

### catalog
- ✅ `app/catalog/urls.py` — manager products + APIs; included in `app/marketpos/urls.py`
- ✅ `app/catalog/views.py` — collections/products UI + APIs; uses `audit_log.services`
- ✅ `app/catalog/models.py` — collections/sets/products/barcodes/unit IDs
- ✅ `app/catalog/import_views.py` — import wizard endpoints; uses `import_engine`
- ✅ `app/catalog/export_views.py` — export endpoints; uses `export_engine`
- ✅ `app/catalog/edit_api.py` — batch edit/delete API; used by products UI
- ✅ `app/catalog/browser_api.py` — browser APIs used by JS
- ✅ `app/catalog/import_engine.py`, `app/catalog/import_rules.py` — import backend used by `import_views`
- ✅ `app/catalog/export_engine.py` — export backend used by `export_views`
- ✅ `app/catalog/io_records.py` — `CatalogDataJob` used by import/export
- 🟡 `app/catalog/tests.py` — tests only
- ✅ Templates: `app/templates/manager/collections_list.html`, `app/templates/manager/product_new.html`, `app/templates/manager/_products_tabs.html`, `app/templates/manager/products_import.html`, `app/templates/manager/products_import_report.html`, `app/templates/manager/products_export.html`
- ✅ Static: `app/static/js/manager_browser.js`, `app/static/js/manager_search.js`, `app/static/js/product_new.js`, `app/static/js/products_import.js`, `app/static/js/manager_export.js`

### stock
- ✅ `app/stock/urls.py` — stock list/move + APIs; included under `/manager/stock/`
- ✅ `app/stock/views.py` — stock list/move; uses `stock.services` + `audit_log.services`
- ✅ `app/stock/models.py` — containers + StockEntry + FIFO layers
- ✅ `app/stock/services.py` — FIFO + transfers; used by stock + inventory + billing
- 🟡 `app/stock/tests.py` — tests only
- ✅ Templates: `app/templates/stock/stock_list.html`, `app/templates/stock/stock_move.html`
- ✅ Static: `app/static/stock/stock_list.js`, `app/static/stock/stock_move.js`

### inventory
- ✅ `app/inventory/views.py` — product movements page; referenced by `catalog/urls.py`
- ✅ `app/inventory/models.py` — ProductMovement + SaleCostPart; used by billing/pos/stock
- ✅ `app/inventory/services.py` — inventory movement API; used by billing/pos/stock
- ❌ `app/inventory/urls.py` — not included in `marketpos/urls.py`
- 🟡 `app/inventory/tests.py` — tests only
- ✅ Templates: `app/templates/manager/product_movements.html`
- ✅ Static: `app/static/inventory/product_movements.js`, `app/static/inventory/product_movements.css`

### billing
- ✅ `app/billing/urls.py` — billing routes; included under `/manager/billing/`
- ✅ `app/billing/views.py` — billing pages + APIs; calls `billing.services`
- ✅ `app/billing/services.py` — purchase bill/returns/payments; heavy side‑effects (inventory, debts, financials, audit)
- ✅ `app/billing/services_provider.py` — provider create/delete; used by `views.py`
- ✅ `app/billing/selectors.py` — list filtering; used by `views.py`
- ✅ `app/billing/serializers.py` — API row serialization
- ✅ `app/billing/models.py` — Provider/Bill/Return models
- 🟡 `app/billing/tests/*` — tests only
- ✅ Templates: `app/templates/billing/*.html`
- ✅ Static: `app/static/js/billing_add_bill.js`, `app/static/js/billing_bills.js`, `app/static/js/billing_bill_view.js`, `app/static/js/billing_returns_list.js`, `app/static/js/billing_add_debt.js`

### debts
- ✅ `app/debts/urls.py` — debts routes; included under `/manager/debts/`
- ✅ `app/debts/views.py` — debts pages + APIs; calls `debts.services`
- ✅ `app/debts/services.py` — manual debts + pay/collect + reminders; uses financials
- ✅ `app/debts/selectors.py` — list filters; used by `views.py`
- ✅ `app/debts/serializers.py` — API row serialization
- ✅ `app/debts/models.py` — debtor/creditor tables (managed=False) + reminders
- 🟡 `app/debts/tests/*` — tests only
- ✅ Templates: `app/templates/debts/*.html`
- ✅ Static: `app/static/js/debts_debtors.js`, `app/static/js/debts_creditors.js`, `app/static/js/debts_view_debt.js`

### financials
- ✅ `app/financials/urls.py` — financials routes; included under `/financials/`
- ✅ `app/financials/views_manager.py` — container pages + FX
- ✅ `app/financials/views_api.py` — manual events API + ref preview
- ✅ `app/financials/manual_events.py` — add/withdraw/transfer/exchange backend
- ✅ `app/financials/services.py` — receipt engine + balances + FX
- ✅ `app/financials/models.py` — money containers, receipts, lines, FX, counterparties
- ✅ `app/financials/forms.py` — container + FX forms; used by `views_manager.py`
- 🟡 `app/financials/tests/*` — tests only
- ✅ Templates: `app/templates/financials/manager/*.html`
- ✅ Static: `app/static/js/financials_manual_events.js`

### pos
- ✅ `app/pos/urls.py` — POS pages + APIs; included under `/pos/`
- ✅ `app/pos/views.py` — POS screens + manager overviews
- ✅ `app/pos/api_bills.py` — POS bill create/update/delete + customers API
- ✅ `app/pos/api_shifts.py` — shift start/end
- ✅ `app/pos/api_sessions.py` — login session close
- ✅ `app/pos/views_api.py` — product lookup APIs (barcode/name/code/id)
- ✅ `app/pos/services.py` — finalize POS bills to inventory/financials
- ✅ `app/pos/models.py` — SalesBill + shifts + customers
- 🟡 `app/pos/tests/*` — tests only
- ✅ Templates: `app/templates/pos/*.html`
- ✅ Static: `app/static/pos.js`, `app/static/pos_customers.js`, `app/static/pos.css`

### audit_log
- ✅ `app/audit_log/urls.py` — owner audit dashboard
- ✅ `app/audit_log/views.py` — audit UI
- ✅ `app/audit_log/services.py` — logging utilities used across apps
- ✅ `app/audit_log/models.py` — AuditLog + AuditSession
- ✅ `app/audit_log/middleware.py` — request context
- ✅ `app/audit_log/signals.py` — login/logout audit
- 🟡 `app/audit_log/tests.py` — tests only
- ✅ Templates: `app/templates/audit_log/*.html`
- ✅ Static: `app/static/audit_log/audit_ui.js`, `app/static/audit_log/audit_ui.css`

### notifications
- ✅ `app/notifications/urls.py` — notifications routes; included under `/manager/notifications/`
- ✅ `app/notifications/views.py` — list + mark_read + page
- ✅ `app/notifications/services.py` — daily reminder generation; used by middleware
- ✅ `app/notifications/middleware.py` — triggers daily reminder notifications
- ✅ `app/notifications/models.py` — Notification table
- ❌ `app/notifications/selectors.py` — unused
- 🟡 `app/notifications/tests.py` — tests only
- 🟡 Templates: `app/templates/notifications/notifications_list.html` (placeholder)
- ✅ Static: `app/static/js/notifications.js`

### ledger (legacy)
- ✅ `app/ledger/urls.py` — volt routes; included under `/manager/volt/`
- ✅ `app/ledger/views.py` — volt UI + APIs
- ✅ `app/ledger/models.py` — journal models
- ✅ `app/ledger/services.py` — ledger posting API (not wired to new financials)
- 🟡 Other ledger modules (`reports.py`, `forms.py`, `perms.py`, etc.) — used internally or legacy
- 🟡 `app/ledger/tests.py` — tests only
- ✅ Templates: `app/templates/ledger/volt_movement.html`
- ✅ Static: `app/static/js/ledger/volt_movement.js`, `app/static/js/ledger/volt.css`

### core / misc (installed, UI‑silent)
- ✅ `app/core/middleware.py` — RequireLoginMiddleware; applies globally
- ❌ `app/printing/*` — no URLs/templates detected
- ❌ `app/io_ops/*` — no URLs/templates detected
- ❌ `app/backups_app/*` — no URLs/templates detected

---

## D) GAP REPORT (SIMPLE MARKET SYSTEM BASELINE)

1) **Sales refunds/voids/exchanges + printable receipts**
- **Status**: **Missing**
- **Owner app**: `pos` + `billing` + `printing`
- **Reusable code**:
  - POS: `app/pos/services.py` (inventory movement posting)
  - Financials: `app/financials/services.py` (receipt posting/reversal)
  - Inventory: `app/inventory/services.py` (sale movements/FIFO)
- **Missing**:
  - Routes/views: POS refund/void endpoints and UI (none in `app/pos/urls.py`)
  - Templates: refund/void screens; printable receipt template(s)
  - Services: sale return workflows (stock in + debt/receipt reversal)
- **Risk areas**: stock/FIFO, financials, audit, POS
- **Suggested UI entry**: POS screen → “Refund/Exchange” button → `/pos/refunds/` with print action

2) **Cashier shift close + cash counting + discrepancy + day close summaries**
- **Status**: **Partially present (models only)**
- **Owner app**: `pos` (UI), possibly `ledger` for cash count
- **Reusable code**:
  - POS: `app/pos/models.py` (`PosShift`, `PosDay`)
  - Ledger: `app/ledger/models.py` (`CashSession`, `CashCount`) and `services.close_session`
- **Missing**:
  - Routes/views/templates for shift closing & cash counting
  - Day close summary report page
- **Risk areas**: financials, POS, audit
- **Suggested UI entry**: POS top bar → “Close shift / Cash count” → `/pos/shift/close/`

3) **Stocktaking (inventory count adjustments) + write‑off (damage/expired)**
- **Status**: **Missing**
- **Owner app**: `stock` + `inventory`
- **Reusable code**:
  - `inventory.ProductMovement` supports `ADJUSTMENT`
  - `stock.services.apply_movement` + FIFO helpers
- **Missing**:
  - Routes/views/templates for stock count entry and adjustments
  - Services for controlled adjustment posting + audit
- **Risk areas**: stock/FIFO, audit
- **Suggested UI entry**: Stock menu → “Stocktake” → `/manager/stock/stocktake/`

4) **Purchase bill statuses (draft/confirmed/partial/paid) in UI**
- **Status**: **Partially present**
- **Evidence**: Bills list displays paid/partial/unpaid derived from debts; no draft/confirmed states in UI or model
- **Owner app**: `billing`
- **Reusable code**: `billing.Bill.status` property + debts integration
- **Missing**:
  - Draft/confirmed workflow fields & UI controls
  - Routes for confirm/post actions
- **Risk areas**: stock/FIFO, debts, financials
- **Suggested UI entry**: Billing add bill → “Save draft / Confirm” buttons

5) **Customer/provider edit fields + statement pages**
- **Status**: **Missing**
- **Owner app**: `pos` (customers), `billing` (providers), `debts` (statements)
- **Reusable code**:
  - Provider list API in `app/billing/views.py`
  - Customer list in `app/pos/views.py`
  - Debts list filters for statements
- **Missing**:
  - Provider edit/detail page + statement view
  - Customer edit/detail page + statement view
- **Risk areas**: debts, financials
- **Suggested UI entry**: Providers list → “View statement” → `/manager/billing/providers/<id>/statement/`

6) **Unify money engine (financials vs legacy ledger) plan**
- **Status**: **Missing (parallel systems)**
- **Evidence**: Manager menu exposes both “Money containers” (`financials`) and “Volt” (`ledger`) with no integration
- **Owner app**: `financials` + `ledger`
- **Reusable code**: `financials.services` (new engine) and `ledger.services` (legacy)
- **Missing**:
  - Migration/bridge plan and UI surface to reconcile balances
  - Decommission strategy for `ledger` UI
- **Risk areas**: financials, audit
- **Suggested UI entry**: Manager → “Financials Migration” page

7) **Serial numbers for key transactions**
- **Status**: **Partial**
- **Evidence**:
  - Billing bills/returns have serials (`billing.Bill.serial`, `ProviderReturn.serial`)
  - Financials receipts have serials (`financials.Receipt.serial`)
  - POS sales bills have **no serial** (only `id`)
  - Stock transfers use ref string in audit, not user‑facing serial
- **Owner app**: `pos`, `stock`
- **Reusable code**: Serial allocation pattern in `billing.models.Bill.save`
- **Missing**:
  - POS sale serial/receipt number + display
  - Stock transfer reference surfaced in UI
- **Risk areas**: audit, POS, financials
- **Suggested UI entry**: POS receipt view shows “Receipt #”

8) **Notifications page/menu wiring**
- **Status**: **Partial**
- **Evidence**: Notifications page exists (`/manager/notifications/`) but no menu link; dropdown uses JS only
- **Owner app**: `notifications`
- **Reusable code**: `notifications.views.notifications_page`
- **Missing**:
  - Menu entry in manager menu
  - List UI with filters/history
- **Risk areas**: audit/ops
- **Suggested UI entry**: Manager menu → “Notifications” → `/manager/notifications/`

---

## E) NEXT IMPLEMENTATION STEPS (TOP 5)

1) **Wire notifications page + menu**
   - **Scope**: add manager menu link; expand notifications page to list/filters
   - **Apps**: `notifications`, `templates/manager/_menu_items.html`
   - **Tests**: UI smoke (GET `/manager/notifications/`), notifications list API test

2) **Provider/Customer edit + statement pages**
   - **Scope**: CRUD detail pages + debt/receipt statement view
   - **Apps**: `billing`, `pos`, `debts`
   - **Tests**: provider edit form; statement query correctness

3) **Stocktake + write‑off adjustments**
   - **Scope**: stock count UI + posting adjustments + audit
   - **Apps**: `stock`, `inventory`, `audit_log`
   - **Tests**: FIFO consistency after adjustments

4) **POS refunds/exchanges + printable receipts**
   - **Scope**: sale return workflow + receipts + printing templates
   - **Apps**: `pos`, `inventory`, `financials`, `printing`, `audit_log`
   - **Tests**: sale return stock/FIFO reversal; receipt posting reversal

5) **Shift close + cash counting + day close summary**
   - **Scope**: close shift UI; cash count capture; daily summary
   - **Apps**: `pos`, `ledger` (optional), `financials`, `audit_log`
   - **Tests**: shift close consistency; discrepancy calculations
