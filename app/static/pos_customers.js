// ===== Customer name fixes & autocomplete (separate from pos.js) =====

// 1) Stop Space from triggering global "edit latest row" when typing customer name.
// Use capture phase so this runs BEFORE pos.js document keydown handler.
document.addEventListener("keydown", (e) => {
  if (e.code === "Space" && e.target && e.target.id === "custName") {
    // Allow the space to be typed, just stop it from bubbling to document.
    e.stopPropagation();
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

// 1) Stronger highlight for parked bills in left panel
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

      leftListEl.querySelectorAll("button.btn").forEach((btn) => {
        const id = btn.dataset.id;
        const bill = byId.get(String(id));
        if (!bill) return;

        if (bill.parked) {
          // Super obvious "pending" style
          btn.style.border = "2px solid #f97316";                 // orange border
          btn.style.background = "rgba(249,115,22,0.12)";          // orange-ish bg
          btn.style.boxShadow = "0 0 0 1px rgba(249,115,22,0.4)";
        } else {
          // Reset to normal (let original styles show)
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
