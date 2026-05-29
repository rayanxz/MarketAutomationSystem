# UI/UX Static Structure Plan (Django Practical Hybrid)

## 1) Current Problem
- Many templates contain inline CSS and inline JS.
- The current `app/static` layout is inconsistent (root files + generic `js` folder + app-specific folders).
- This increases maintenance cost and makes UI changes harder to track and review.

## 2) Target Practical Structure

```text
app/static/
  shared/
    css/
    js/
  billing/
    css/
    js/
  debts/
    css/
    js/
  financials/
    css/
    js/
  stock/
    css/
    js/
  pos/
    css/
    js/
  vendor/
```

- This is the long-term target structure.
- No full-system refactor is required now.
- Legacy pages stay as-is until they are actively touched.

## 3) Naming Convention
- Keep template and static file names aligned:
  - `provider_details.html`
  - `provider_details.css`
  - `provider_details.js`
- Use consistent `snake_case` for page files.

## 4) Shared CSS/JS Policy
- Keep code page-specific by default.
- Promote code to `shared` only when 2+ pages need the same pattern.
- `shared/css` should contain cross-page UI primitives (buttons, cards, forms, table shells, helpers).
- `shared/js` should contain reusable utilities (formatters, lightweight UI helpers, generic behavior).

## 5) Template-to-JS Data Policy
- Preferred default: `json_script` for page-level config data.
- Use `data-*` attributes for repeated element-level metadata in lists/tables/cards.
- Avoid introducing new `window.__globals` unless there is a clear technical reason.

## 6) RTL / Arabic Policy
- Keep user-facing text in Arabic.
- Keep layout direction RTL.
- Use logical CSS properties when possible (`margin-inline`, `padding-inline`, `inset-inline`).
- Numeric IDs, phone values, and similar fields may use local LTR handling where needed.

## 7) Gradual Migration Policy
- Migrate one page at a time.
- Migrate when a page is actively being edited (feature, fix, or major cleanup).
- Do not run a mass refactor across all templates at once.
- New pages and heavily edited pages should follow this target structure.

## 8) Page Migration Checklist
1. Create page CSS/JS files in the correct app folder.
2. Move inline CSS from the template into the page CSS file.
3. Move inline JS from the template into the page JS file.
4. Load assets using template blocks (`extra_head`, `extra_scripts`).
5. Use `json_script` for page config and `data-*` for repeated DOM metadata.
6. Verify no functional behavior changed.
7. Verify RTL and Arabic rendering after extraction.
8. Run related tests for the migrated page and nearby flows.
