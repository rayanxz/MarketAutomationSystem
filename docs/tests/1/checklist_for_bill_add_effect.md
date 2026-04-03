A. Purchase Bill (bill_add)
 # create bill with total = 0
 # payment section is disabled
 # UI shows clear message (non-financial)
 # save works without errors
 result:
 # no receipt created
 # no container change
 # no debt created
 # bill appears normally in bill_list & bill_view
B. Provider Return
 create return with total = 0
 payment controls disabled
 save works
 result:
 no receipt
 no container movement
 no debt
C. Edge Case
 bill with:
some items = 0
total > 0
 payment works normally
🟠 2. PAYMENT INTENT + FINANCIAL FLOW
A. Purchase Bill — All Modes

test each:

 # fully paid (SYP only)
 # fully paid (USD only)
 # fully paid (separate)
 # fully paid (mixed)
 partially paid (each mode)
 unpaid

for each:

 receipt created exactly once
 container updated correctly
 amounts match UI
 FX applied correctly
 no duplicate receipts
B. Provider Return — All Modes

same scenarios:

 full return paid
 partial return
 unpaid

verify:

 correct container direction (money IN)
 one receipt only
 no forced dual-currency behavior
🟡 3. RECEIPT SYSTEM (CORE)
A. One Action = One Receipt

for each:

 purchase bill
 provider return
 POS sale
 POS return

verify:

 exactly ONE primary receipt
 no duplicates
 receipt contains all currencies (if mixed)
B. Idempotency
 simulate retry (save twice / refresh / double submit)
 ensure:
still ONE receipt only
C. Receipt Content
 lines make sense:
bill_total
settlement
cash movement
 amounts add up correctly
 currencies correct
🟢 4. BILL_VIEW (HISTORICAL TRUTH)
A. Snapshot Integrity
 change product name after bill
 bill_view still shows OLD name
 change units in catalog
 bill_view unchanged
B. Payment Display
 new bill → shows correct payment info
 legacy bill → shows:
"غير معروف (سجل قديم)"
C. No Fallback
 ensure no live product data leaks into bill_view
🔵 5. RETURN VIEW (IMMUTABILITY)
 change product name after return
 return view still shows original
 missing snapshot:
shows "—"
NOT live data
🟣 6. POS SYSTEM (UNIFIED RECEIPTS)
A. POS Sale
 create sale (single currency)
 create sale (mixed currency)

verify:

 ONE receipt
 correct amounts
 no split receipts
B. POS Return
 return item
 check:
one receipt only
correct direction (money out/in)
🟤 7. CONTAINER / FINANCIAL ACCURACY
 before/after container balance check

for:

 purchase bill
 return
 POS sale
 POS return

verify:

 balance changes exactly once
 no double deduction/addition
 matches receipt values
⚫ 8. EDGE CASES (IMPORTANT)
 FX = edge values (high / low)
 partial payments weird combos
 mixed currency with only one currency paid
 empty/invalid inputs blocked
🧠 9. ACCOUNTANT TOOLS (NEW PAGES)
A. Receipt Explorer
 page loads
 filters work:
date
container
currency
 clicking receipt shows full details
B. Document Financial Trace
 open bill → trace
 see:
receipt
debt (if exists)
payments
 verify flow is correct
C. Reconciliation Dashboard
 page loads
 detects:
missing receipts
mismatches
 no false positives (basic check)
🧩 10. CROSS-SYSTEM INTEGRITY
 bill → affects:
inventory ✔
FIFO ✔
stock ✔
 return → reverses correctly
 nothing duplicated
 nothing missing
🧪 11. REGRESSION CHECK (IMPORTANT)

quick sanity:

 create normal bill (old flow still works)
 create return
 create POS sale
 nothing crashes