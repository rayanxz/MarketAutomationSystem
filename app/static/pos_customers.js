// ===== Customer name fixes & autocomplete (separate from pos.js) =====

// 1) Stop Space / Ctrl+Backspace from triggering global shortcuts
//    when typing in certain inputs (custName, pname).
// Use capture phase so this runs BEFORE pos.js document keydown handler.
document.addEventListener("keydown", (e) => {
  const t = e.target;

  const spaceSafeIds = ["custName", "pname"]; // Space behaves normally only here
  const backspaceSafeIds = ["custName", "pname", "barcode", "pcode", "pid"]; // Ctrl+Backspace safe in all 5

  const id = t && t.id;

  // Space: allow typing in custName/pname, but don't bubble to document
  // so it won't trigger "edit last row".
  if (e.code === "Space" && id && spaceSafeIds.includes(id)) {
    e.stopPropagation();
    return;
  }

  // Ctrl+Backspace: allow browser default (delete word) in all 4 search boxes + custName/pname,
  // but don't bubble to document so it won't open the "delete whole bill" modal.
  if (
    e.code === "Backspace" &&
    e.ctrlKey &&
    !e.altKey &&
    !e.metaKey &&
    id &&
    backspaceSafeIds.includes(id)
  ) {
    // DO NOT call preventDefault → keep native word-delete behavior
    e.stopPropagation();
    return;
  }
}, true);


// 2) Customer autocomplete dropdown
(function () {
  const input    = document.getElementById("custName");
  const panel    = document.getElementById("custSuggest");
  const createCb = document.getElementById("custCreate");

  if (!input || !panel) return;

  let items = [];
  let idx   = -1;
  let timer = null;

  function fmtPhone(p) {
    return p ? ` (${p})` : "";
  }

  function show(itemsList) {
    panel.innerHTML = "";
    items = itemsList || [];
    idx   = -1;

    if (!items.length) {
      panel.style.display = "none";
      return;
    }

    items.forEach((c, i) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn";
      btn.style.cssText = "width:100%; text-align:right; margin:4px 0; font-size:13px;";
      btn.textContent = c.name + fmtPhone(c.phone);
      btn.addEventListener("click", () => choose(i));
      panel.appendChild(btn);
    });

    panel.style.display = "block";
  }

  function highlight() {
    [...panel.querySelectorAll("button")].forEach((b, i) => {
      b.style.filter = (i === idx) ? "brightness(1.2)" : "none";
    });
  }

  function choose(i) {
    const c = items[i];
    if (!c) return;

    // Fill input with chosen customer name
    input.value = c.name;

    // Update global bill state if available
    if (window.state && state.bill) {
      state.bill.customerName = c.name;
      state.bill.customerId   = c.id;
      state.bill.createNewCustomer = false;
    }

    if (createCb) {
      createCb.checked = false;
    }

    panel.style.display = "none";
  }

  // Input typing → debounce fetch
  input.addEventListener("input", () => {
    const q = input.value.trim();
    if (timer) clearTimeout(timer);

    if (!q) {
      show([]);
      // If user is typing a fresh name, they might want a new customer
      if (window.state && state.bill) {
        state.bill.customerName = "";
        state.bill.customerId   = null;
      }
      return;
    }

    // Update state.bill.customerName live
    if (window.state && state.bill) {
      state.bill.customerName = q;
      state.bill.customerId   = null;   // typing breaks any previous selection
    }

    timer = setTimeout(async () => {
      try {
        const res = await fetch(`/pos/api/customers/search/?q=${encodeURIComponent(q)}&limit=7`);
        const j   = await res.json();
        if (!j.ok) {
          show([]);
          return;
        }
        show(j.hits || []);
      } catch (err) {
        console.error(err);
        show([]);
      }
    }, 120);
  });

  // Keyboard navigation for the dropdown
  input.addEventListener("keydown", (e) => {
    if (panel.style.display !== "block") return;

    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (!items.length) return;
      idx = Math.min(idx + 1, items.length - 1);
      highlight();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (!items.length) return;
      idx = Math.max(idx - 1, 0);
      highlight();
    } else if (e.key === "Enter") {
      if (!items.length) return;
      e.preventDefault();
      choose(idx >= 0 ? idx : 0);
    } else if (e.key === "Escape") {
      e.preventDefault();
      panel.style.display = "none";
    }
  });

  // Click outside → close suggestions
  document.addEventListener("click", (e) => {
    if (!panel.contains(e.target) && e.target !== input) {
      panel.style.display = "none";
    }
  });
})();


// ===== Extra POS enhancements (parked styling, auto-new-after-park, protect saved bills) =====

// 1) Stronger highlight for parked bills in left panel + active-bill highlight
(function () {
  if (typeof renderLeftBills !== "function") return;
  const originalRenderLeftBills = renderLeftBills;

  // Override global renderLeftBills
  window.renderLeftBills = function () {
    originalRenderLeftBills(); // call original

    try {
      // state.todayBills and leftListEl come from pos.js
      if (!window.state || !state.todayBills || !leftListEl) return;

      const byId = new Map();
      (state.todayBills || []).forEach((b) => {
        byId.set(String(b.id), b);
      });

      const activeId = state.selectedBillId != null
        ? String(state.selectedBillId)
        : null;

      leftListEl.querySelectorAll("button.btn").forEach((btn) => {
        const id = btn.dataset.id;
        const bill = byId.get(String(id));
        if (!bill) return;

        const isActive = activeId && activeId === String(bill.id);

        if (isActive) {
          // Active bill (currently opened in middle) -> green-ish highlight
          btn.style.border = "2px solid #22c55e";
          btn.style.background = "rgba(34,197,94,0.16)";
          btn.style.boxShadow = "0 0 0 1px rgba(34,197,94,0.4)";
        } else if (bill.parked) {
          // Pending/parked bill -> orange highlight
          btn.style.border = "2px solid #f97316";
          btn.style.background = "rgba(249,115,22,0.12)";
          btn.style.boxShadow = "0 0 0 1px rgba(249,115,22,0.4)";
        } else {
          // Normal saved bill, not currently active
          btn.style.border = "";
          btn.style.background = "";
          btn.style.boxShadow = "";
        }
      });
    } catch (err) {
      console.error("renderLeftBills override failed:", err);
    }
  };
})();


// 2) After parking a bill → start a new empty bill automatically
(function () {
  if (typeof handleParkBill !== "function") return;

  // Replace the original handleParkBill with an extended version
  window.handleParkBill = async function () {
  if (state.bill.locked) return;
  if (!validateBillBeforeSave({ parked: true })) return;

  try {
    const j = await sendBillToBackend({ parked: true });
    state.bill.id = j.bill.id;
    state.bill.parked = true;

    await loadTodayBills();
    alert("تم تعليق الفاتورة بنجاح.");

    // ---- Start a fresh empty bill for the cashier ----
    state.rows = [];
    state.selectedIndex = -1;
    state.editing = false;

    resetBillState();         // reset bill meta (id, amounts, customer, etc.)
    state.selectedBillId = null;  // no active selection in left

    setBillLocked(false);     // unlock inputs
    renderRows();             // clear table UI
    clearRightPanel();        // clear right panel inputs
    setEditingLock(false);    // allow barcode/name again
    syncFooterFromBill();     // reset footer (pay status, amounts, customer)

    // Focus barcode for new bill
    setTimeout(() => document.getElementById("barcode")?.focus(), 0);
  } catch (err) {
    console.error(err);
    alert("حدث خطأ أثناء تعليق الفاتورة.");
  }
};

})();

// 3) Prevent deleting finalized (saved) bills – no modal, no delete
(function () {
  if (typeof openBillConfirmModal !== "function") return;

  const originalOpenBillConfirmModal = openBillConfirmModal;

  window.openBillConfirmModal = function () {
    // If bill is locked (finalized / non-parked), do NOTHING
    if (window.state && state.bill && state.bill.locked) {
      // silently ignore Ctrl+Backspace on saved bills
      return;
    }
    // For new or parked bills, keep original delete behavior
    originalOpenBillConfirmModal();
  };
})();
