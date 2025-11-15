/* ===== Theme handling ===== */
(function () {
  const KEY = "posTheme";
  const html = document.documentElement;
  const select = document.getElementById("posTheme");

  const saved = localStorage.getItem(KEY);
  const initial = saved || html.getAttribute("data-theme") || "blue";
  html.setAttribute("data-theme", initial);
  if (select) select.value = initial;

  if (select) {
    select.addEventListener("change", () => {
      const v = select.value || "blue";
      html.setAttribute("data-theme", v);
      localStorage.setItem(KEY, v);
    });
  }

  // Ctrl+Alt+T → cycle themes
  document.addEventListener("keydown", (e) => {
    if (e.ctrlKey && e.altKey && e.key.toLowerCase() === "t") {
      const order = ["blue", "dark", "light"];
      const cur = html.getAttribute("data-theme") || "blue";
      const next = order[(order.indexOf(cur) + 1) % order.length];
      html.setAttribute("data-theme", next);
      if (select) select.value = next;
      localStorage.setItem(KEY, next);
    }
  });
})();

/* ===== Scanner-first focus nicety ===== */
const barcode = document.getElementById("barcode");
window.addEventListener("load", () => barcode && barcode.focus());
document.addEventListener("keydown", (e) => {
  if (e.code === "Slash" && !e.ctrlKey && !e.metaKey && !e.altKey) {
    e.preventDefault();
    barcode && barcode.focus();
  }
});


/* ===== Notifications dropdown (UI only) ===== */
(function () {
  const btn = document.getElementById("posNotifBtn");
  const panel = document.getElementById("posNotifPanel");
  if (!btn || !panel) return;

  const badge = document.getElementById("posNotifBadge");
  function openPanel() { panel.hidden = false; btn.setAttribute("aria-expanded", "true"); }
  function closePanel() { panel.hidden = true; btn.setAttribute("aria-expanded", "false"); }
  function togglePanel() { panel.hidden ? openPanel() : closePanel(); }

  btn.addEventListener("click", (e) => { e.stopPropagation(); togglePanel(); });
  document.addEventListener("click", (e) => {
    if (panel.hidden) return;
    const path = e.composedPath ? e.composedPath() : [];
    if (!path.includes(panel) && !path.includes(btn)) closePanel();
  });

  const unread = 0;
  if (badge) { badge.hidden = unread <= 0; if (unread > 0) badge.textContent = unread; }
})();

/* ===== Wheel routing & context-menu lock ===== */
// disable the browser context menu everywhere
document.addEventListener("contextmenu", (e) => e.preventDefault());

// helper: route wheel on a container to its inner scroller
function routeWheel(container, scroller) {
  if (!container || !scroller) return;
  container.addEventListener("wheel", (e) => {
    scroller.scrollTop += e.deltaY;
    e.preventDefault();
  }, { passive: false });
}
routeWheel(document.querySelector(".mid-panel"), document.querySelector(".table-wrap"));
routeWheel(document.querySelector(".left-panel"), document.querySelector(".bill-list"));

/* ===== Bottom-left live clock ===== */
(function () {
  const tEl = document.getElementById("posClockTime");
  const dEl = document.getElementById("posClockDate");
  if (!tEl || !dEl) return;

  const locale = "ar";
  const timeFmt = new Intl.DateTimeFormat(locale, { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: true });
  const dateFmt = new Intl.DateTimeFormat(locale, { year: "numeric", month: "2-digit", day: "2-digit", weekday: "long" });

  function tick() {
    const now = new Date();
    tEl.textContent = timeFmt.format(now);
    dEl.textContent = dateFmt.format(now);
  }
  tick();
  setInterval(tick, 1000);
})();

/* ===== POS State ===== */
const initialBillState = {
  id: null,
  parked: false,
  locked: false,

  payStatus: "full",      // 'full' | 'none' | 'partial'
  paidAmount: 0,
  totalAmount: 0,
  leftAmount: 0,

  customerId: null,
  customerName: "",
  createNewCustomer: false,
  createdAt: null,
};

const state = {
  mode: "add",              // 'add' | 'inq'
  rows: [],                 // [{id,name,number,qty,uomIndex,price,conv,u1Label,u2Label,discPct,discAmt,notes,lastBarcode,lastCode}]
  selectedIndex: -1,
  editing: false,

  bill: { ...initialBillState },
  todayBills: [],
  selectedBillId: null,
};

function resetBillState() {
  Object.assign(state.bill, initialBillState);
}

/* ===== Utils ===== */
function fmt(n) { const x = Number(n || 0); return x.toFixed(3); }

function rowBase(r) {
  const qtyInPrimary = Number(r.qty || 0) * (r.uomIndex === 2 ? Number(r.conv || 1) : 1);
  return qtyInPrimary * Number(r.price || 0);
}

function formBase() {
  if (state.selectedIndex < 0) return 0;
  const r = state.rows[state.selectedIndex];
  const qty = Number(document.getElementById("qty").value || 0);
  const uomIndex = Number(document.getElementById("uom").value || 1);
  const qtyInPrimary = qty * (uomIndex === 2 ? Number(r.conv || 1) : 1);
  return qtyInPrimary * Number(r.price || 0);
}

function rowTotal(r) {
  const base = rowBase(r);
  const amt  = Math.max(Number(r.discAmt || 0), 0);
  return Math.max(base - Math.min(amt, base), 0);
}

function setEditingLock(locked) {
  ["barcode","pname","pcode","pid"].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.disabled = locked;
  });
  const addR = document.getElementById("modeAdd");
  const inqR = document.getElementById("modeInq");
  if (addR) addR.disabled = locked;
  if (inqR) inqR.disabled = locked;
}

function setBillLocked(locked) {
  state.bill.locked = !!locked;

  const ids = [
    "barcode","pname","pcode","pid",
    "qty","uom","discPct","discAmt","notes",
    "partAmt","custName","custCreate"
  ];
  ids.forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.disabled = locked;
  });

  document.querySelectorAll("input[name='payStatus']").forEach((r) => {
    r.disabled = locked;
  });

  const park = document.getElementById("parkBtn");
  const save = document.getElementById("payPrintBtn");
  if (park) park.disabled = locked;
  if (save) save.disabled = locked;

  // Toggle footer mode: edit vs readonly
  if (footerEditBox && footerViewBox) {
    if (locked) {
      footerEditBox.style.display = "none";
      footerViewBox.style.display = "block";
      updateReadonlyFooter();
    } else {
      footerEditBox.style.display = "block";
      footerViewBox.style.display = "none";
    }
  }

  // Show / hide "new bill" button
  if (closeBillBtn) {
    closeBillBtn.style.display = locked ? "inline-flex" : "none";
  }
}


/* ===== Render bill rows ===== */
const tbody = document.getElementById("billRows");

function renderRows() {
  if (!tbody) return;
  tbody.innerHTML = "";
  state.rows.forEach((r, idx) => {
    const tr = document.createElement("tr");
    tr.dataset.index = idx;
    if (state.editing && idx === state.selectedIndex) tr.classList.add("is-active");

    const tdName = document.createElement("td"); tdName.textContent = r.name;
    const tdQty  = document.createElement("td"); tdQty.className = "col-qty";  tdQty.textContent = fmt(r.qty);
    const tdUnit = document.createElement("td"); tdUnit.className = "col-unit";
    tdUnit.textContent = (r.uomIndex === 2 ? r.u2Label : r.u1Label) || (r.uomIndex === 2 ? "الوحدة الثانية" : "الوحدة الأولى");

    // always show discount as AMOUNT in the middle table
    const tdDisc = document.createElement("td"); tdDisc.className = "col-disc";
    const discAmount = Math.max(Number(r.discAmt || 0), 0);
    tdDisc.textContent = discAmount > 0 ? fmt(discAmount) : "—";

    const tdTotal = document.createElement("td"); tdTotal.className = "col-total"; tdTotal.textContent = fmt(rowTotal(r));
    const tdNotes = document.createElement("td"); tdNotes.textContent = r.notes || "—";
    tr.append(tdName, tdQty, tdUnit, tdDisc, tdTotal, tdNotes);

    tr.addEventListener("click", (e) => {
      e.stopPropagation();
      if (state.bill.locked) return; // لا تعديل على فاتورة محفوظة

      state.selectedIndex = idx;
      loadRowToRight(state.rows[idx]);
      state.editing = true;
      setEditingLock(true);
      renderRows();
      const q = document.getElementById("qty");
      if (q) {
        q.focus();
        try { q.select(); }
        catch {
          try { q.setSelectionRange(0, String(q.value).length); } catch {}
        }
      }
    });

    tr.addEventListener("contextmenu", (e) => {
      e.preventDefault();
      if (state.bill.locked) return;
      state.selectedIndex = idx;
      showContextMenu(e.clientX, e.clientY);
    });

    tbody.appendChild(tr);
  });
  updateGrandTotal();
}

const leftAmtEl  = document.getElementById("leftAmount");
const partAmtEl  = document.getElementById("partAmt");
const custNameEl = document.getElementById("custName");
const custNewEl  = document.getElementById("custCreate");
const payRadios  = document.querySelectorAll("input[name='payStatus']");

// footer edit / readonly + readonly labels
const footerEditBox  = document.getElementById("billFooterEdit");
const footerViewBox  = document.getElementById("billFooterReadonly");
const roTotalEl      = document.getElementById("roTotal");
const roStatusEl     = document.getElementById("roPayStatus");
const roPaidEl       = document.getElementById("roPaid");
const roLeftEl       = document.getElementById("roLeft");
const roCustEl       = document.getElementById("roCustomer");

// close button in mid header
const closeBillBtn   = document.getElementById("posBillClose");


function updateGrandTotal() {
  const t = state.rows.reduce((s, r) => s + rowTotal(r), 0);
  const el = document.getElementById("grandTotal");
  if (el) el.textContent = fmt(t);

  state.bill.totalAmount = t;
  // recompute left based on current paid amount
  state.bill.leftAmount = Math.max(state.bill.totalAmount - state.bill.paidAmount, 0);
  if (leftAmtEl) leftAmtEl.textContent = fmt(state.bill.leftAmount);
}

/* ===== Right panel load/save ===== */
function loadRowToRight(r) {
  document.getElementById("barcode").value = r.lastBarcode || "";
  document.getElementById("pname").value   = r.name || "";
  document.getElementById("pcode").value   = r.lastCode || "";
  document.getElementById("pid").value     = r.id || "";
  document.getElementById("qty").value     = r.qty ?? 1;

  const uom = document.getElementById("uom");
  if (uom) {
    uom.value = String(r.uomIndex || 1);
    const opt2 = [...uom.options].find((o) => o.value === "2");
    if (opt2) opt2.disabled = !r.u2Label;
    if (!r.u2Label && r.uomIndex === 2) uom.value = "1";
  }

  document.getElementById("discPct").value = r.discPct ?? "";
  document.getElementById("discAmt").value = r.discAmt ?? "";
  document.getElementById("notes").value   = r.notes ?? "";

  setQtyPrevSnapshot();
}

function saveRightToRow(idx) {
  const r = state.rows[idx]; if (!r) return;
  const qEl = document.getElementById("qty");
  const uEl = document.getElementById("uom");
  const pEl = document.getElementById("discPct");
  const aEl = document.getElementById("discAmt");

  r.qty      = Number(qEl?.value || 1);
  r.uomIndex = Number(uEl?.value || 1);

  // derive using form base; ensure amount wins and is clamped to base
  const base = formBase();
  const pct  = Math.max(Number(pEl?.value || 0), 0);
  let amt    = Math.max(Number(aEl?.value || 0), 0);

  // If pct is typed but amt is zero, compute amt from pct
  if (amt === 0 && pct > 0) amt = base > 0 ? (base * pct) / 100 : 0;

  // Clamp
  amt = Math.min(amt, Math.max(base, 0));

  r.discAmt  = amt;
  r.discPct  = base > 0 ? (amt / base) * 100 : 0;

  r.notes    = document.getElementById("notes")?.value || "";
}

function clearRightPanel() {
  ["barcode","pname","pcode","pid","qty","discPct","discAmt","notes"].forEach(id => {
    const el = document.getElementById(id); if (el) el.value = "";
  });
  const u = document.getElementById("uom"); if (u) u.value = "1";
}

function goIdle() {
  state.editing = false;
  state.selectedIndex = -1;
  clearRightPanel();
  setEditingLock(false);
  // لا نلمس setBillLocked() هنا، لأنه يستخدم فقط عند التحويل لحالة قراءة
  setTimeout(() => document.getElementById("barcode")?.focus(), 0);
}

function saveEditAndGoIdle() {
  if (state.selectedIndex >= 0) {
    saveRightToRow(state.selectedIndex);
    renderRows();
  }
  goIdle();
}

/* ===== Mode switch ===== */
document.getElementById("modeAdd")?.addEventListener("change", () => { state.mode = "add"; });
document.getElementById("modeInq")?.addEventListener("change", () => { state.mode = "inq"; });

/* ===== Barcode flow ===== */
const bcInput = document.getElementById("barcode");
const bcErr   = document.getElementById("bcError");

function focusBarcodeSoon() {
  if (!state.editing && !state.bill.locked) setTimeout(() => bcInput?.focus(), 0);
}
window.addEventListener("load", focusBarcodeSoon);

bcInput?.addEventListener("keydown", (e) => {
  if (state.bill.locked) return;
  if (e.code === "Enter" || e.code === "NumpadEnter") {
    const val = bcInput.value.trim();
    if (!val) return;
    lookupByBarcode(val);
  }
});

async function lookupByBarcode(code) {
  if (state.bill.locked) return;
  bcErr.style.display = "none";
  try {
    const res = await fetch(`/pos/api/barcode/${encodeURIComponent(code)}/`);
    const j = await res.json();
    if (!j.ok) {
      try { new Audio("data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAESsAACJWAAACABYAAA==").play(); } catch {}
      bcErr.style.display = "block";
      return;
    }
    const p = j.product;

    if (state.mode === "inq") {
      const temp = toRow(p);
      loadRowToRight(temp);
      state.editing = true;
      setEditingLock(true);
      document.getElementById("qty")?.focus();
      return;
    }

    const row = toRow(p);
    row.qty = 1;
    row.uomIndex = Number(p.matched_unit_index || 1);
    row.lastBarcode = code;
    state.rows.push(row);
    state.selectedIndex = state.rows.length - 1;
    renderRows();
    clearRightPanel();   // wipe old right-panel data when adding a new row
    focusBarcodeSoon();
  } catch (err) {
    console.error(err);
  } finally {
    bcInput.value = "";
  }
}

function toRow(p) {
  return {
    id: p.id,
    name: p.name,
    number: p.number,
    price: Number(p.price || 0),                 // price per PRIMARY unit
    qty: 1,
    uomIndex: Number(p.matched_unit_index || 1), // 1 or 2
    conv: Number(p.units?.conversion_factor || 1),
    u1Label: p.units?.primary?.label,
    u2Label: p.units?.secondary?.label,
    discPct: 0,
    discAmt: 0,
    notes: "",
  };
}

/* ===== Right panel key handling ===== */
// Enter inside any right-panel input => save and go idle
["qty","uom","discPct","discAmt","notes"].forEach((id) => {
  const el = document.getElementById(id);
  el?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      saveEditAndGoIdle();
    }
  });
});

// Two-way discount sync and live render
const qtyEl  = document.getElementById("qty");
const uomEl  = document.getElementById("uom");
const pctEl  = document.getElementById("discPct");
const amtEl  = document.getElementById("discAmt");
const notesEl= document.getElementById("notes");

function pushFormToStateAndRender() {
  if (state.selectedIndex < 0 || !state.editing) return;
  saveRightToRow(state.selectedIndex);
  renderRows();
}

// When entering edit mode, remember the original qty to detect changes
function setQtyPrevSnapshot() {
  if (state.selectedIndex < 0) return;
  const r = state.rows[state.selectedIndex];
  if (qtyEl) qtyEl.dataset.prev = String(r.qty ?? 0);
}

// qty changes → recompute the amount from percentage
qtyEl?.addEventListener("input", () => {
  const prev = Number(qtyEl?.dataset.prev ?? "NaN");
  const cur  = Number(qtyEl?.value || 0);

  const hadDiscount = (Number(pctEl?.value || 0) > 0) || (Number(amtEl?.value || 0) > 0);
  const qtyChanged  = (isFinite(prev) ? cur !== prev : true);

  if (qtyChanged && hadDiscount) {
    // nuke both discounts; user must re-enter
    if (pctEl) pctEl.value = "0.000";
    if (amtEl) amtEl.value = "0.000";
  }

  // update prev snapshot
  if (qtyEl) qtyEl.dataset.prev = String(cur);

  pushFormToStateAndRender();
});

// UOM change → resync math, keep discount
uomEl?.addEventListener("change", () => {
  const base = formBase();
  const currentPct = Math.max(Number(pctEl?.value || 0), 0);
  const currentAmt = Math.max(Number(amtEl?.value || 0), 0);

  // If percentage has value, recompute amount from it
  if (pctEl && amtEl) {
    if (currentPct > 0) {
      const amt = base > 0 ? (base * currentPct) / 100 : 0;
      amtEl.value = base ? fmt(amt) : "0.000";
    } else if (currentAmt > 0) {
      const pct = base > 0 ? (currentAmt / base) * 100 : 0;
      pctEl.value = isFinite(pct) ? fmt(pct) : "0.000";
    }
  }
  pushFormToStateAndRender();
});

// typing % → compute amount from %
pctEl?.addEventListener("input", () => {
  const base = formBase();
  const pct = Math.max(Number(pctEl.value || 0), 0);
  const amt = base > 0 ? (base * pct) / 100 : 0;
  if (amtEl) amtEl.value = base ? fmt(amt) : "0.000";
  pushFormToStateAndRender();
});

// Amount → Percentage
amtEl?.addEventListener("input", () => {
  const base = formBase();
  const amt  = Math.max(Number(amtEl.value || 0), 0);
  const pct  = base > 0 ? (amt / base) * 100 : 0;
  if (pctEl) pctEl.value = isFinite(pct) ? fmt(pct) : "0.000";
  pushFormToStateAndRender();
});

// notes live update
notesEl?.addEventListener("input", () => { pushFormToStateAndRender(); });

// If we're editing and the user presses Enter anywhere in the right panel, save.
const rightPanel = document.querySelector(".right-panel");
rightPanel?.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && state.editing) {
    if (!["TEXTAREA"].includes(e.target.tagName)) e.preventDefault();
    saveEditAndGoIdle();
  }
});

/* ===== Bill footer: pay status + customer ===== */

function paymentStatusLabel(value) {
  switch (value) {
    case "full":    return "مدفوعة بالكامل";
    case "none":    return "غير مدفوعة";
    case "partial": return "مدفوعة جزئياً";
    default:        return "غير معروف";
  }
}

function updateReadonlyFooter() {
  if (!footerViewBox) return;

  if (roTotalEl)  roTotalEl.textContent  = fmt(state.bill.totalAmount || 0);
  if (roPaidEl)   roPaidEl.textContent   = fmt(state.bill.paidAmount || 0);
  if (roLeftEl)   roLeftEl.textContent   = fmt(state.bill.leftAmount || 0);
  if (roStatusEl) roStatusEl.textContent = paymentStatusLabel(state.bill.payStatus);
  if (roCustEl)   roCustEl.textContent   = state.bill.customerName || "—";
}



function syncBillFromFooter() {
  // pay status
  payRadios.forEach((r) => {
    if (r.checked) state.bill.payStatus = r.value;
  });

  state.bill.paidAmount = Number(partAmtEl?.value || 0);
  // total already tracked in updateGrandTotal
  state.bill.leftAmount = Math.max(state.bill.totalAmount - state.bill.paidAmount, 0);

  if (leftAmtEl) leftAmtEl.textContent = fmt(state.bill.leftAmount);

  state.bill.customerName = custNameEl?.value.trim() || "";
  state.bill.createNewCustomer = !!custNewEl?.checked;
}

function syncFooterFromBill() {
  payRadios.forEach((r) => {
    r.checked = (r.value === state.bill.payStatus);
  });

  if (partAmtEl) {
    partAmtEl.value = state.bill.paidAmount ? fmt(state.bill.paidAmount) : "";
  }
  if (leftAmtEl) leftAmtEl.textContent = fmt(state.bill.leftAmount || 0);
  if (custNameEl) custNameEl.value = state.bill.customerName || "";
  if (custNewEl) custNewEl.checked = !!state.bill.createNewCustomer;
}

payRadios.forEach((r) => {
  r.addEventListener("change", () => {
    syncBillFromFooter();

    if (state.bill.payStatus === "full") {
      state.bill.paidAmount = state.bill.totalAmount;
      state.bill.leftAmount = 0;
      if (partAmtEl) partAmtEl.value = fmt(state.bill.paidAmount);
      if (leftAmtEl) leftAmtEl.textContent = fmt(0);
    } else if (state.bill.payStatus === "none") {
      state.bill.paidAmount = 0;
      state.bill.leftAmount = state.bill.totalAmount;
      if (partAmtEl) partAmtEl.value = "";
      if (leftAmtEl) leftAmtEl.textContent = fmt(state.bill.leftAmount);
    } else {
      // partial: don't auto-change paid amount, just recompute left
      state.bill.leftAmount = Math.max(state.bill.totalAmount - state.bill.paidAmount, 0);
      if (leftAmtEl) leftAmtEl.textContent = fmt(state.bill.leftAmount);
    }
  });
});

partAmtEl?.addEventListener("input", () => {
  state.bill.paidAmount = Number(partAmtEl.value || 0);
  if (state.bill.paidAmount < 0) state.bill.paidAmount = 0;
  if (state.bill.paidAmount > state.bill.totalAmount) {
    state.bill.paidAmount = state.bill.totalAmount;
    partAmtEl.value = fmt(state.bill.paidAmount);
  }
  state.bill.leftAmount = Math.max(state.bill.totalAmount - state.bill.paidAmount, 0);
  if (leftAmtEl) leftAmtEl.textContent = fmt(state.bill.leftAmount);
});

custNameEl?.addEventListener("input", () => {
  state.bill.customerName = custNameEl.value.trim();
});

custNewEl?.addEventListener("change", () => {
  state.bill.createNewCustomer = !!custNewEl.checked;
});

/* ===== Keyboard: Space → edit latest (or selected) row, regardless of focus ===== */
document.addEventListener("keydown", (e) => {
  if (e.code !== "Space" || e.ctrlKey || e.altKey || e.metaKey) return;

  // if editing, ignore Space entirely (do NOT jump to qty)
  if (state.editing) return;

  // don't hijack when bill is locked
  if (state.bill.locked) return;

  e.preventDefault();
  if (!state.rows.length) return;

  const idx = state.selectedIndex >= 0 ? state.selectedIndex : (state.rows.length - 1);
  state.selectedIndex = idx;
  loadRowToRight(state.rows[idx]);
  state.editing = true;
  setEditingLock(true);
  renderRows();

  const q = document.getElementById("qty");
  if (q) {
    q.focus();
    try {
      q.select(); // highlight the existing value instead of wiping it
    } catch {
      try { q.setSelectionRange(0, String(q.value).length); } catch {}
    }
  }
});


/* ===== Left panel: today's bills ===== */
const leftListEl   = document.getElementById("leftBills");
const leftSearchEl = document.getElementById("leftSearch");

async function loadTodayBills() {
  try {
    const q = leftSearchEl?.value.trim() || "";
    const url = q
      ? `/pos/api/bills/today/?q=${encodeURIComponent(q)}`
      : "/pos/api/bills/today/";
    const res = await fetch(url);
    const j = await res.json();
    if (!j.ok) throw new Error(j.error || "failed");
    state.todayBills = j.bills || [];
    renderLeftBills();
  } catch (err) {
    console.error(err);
  }
}

function renderLeftBills() {
  if (!leftListEl) return;
  leftListEl.innerHTML = "";

  if (!state.todayBills.length) {
    const empty = document.createElement("div");
    empty.className = "muted";
    empty.style.padding = "8px 12px";
    empty.textContent = "لا توجد فواتير اليوم حتى الآن.";
    leftListEl.appendChild(empty);
    return;
  }

  state.todayBills.forEach((b) => {
    const div = document.createElement("button");
    div.type = "button";
    div.className = "btn";
    div.style.cssText = `
      width:100%;
      text-align:right;
      justify-content:space-between;
      margin:2px 0;
      padding:6px 8px;
      font-size:13px;
      display:flex;
      gap:6px;
      border-radius:6px;
    `;

    div.dataset.id = b.id;

    const main = document.createElement("div");
    main.style.display = "flex";
    main.style.flexDirection = "column";

    const title = document.createElement("div");
    title.textContent = b.customer_name || "زبون غير محدد";

    const sub = document.createElement("div");
    sub.className = "muted";
    sub.style.fontSize = "11px";
    const statusLabel =
      b.parked ? "معلقة" :
      b.pay_status === "full" ? "مدفوعة بالكامل" :
      b.pay_status === "none" ? "غير مدفوعة" :
      "مدفوعة جزئياً";
    sub.textContent = `${b.time || ""} • ${statusLabel}`;

    main.appendChild(title);
    main.appendChild(sub);

    const amounts = document.createElement("div");
    amounts.style.textAlign = "left";
    amounts.style.fontSize = "11px";
    amounts.innerHTML = `
      <div>${fmt(b.total_amount || 0)} إجمالي</div>
      <div class="muted">${fmt(b.paid_amount || 0)} مدفوع</div>
    `;

    if (b.parked) {
      div.style.border = "1px dashed var(--accent)";
      div.style.background = "rgba(59,130,246,0.06)";
    }

    div.appendChild(main);
    div.appendChild(amounts);

    div.addEventListener("click", () => {
      loadBillFromBackend(b.id);
    });

    leftListEl.appendChild(div);
  });
}

leftSearchEl?.addEventListener("input", () => {
  loadTodayBills();
});

async function loadBillFromBackend(id) {
  try {
    const res = await fetch(`/pos/api/bill/${id}/`);
    const j = await res.json();
    if (!j.ok) throw new Error(j.error || "failed");

    const b = j.bill;
    resetBillState();

    // fill rows
    state.rows = (b.rows || []).map((r) => ({
      id: r.product_id,
      name: r.name,
      number: r.number,
      price: Number(r.unit_price || 0),
      qty: Number(r.qty || 0),
      uomIndex: Number(r.uom_index || 1),
      conv: Number(r.conv || 1),
      u1Label: r.u1_label || "الوحدة الأولى",
      u2Label: r.u2_label || null,
      discAmt: Number(r.disc_amount || 0),
      discPct: Number(r.disc_pct || 0),
      notes: r.notes || "",
    }));

    state.bill.id           = b.id;
    state.bill.parked       = !!b.parked;
    state.bill.payStatus    = b.pay_status;
    state.bill.paidAmount   = Number(b.paid_amount || 0);
    state.bill.totalAmount  = Number(b.total_amount || 0);
    state.bill.leftAmount   = Math.max(state.bill.totalAmount - state.bill.paidAmount, 0);
    state.bill.customerName = b.customer_name || "";
    state.bill.customerId   = b.customer_id || null;
    state.bill.createdAt    = b.created_at;
    state.selectedBillId    = b.id;

    state.editing = false;
    state.selectedIndex = -1;
    setEditingLock(false);

    renderRows();          // will also recompute total & left based on rows
    syncFooterFromBill();  // push bill state to footer inputs

    // locked if NOT parked (saved/final)
    setBillLocked(!state.bill.parked);

    clearRightPanel();
  } catch (err) {
    console.error(err);
    alert("تعذر تحميل الفاتورة.");
  }
}

/* ===== Global shortcuts (idle only) =====
   - Ctrl+Enter: save bill
   - Ctrl+Space: park bill
   - Enter (idle): focus barcode (scanner-ready)
   - N (idle): focus product-name input
   - Alt+D (idle): delete last row
   - Ctrl+Backspace (idle): confirm delete whole bill
================================================ */
document.addEventListener("keydown", (e) => {
  // If modal is open, ignore global shortcuts (modal traps Enter/Esc)
  if (overlayEl && !overlayEl.hidden) return;

   if (e.key === "Escape" && !e.ctrlKey && !e.altKey && !e.metaKey) {
    if (state.bill.locked) {
      e.preventDefault();
      deleteWholeBill();
      return;
    }
  }

  // only when NOT editing row
  if (state.editing) return;

  // Ctrl+Enter => save bill
  if (e.ctrlKey && !e.altKey && !e.metaKey && (e.code === "Enter" || e.code === "NumpadEnter")) {
    e.preventDefault();
    handleSaveBill();
    return;
  }

  // Ctrl+Space => park bill
  if (e.ctrlKey && !e.altKey && !e.metaKey && e.code === "Space") {
    e.preventDefault();
    handleParkBill();
    return;
  }

  // ENTER => go to barcode field (scanner wait)
  if (e.code === "Enter" || e.code === "NumpadEnter"){
    e.preventDefault();
    const bc = document.getElementById("barcode");
    if (bc && !state.bill.locked){ bc.focus(); try{ bc.select(); }catch{} }
    return;
  }

  // N => jump to product name search
  if (e.code === "KeyN" && !e.ctrlKey && !e.altKey && !e.metaKey){
    e.preventDefault();
    if (state.bill.locked) return;
    const name = document.getElementById("pname");
    if (name){ name.focus(); name.value = ""; }
    return;
  }

  // Alt + D => delete LAST row
  if (e.altKey && e.code === "KeyD"){
    e.preventDefault();
    deleteLastRow();
    // keep focus barcode for rapid flow
    setTimeout(()=> document.getElementById("barcode")?.focus(), 0);
    return;
  }

   // Ctrl + Backspace => delete whole bill ONLY if not locked
  if (e.ctrlKey && !e.altKey && !e.metaKey && e.code === "Backspace") {
    e.preventDefault();

    // If the bill is locked (final/saved), do nothing
    if (state.bill.locked) {
      return;
    }

    // For new or editable bills (unsaved or parked) -> wipe everything and start fresh
    openBillConfirmModal();
    return;
  }

});


/* ===== Name autocomplete ===== */
const nameInput = document.getElementById("pname");
const suggest   = document.getElementById("nameSuggest");
let suggestIdx = -1;
let suggestItems = [];

function showSuggestions(items) {
  suggest.innerHTML = "";
  suggestItems = items; suggestIdx = -1;
  if (!items.length) { suggest.style.display = "none"; return; }
  items.forEach((it, i) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn";
    btn.style.cssText = "width:100%; text-align:right; margin:4px 0;";
    btn.textContent = `${it.name} — ${it.number}`;
    btn.addEventListener("click", () => chooseName(i));
    suggest.appendChild(btn);
  });
  suggest.style.display = "block";
}

function chooseName(i) {
  const it = suggestItems[i]; if (!it || state.bill.locked) return;
  fetch(`/pos/api/lookup/id/${it.id}/`).then(r => r.json()).then(j => {
    if (!j.ok) return;
    const p = j.product;
    if (state.mode === "inq") {
      const temp = toRow(p);
      loadRowToRight(temp);
      state.editing = true;
      setEditingLock(true);
      document.getElementById("qty")?.focus();
    } else {
      const row = toRow(p);
      row.qty = 1;
      row.uomIndex = 1;
      state.rows.push(row);
      state.selectedIndex = state.rows.length - 1;
      renderRows();
      clearRightPanel();
      focusBarcodeSoon();
    }
  });
  nameInput.value = "";
  suggest.style.display = "none";
}

let nameTimer = null;
nameInput?.addEventListener("input", () => {
  const q = nameInput.value.trim();
  if (nameTimer) clearTimeout(nameTimer);
  if (!q) { showSuggestions([]); return; }
  nameTimer = setTimeout(async () => {
    const r = await fetch(`/pos/api/search/name/?q=${encodeURIComponent(q)}&limit=5`);
    const j = await r.json();
    showSuggestions(j.ok ? j.hits : []);
  }, 120);
});

nameInput?.addEventListener("keydown", (e) => {
  if (suggest.style.display === "block") {
    if (e.key === "ArrowDown") { e.preventDefault(); suggestIdx = Math.min(suggestIdx + 1, suggestItems.length - 1); highlightSuggest(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); suggestIdx = Math.max(suggestIdx - 1, 0); highlightSuggest(); }
    else if (e.key === "Enter") { e.preventDefault(); chooseName(suggestIdx >= 0 ? suggestIdx : 0); }
    else if (e.key === "Escape") { suggest.style.display = "none"; }
  }
});
function highlightSuggest() {
  [...suggest.querySelectorAll("button")].forEach((b, i) => {
    b.style.filter = i === suggestIdx ? "brightness(1.2)" : "none";
  });
}

/* ===== Code/ID quick lookup (Enter to add/load) ===== */
document.getElementById("pcode")?.addEventListener("keydown", (e) => {
  if (e.code === "Enter" || e.code === "NumpadEnter") {
    if (state.bill.locked) return;
    const v = e.target.value.trim(); if (!v) return;
    fetch(`/pos/api/lookup/code/${encodeURIComponent(v)}/`).then(r => r.json()).then(j => {
      if (!j.ok) return;
      const p = j.product;
      if (state.mode === "inq") {
        const temp = toRow(p);
        loadRowToRight(temp);
        state.editing = true;
        setEditingLock(true);
        document.getElementById("qty")?.focus();
      } else {
        const row = toRow(p);
        row.qty = 1;
        row.uomIndex = Number(p.matched_unit_index || 1);
        row.lastCode = v;
        state.rows.push(row);
        state.selectedIndex = state.rows.length - 1;
        renderRows();
        clearRightPanel();
      }
    }).finally(() => e.target.value = "");
  }
});

document.getElementById("pid")?.addEventListener("keydown", (e) => {
  if (e.code === "Enter" || e.code === "NumpadEnter") {
    if (state.bill.locked) return;
    const v = parseInt(e.target.value.trim() || "0", 10); if (!v) return;
    fetch(`/pos/api/lookup/id/${v}/`).then(r => r.json()).then(j => {
      if (!j.ok) return;
      const p = j.product;
      if (state.mode === "inq") {
        const temp = toRow(p);
        loadRowToRight(temp);
        state.editing = true;
        setEditingLock(true);
        document.getElementById("qty")?.focus();
      } else {
        const row = toRow(p);
        row.qty = 1;
        row.uomIndex = 1;
        state.rows.push(row);
        state.selectedIndex = state.rows.length - 1;
        renderRows();
        clearRightPanel();
      }
    }).finally(() => e.target.value = "");
  }
});

/* ===== Click outside to save & return to barcode ===== */
document.addEventListener("click", (e) => {
  if (!state.editing) return;
  if (state.bill.locked) return;
  const ignore = e.target.closest("input, textarea, select, button, .btn, [role='button'], #nameSuggest, #posContextMenu");
  if (ignore) return;
  saveEditAndGoIdle();
});

/* ===== Context menu (delete row) ===== */
const ctx = document.getElementById("posContextMenu");
document.getElementById("ctxDeleteRow")?.addEventListener("click", () => {
  if (state.bill.locked) { hideContextMenu(); return; }
  if (state.selectedIndex >= 0) {
    state.rows.splice(state.selectedIndex, 1);
    state.selectedIndex = -1;
    renderRows();
  }
  hideContextMenu();
});
function showContextMenu(x, y) { ctx.style.display = "block"; ctx.style.left = `${x}px`; ctx.style.top = `${y}px`; }
function hideContextMenu() { ctx.style.display = "none"; }
document.addEventListener("click", (e) => { if (ctx.style.display === "block" && !ctx.contains(e.target)) hideContextMenu(); });


/* ===== Delete whole bill modal ===== */
const overlayEl = document.getElementById("billConfirmOverlay");
const confirmYes = document.getElementById("billConfirmYes");
const confirmNo  = document.getElementById("billConfirmNo");
if (overlayEl) overlayEl.hidden = true;

function openBillConfirmModal() {
  if (!overlayEl) return;
  overlayEl.hidden = false;

  // trap: Enter = confirm, Esc = cancel
  function keyTrap(e) {
    if (e.key === "Enter")  { e.preventDefault(); confirmYes?.click(); }
    if (e.key === "Escape") { e.preventDefault(); confirmNo?.click();  }
  }
  overlayEl.dataset.trap = "1";
  document.addEventListener("keydown", keyTrap);

  // cleanup on close
  function cleanup() {
    document.removeEventListener("keydown", keyTrap);
    overlayEl.dataset.trap = "";
  }
  overlayEl.dataset.cleanup = "1";
  overlayEl._cleanup = cleanup;

  // button wiring
  confirmYes?.addEventListener("click", onConfirm, { once: true });
  confirmNo?.addEventListener("click", onCancel, { once: true });

  async function onConfirm() {
    await handleBillDeleteConfirm();
  }
  function onCancel() {
    closeBillConfirmModal();
  }
}

function closeBillConfirmModal() {
  if (!overlayEl) return;
  overlayEl.hidden = true;
  if (overlayEl._cleanup) {
    try { overlayEl._cleanup(); } catch {}
  }
  // return focus to barcode
  setTimeout(() => document.getElementById("barcode")?.focus(), 0);
}

// Decide what "delete bill" actually means (new vs parked)
async function handleBillDeleteConfirm() {
  // Snapshot before we nuke state
  const currentId   = state.bill.id;
  const isParked    = !!state.bill.parked && !!currentId;

  // Close modal first for snappier UX
  closeBillConfirmModal();

  if (isParked) {
    // Parked bill that exists in DB -> delete from backend + refresh list
    try {
      const res = await fetch(`/pos/api/bill/${currentId}/delete/`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": window.CSRF_TOKEN || "",
        },
        body: JSON.stringify({}),
      });
      if (!res.ok) throw new Error("HTTP " + res.status);
      const j = await res.json();
      if (!j.ok) throw new Error(j.error || "Failed to delete bill");
    } catch (err) {
      console.error(err);
      alert("تعذر حذف الفاتورة المعلقة من الخادم.");
    }

    // Regardless of backend result, reset UI and reload today's bills
    deleteWholeBill();
    await loadTodayBills();
  } else {
    // New / unsaved bill (or something without ID/parked) -> just clear UI
    deleteWholeBill();
  }
}


function deleteWholeBill(){
  // clear current bill and start a fresh one
  state.rows = [];
  state.selectedIndex = -1;
  state.editing = false;
  resetBillState();
  setBillLocked(false);
  renderRows();
  clearRightPanel();
  setEditingLock(false);
  syncFooterFromBill();
}

closeBillBtn?.addEventListener("click", () => {
  deleteWholeBill();
  setTimeout(() => document.getElementById("barcode")?.focus(), 0);
});


function deleteLastRow(){
  if (!state.rows.length) return;
  if (state.bill.locked) return;
  const last = state.rows.length - 1;
  state.rows.splice(last, 1);
  state.selectedIndex = -1;
  renderRows();
}

/* ===== Save / park bill API ===== */
const parkBtn     = document.getElementById("parkBtn");
const payPrintBtn = document.getElementById("payPrintBtn");

function validateBillBeforeSave(options) {
  const parked = !!(options && options.parked);

  if (!state.rows.length) {
    alert("لا يمكن حفظ فاتورة فارغة.");
    return false;
  }

  syncBillFromFooter();

  if (state.bill.payStatus !== "full") {
    if (!state.bill.customerName) {
      alert("يجب إدخال اسم الزبون أو إنشاء بطاقة زبون عندما تكون الفاتورة غير مدفوعة بالكامل.");
      if (custNameEl) custNameEl.focus();
      return false;
    }
  }

  if (state.bill.payStatus === "partial") {
    if (state.bill.paidAmount <= 0) {
      alert("الرجاء إدخال المبلغ المدفوع للفاتورة المدفوعة جزئياً.");
      partAmtEl?.focus();
      return false;
    }
    if (state.bill.paidAmount >= state.bill.totalAmount) {
      alert("إذا كان المبلغ المدفوع يساوي أو يتجاوز الإجمالي، استخدم خيار (مدفوع بالكامل).");
      return false;
    }
  }

  if (state.bill.payStatus === "none") {
    state.bill.paidAmount = 0;
    state.bill.leftAmount = state.bill.totalAmount;
  }

  state.bill.parked = parked;

  return true;
}

async function sendBillToBackend(options) {
  const parked = !!(options && options.parked);

  const payload = {
    id: state.bill.id,
    parked: parked,
    pay_status: state.bill.payStatus,
    paid_amount: state.bill.paidAmount,
    total_amount: state.rows.reduce((s,r)=> s + rowTotal(r), 0),
    customer_name: state.bill.customerName || null,
    create_new_customer: state.bill.createNewCustomer,
    rows: state.rows.map((r) => ({
      product_id: r.id,
      name: r.name,
      number: r.number,
      qty: r.qty,
      uom_index: r.uomIndex,
      unit_price: r.price,
      disc_amount: r.discAmt || 0,
      notes: r.notes || "",
    })),
  };

  const url = "/pos/api/bill/save/";
  const res = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-CSRFToken": window.CSRF_TOKEN || "",
    },
    body: JSON.stringify(payload),
  });

  if (!res.ok) throw new Error("HTTP " + res.status);
  const j = await res.json();
  if (!j.ok) throw new Error(j.error || "Failed to save bill");
  return j;
}

async function handleParkBill() {
  if (state.bill.locked) return;
  if (!validateBillBeforeSave({parked:true})) return;

  try {
    const j = await sendBillToBackend({parked:true});
    state.bill.id = j.bill.id;
    state.bill.parked = true;

    await loadTodayBills();
    alert("تم تعليق الفاتورة بنجاح.");
  } catch (err) {
    console.error(err);
    alert("حدث خطأ أثناء تعليق الفاتورة.");
  }
}

async function handleSaveBill() {
  if (state.bill.locked) return;
  if (!validateBillBeforeSave({parked:false})) return;

  try {
    const j = await sendBillToBackend({parked:false});
    state.bill.id = j.bill.id;
    state.bill.parked = false;

    setBillLocked(true);
    await loadTodayBills();

    alert("تم حفظ الفاتورة بنجاح.");
  } catch (err) {
    console.error(err);
    alert("حدث خطأ أثناء حفظ الفاتورة.");
  }
}

parkBtn?.addEventListener("click", (e) => {
  e.preventDefault();
  handleParkBill();
});

payPrintBtn?.addEventListener("click", (e) => {
  e.preventDefault();
  handleSaveBill();
});


// initial render + initial state
resetBillState();
renderRows();
syncFooterFromBill();
loadTodayBills();
