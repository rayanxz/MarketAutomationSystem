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


function getCookie(name) {
  const v = document.cookie.split(";").map(s => s.trim());
  for (const c of v) {
    if (c.startsWith(name + "=")) return decodeURIComponent(c.slice(name.length + 1));
  }
  return "";
}

function csrfToken() {
  return getCookie("csrftoken");
}

function readJsonScript(id) {
  const el = document.getElementById(id);
  if (!el) return null;
  try {
    return JSON.parse(el.textContent || "null");
  } catch (err) {
    console.error("JSON script parse failed:", id, err);
    return null;
  }
}

const POS_CONTAINER_CHOICES = readJsonScript("pos-containers-data") || [];
const POS_FX_SYP_PER_USD = Number(readJsonScript("pos-fx-rate") || 0) || 0;

const CUR_SYP = "SYP";
const CUR_USD = "USD";

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
  const timeFmt = new Intl.DateTimeFormat(locale, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: true,
  });
  const dateFmt = new Intl.DateTimeFormat(locale, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    weekday: "long",
  });

  // NEW: track the logical "day" in JS and refresh today's bills when it flips
  let lastDateKey = null;

  function tick() {
    const now = new Date();
    tEl.textContent = timeFmt.format(now);
    dEl.textContent = dateFmt.format(now);

    // YYYY-MM-DD key so we can detect date change
    const y = now.getFullYear();
    const m = String(now.getMonth() + 1).padStart(2, "0");
    const d = String(now.getDate()).padStart(2, "0");
    const key = `${y}-${m}-${d}`;

    if (lastDateKey && key !== lastDateKey) {
      // Day just changed → refresh today's bills if function exists
      if (typeof loadTodayBills === "function") {
        try { loadTodayBills(); } catch (err) { console.error(err); }
      }
    }
    lastDateKey = key;
  }

  tick();
  setInterval(tick, 1000);
})();


/* ===== Login session closing (backend) ===== */
(function () {
  // global one-shot guard
  if (window.__POS_LOGIN_CLOSE_INIT__) return;
  window.__POS_LOGIN_CLOSE_INIT__ = true;

  let sent = false;

  function sendLoginEndBeacon(reason) {
  try {
    const url = "/pos/api/login/end/";

    const token = csrfToken();

    // Django accepts csrfmiddlewaretoken in POST body (form-encoded)
    const params = new URLSearchParams();
    params.set("reason", reason || "");
    params.set("csrfmiddlewaretoken", token || "");

    const blob = new Blob([params.toString()], {
      type: "application/x-www-form-urlencoded",
    });

    if (navigator.sendBeacon) {
      return navigator.sendBeacon(url, blob);
    }
  } catch {}
  return false;
}


  async function closeLoginOnce(reason) {
  if (sent) return;
  sent = true;

  // try beacon first (most reliable on unload)
  if (sendLoginEndBeacon(reason)) return;

  // fallback
  try {
    await fetch("/pos/api/login/end/", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": csrfToken() || "",
      },
      body: JSON.stringify({ reason }),
      keepalive: true,
    });
  } catch (err) {
    console.error("login end tracking failed:", err);
  }
}


  // Logout button: close session BEFORE django logs out
  const form = document.querySelector("form.logout-form");
  if (form) {
    form.addEventListener("submit", async (e) => {
      e.preventDefault(); // ✅ IMPORTANT: stop navigation until we finish best-effort calls

      // best effort: end shift first (optional)
      try {
        if (window.POS_ACTIVE_SHIFT_ID) {
          await fetch("/pos/api/shift/end/", {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "X-CSRFToken": csrfToken() || "",
            },
            body: JSON.stringify({ id: window.POS_ACTIVE_SHIFT_ID }),
            keepalive: true,
          });
        }
      } catch (err) {
        console.error("shift end on logout failed:", err);
      }

      // ✅ this will use sendBeacon first (fast + reliable)
      await closeLoginOnce("logout_btn");

      // ✅ now continue the real logout
      form.submit();
    });
  }


  // Tab close / refresh
  window.addEventListener("beforeunload", () => {
    closeLoginOnce("tab_close");
  });

  // Hidden (fires earlier sometimes)
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") {
      closeLoginOnce("hidden");
    }
  });
})();

function clearEl(el) {
  while (el && el.firstChild) el.removeChild(el.firstChild);
}

function el(tag, opts = {}) {
  const n = document.createElement(tag);
  if (opts.className) n.className = opts.className;
  if (opts.text != null) n.textContent = String(opts.text);
  if (opts.html != null) n.innerHTML = opts.html; // use ONLY for static trusted HTML
  if (opts.attrs) for (const [k,v] of Object.entries(opts.attrs)) n.setAttribute(k, v);
  return n;
}

function showInsufficientStockError(items) {
  if (!posErrorOverlay || !posErrorMsgEl || !posErrorDetailsEl) {
    alert("لا يمكن حفظ الفاتورة بسبب نفاد المخزون.");
    return;
  }

  posErrorMsgEl.textContent = "لا يمكن حفظ الفاتورة بسبب نفاد المخزون.";
  clearEl(posErrorDetailsEl);

  posErrorDetailsEl.appendChild(
    el("p", { text: "الكمية المتوفرة في المتجر أقل من الكمية المطلوبة لبعض المواد:" })
  );

  const table = el("table", {
    className: "pos-error-table",
    attrs: { style: "width:100%; border-collapse:collapse; margin-top:4px;" }
  });

  const thead = el("thead");
  const trh = el("tr");
  ["المادة", "المتوفر", "المطلوب"].forEach((h) => {
    trh.appendChild(el("th", { text: h, attrs: { style: "border-bottom:1px solid #ddd; padding:4px;" } }));
  });
  thead.appendChild(trh);

  const tbody = el("tbody");

  if (!items || !items.length) {
    const tr = el("tr");
    tr.appendChild(el("td", {
      text: "لا توجد تفاصيل إضافية.",
      attrs: { colspan: "3", style: "padding:4px;" }
    }));
    tbody.appendChild(tr);
  } else {
    items.forEach((it) => {
      const tr = el("tr");
      tr.appendChild(el("td", {
        text: it.product_name || it.product_id,
        attrs: { style: "padding:4px; border-bottom:1px solid #eee;" }
      }));
      tr.appendChild(el("td", {
        text: it.available,
        attrs: { style: "padding:4px; border-bottom:1px solid #eee;" }
      }));
      tr.appendChild(el("td", {
        text: it.needed,
        attrs: { style: "padding:4px; border-bottom:1px solid #eee;" }
      }));
      tbody.appendChild(tr);
    });
  }

  table.appendChild(thead);
  table.appendChild(tbody);
  posErrorDetailsEl.appendChild(table);

  posErrorDetailsEl.appendChild(
    el("p", { text: "قُم بتعديل الكميات أو إدخال فاتورة شراء جديدة ثم حاول مرة أخرى.", attrs: { style: "margin-top:6px;" } })
  );

  posErrorOverlay.hidden = false;
  posErrorOkBtn?.focus();
}


/* ===== Shift box (start/end + stopwatch, with backend) ===== */
(function () {
  const box         = document.getElementById("shiftBox");
  const startBtn    = document.getElementById("shiftStartBtn");
  const activeBox   = document.getElementById("shiftActive");
  const startTimeEl = document.getElementById("shiftStartTime");
  const elapsedEl   = document.getElementById("shiftElapsed");
  const endBtn      = document.getElementById("shiftEndBtn");

  // confirm modal bits (you already have these in HTML)
  const shiftOverlay    = document.getElementById("shiftConfirmOverlay");
  const shiftStartLbl   = document.getElementById("shiftConfirmStart");
  const shiftElapsedLbl = document.getElementById("shiftConfirmElapsed");
  const shiftYesBtn     = document.getElementById("shiftConfirmYes");
  const shiftNoBtn      = document.getElementById("shiftConfirmNo");

  if (!box || !startBtn || !activeBox) return;

  // keep same key for backwards compatibility (old value was plain ISO string)
  const STORAGE_KEY = "posShiftStartISO";

  let startDate = null;
  let timerId   = null;
  let currentShiftId = null;

  // expose active shift globally so bills can use it
  window.POS_ACTIVE_SHIFT_ID = null;

  const timeFmt = new Intl.DateTimeFormat("ar", {
    hour:   "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: true,
  });

  function setActiveUI(isActive) {
    if (isActive) {
      startBtn.style.display  = "none";
      activeBox.style.display = "flex";
    } else {
      activeBox.style.display = "none";
      startBtn.style.display  = "inline-flex";
    }
  }

  function formatElapsed(ms) {
    const totalSec = Math.max(0, Math.floor(ms / 1000));
    const h = String(Math.floor(totalSec / 3600)).padStart(2, "0");
    const m = String(Math.floor((totalSec % 3600) / 60)).padStart(2, "0");
    const s = String(totalSec % 60).padStart(2, "0");
    return `${h}:${m}:${s}`;
  }

  function tick() {
    if (!startDate || !elapsedEl) return;
    const diff = Date.now() - startDate.getTime();
    elapsedEl.textContent = formatElapsed(diff);
  }

  function startTimer() {
    if (timerId) clearInterval(timerId);
    timerId = setInterval(tick, 1000);
    tick(); // instant update
  }

  function stopTimer() {
    if (timerId) {
      clearInterval(timerId);
      timerId = null;
    }
  }

  function saveShiftState() {
    try {
      if (startDate && currentShiftId) {
        const data = {
          started_at: startDate.toISOString(),
          id: currentShiftId,
        };
        localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
      } else {
        localStorage.removeItem(STORAGE_KEY);
      }
    } catch (err) {
      console.warn("shift localStorage failed:", err);
    }
  }

  function activate(startIso, shiftId) {
    startDate = startIso ? new Date(startIso) : new Date();
    currentShiftId = shiftId || currentShiftId || null;
    window.POS_ACTIVE_SHIFT_ID = currentShiftId;

    if (startTimeEl) {
      startTimeEl.textContent = timeFmt.format(startDate);
    }

    setActiveUI(true);
    startTimer();
    saveShiftState();
  }

  function deactivate() {
    stopTimer();
    startDate = null;
    currentShiftId = null;
    window.POS_ACTIVE_SHIFT_ID = null;

    setActiveUI(false);
    if (elapsedEl)   elapsedEl.textContent   = "00:00:00";
    if (startTimeEl) startTimeEl.textContent = "--:--:--";

    saveShiftState();
  }

  async function startShiftOnServer() {
    try {
      const res = await fetch("/pos/api/shift/start/", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": csrfToken() || "",
        },
        body: JSON.stringify({}),
      });
      const j = await res.json();
      if (!res.ok || !j.ok) {
        throw new Error(j.error || ("HTTP " + res.status));
      }
      activate(j.started_at, j.id);
    } catch (err) {
      console.error("shift start failed:", err);
      alert("تعذر بدء الدوام من الخادم. حاول مرة أخرى.");
    }
  }

  async function endShiftOnServer() {
    if (!startDate) {
      deactivate();
      return;
    }

    if (!currentShiftId) {
      // no id? just local stop
      deactivate();
      return;
    }

    try {
      const res = await fetch("/pos/api/shift/end/", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": csrfToken() || "",
        },
        body: JSON.stringify({ id: currentShiftId }),
      });
      const j = await res.json();
      if (!res.ok || !j.ok) {
        throw new Error(j.error || ("HTTP " + res.status));
      }
    } catch (err) {
      console.error("shift end failed:", err);
      alert("تعذر إنهاء الدوام من الخادم، سيتم إيقاف المؤقت محلياً.");
    } finally {
      deactivate();
    }
  }

  // Start shift
  startBtn.addEventListener("click", () => {
    if (startDate) return; // already running
    startShiftOnServer();
  });

  // End shift (with your existing confirmation modal)
  endBtn?.addEventListener("click", (e) => {
    e.preventDefault();
    if (!startDate) return;

    // If modal HTML not present for some reason, just end directly
    if (!shiftOverlay || !shiftYesBtn || !shiftNoBtn) {
      endShiftOnServer();
      return;
    }

    // Fill labels
    if (shiftStartLbl && startTimeEl) {
      shiftStartLbl.textContent = startTimeEl.textContent || "--:--:--";
    }
    if (shiftElapsedLbl && elapsedEl) {
      shiftElapsedLbl.textContent = elapsedEl.textContent || "00:00:00";
    }

    // Show overlay
    shiftOverlay.hidden = false;
    shiftYesBtn.focus();

    // Key trap: Enter = confirm, Esc = cancel
    function keyTrap(ev) {
      if (ev.key === "Enter") {
        ev.preventDefault();
        onYes();
      } else if (ev.key === "Escape") {
        ev.preventDefault();
        onNo();
      }
    }
    document.addEventListener("keydown", keyTrap);

    function cleanup() {
      document.removeEventListener("keydown", keyTrap);
    }

    function closeOverlay() {
      shiftOverlay.hidden = true;
      cleanup();
    }

    async function onYes() {
      closeOverlay();
      await endShiftOnServer(); // 🔥 now hits backend
    }

    function onNo() {
      closeOverlay();
    }

    shiftYesBtn.addEventListener("click", onYes, { once: true });
    shiftNoBtn.addEventListener("click", onNo, { once: true });

    function onBgClick(ev) {
    if (ev.target === shiftOverlay) onNo();
  }
  shiftOverlay.addEventListener("click", onBgClick, { once: true });

  });

  // Restore running shift from localStorage if tab reloads
  let savedRaw = null;
  try {
    savedRaw = localStorage.getItem(STORAGE_KEY);
  } catch (err) {
    console.warn("shift localStorage read failed:", err);
  }

  if (savedRaw) {
    try {
      let data;
      try {
        data = JSON.parse(savedRaw);
      } catch {
        // old format: plain ISO string
        data = { started_at: savedRaw, id: null };
      }
      if (data.started_at) {
        currentShiftId = data.id || null;
        window.POS_ACTIVE_SHIFT_ID = currentShiftId;
        activate(data.started_at, currentShiftId);
      } else {
        setActiveUI(false);
      }
    } catch (err) {
      console.error("shift restore failed:", err);
      deactivate();
    }
  } else {
    setActiveUI(false);
  }
})();



const newBillBtn = document.getElementById("posNewBillBtn");
const posContainerSelect = document.getElementById("posContainerSelect");

function getSelectedContainerCurrencies() {
  const opt = posContainerSelect?.selectedOptions?.[0];
  if (!opt) return [];
  const raw = opt.getAttribute("data-currencies") || "";
  return raw.split(",").map(s => s.trim()).filter(Boolean);
}

function syncContainerFromSelect() {
  const val = posContainerSelect?.value || "";
  state.bill.moneyContainerId = val ? Number(val) : null;
}

function restoreContainerSelection() {
  if (!posContainerSelect) return;
  const key = "posMoneyContainerId";
  const saved = localStorage.getItem(key);
  if (saved && [...posContainerSelect.options].some(o => o.value === saved)) {
    posContainerSelect.value = saved;
    state.bill.moneyContainerId = Number(saved);
  }
}

posContainerSelect?.addEventListener("change", () => {
  syncContainerFromSelect();
  if (state.bill.moneyContainerId) {
    localStorage.setItem("posMoneyContainerId", String(state.bill.moneyContainerId));
  } else {
    localStorage.removeItem("posMoneyContainerId");
  }
});


/* ===== POS error modal (generic) ===== */
const posErrorOverlay    = document.getElementById("posErrorOverlay");
const posErrorMsgEl      = document.getElementById("posErrorMessage");
const posErrorDetailsEl  = document.getElementById("posErrorDetails");
const posErrorOkBtn      = document.getElementById("posErrorOk");

function showPosError(message, detailsHtml) {
  if (!posErrorOverlay || !posErrorMsgEl || !posErrorDetailsEl) {
    // fallback if template missing: ugly but works
    alert(message || "حدث خطأ غير معروف.");
    return;
  }
  posErrorMsgEl.textContent = message || "";
  posErrorDetailsEl.innerHTML = detailsHtml || "";
  posErrorOverlay.hidden = false;

  if (posErrorOkBtn) {
    posErrorOkBtn.focus();
  }
}

function hidePosError() {
  if (posErrorOverlay) {
    posErrorOverlay.hidden = true;
  }
}

// OK button click
if (posErrorOkBtn) {
  posErrorOkBtn.addEventListener("click", () => {
    hidePosError();
  });
}

// Enter / Esc while error modal is open → close it
document.addEventListener("keydown", (e) => {
  if (!posErrorOverlay || posErrorOverlay.hidden) return;
  if (e.key === "Escape" || e.key === "Enter") {
    e.preventDefault();
    hidePosError();
  }
});

/* ===== POS State ===== */
const initialBillState = {
  id: null,
  parked: false,
  locked: false,

  payStatus: "full",      // 'full' | 'none' | 'partial'
  paidAmount: 0,
  totalAmount: 0,
  totalSyp: 0,
  totalUsd: 0,
  settlementMode: "split", // split | all_syp | all_usd
  settlementCurrency: "",
  leftAmount: 0,

  customerId: null,
  customerName: "",
  createNewCustomer: false,
  createdAt: null,

  moneyContainerId: null,
};

const state = {
  mode: "add",              // 'add' | 'inq'
  rows: [],                 // [{id,name,number,qty,uomIndex,price,conv,u1Label,u2Label,discPct,discAmt,notes,lastBarcode,lastCode}]
  selectedIndex: -1,
  editing: false,

  bill: { ...initialBillState },
  todayBills: [],
  selectedBillId: null,

  // NEW: left-panel loading flag
  leftLoading: false,
};


function resetBillState() {
  Object.assign(state.bill, initialBillState);
  syncContainerFromSelect();
}

/* ===== Utils ===== */
function fmt(n) { const x = Number(n || 0); return x.toFixed(2); }
function round2(n) {
  const x = Number(n || 0);
  if (!Number.isFinite(x)) return 0;
  return Math.round((x + Number.EPSILON) * 100) / 100;
}
function money2(n) { return round2(n); }
function fmtMoney(n) { return formatMoney(money2(n)); }
function fmtPrice(n) { const x = Number(n || 0); return formatMoney(x); }

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
  return money2(Math.max(base - Math.min(amt, base), 0));
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
    "partAmt","custName","custCreate","posContainerSelect","saleCurrency"
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

}

/* ===== Stock lookup helpers (store container) ===== */

// call /stock/api/product-stock/?product_id=...
async function fetchStoreStockQty(productId) {
  if (!productId) return 0;

  try {
    const url = `/manager/stock/api/product-stock/?product_id=${encodeURIComponent(productId)}`;
    const res = await fetch(url, { method: "GET", credentials: "same-origin" });
    if (!res.ok) return 0;

    const data = await res.json();
    if (!data.ok) return 0;

    const containers = data.containers || [];
    const storeRow = containers.find(c => c.code === "store");
    if (!storeRow) return 0;

    const qty = parseFloat(storeRow.qty);
    return isNaN(qty) ? 0 : qty;
  } catch (err) {
    console.error("fetchStoreStockQty error:", err);
    return 0;
  }
}

/**
 * Ensure product has >0 qty in store container before adding to bill rows.
 * Used only in "add" mode, not in "inq".
 */
function rowQtyPrimary(r) {
  const qty = Number(r.qty || 0);
  const conv = Number(r.conv || 1);
  return qty * (r.uomIndex === 2 ? conv : 1);
}

function sumOtherRowsPrimary(productId, excludeIdx = null) {
  let s = 0;
  state.rows.forEach((r, i) => {
    if (excludeIdx != null && i === excludeIdx) return;
    if (String(r.id) === String(productId)) s += rowQtyPrimary(r);
  });
  return s;
}


async function ensureProductAvailableInStore(product) {
  if (!product || !product.id) return true;

  const storeQty = await fetchStoreStockQty(product.id);

  // how much already requested in this bill for same product
  const already = sumOtherRowsPrimary(product.id, null);
  const remaining = storeQty - already;

  if (remaining <= 0) {
    const name = product.name || `#${product.id}`;
    showPosError(
      `لا يمكن إضافة المنتج «${name}» لأن الكمية المتبقية في المتجر غير كافية.`,
      `
        <div>المخزون في المتجر: <strong>${fmt(storeQty)}</strong></div>
        <div>مطلوب بالفعل في هذه الفاتورة: <strong>${fmt(already)}</strong></div>
        <div>المتبقي: <strong>${fmt(remaining)}</strong></div>
      `
    );
    return false;
  }
  return true;
}


/**
 * Validate that the edited row's quantity does NOT exceed store stock.
 * Uses current form values (qty + uom) + row.conv to compute primary qty.
 * Returns true if OK, false if not.
 */
async function validateRowStockBeforeSave(idx) {
  const row = state.rows[idx];
  if (!row || !row.id) return true; // nothing to validate

  // read current form values (what cashier just edited)
  const qEl  = document.getElementById("qty");
  const uEl  = document.getElementById("uom");

  const qty      = Number(qEl?.value || 0);
  const uomIndex = Number(uEl?.value || 1);
  const conv     = Number(row.conv || 1);

  // convert to primary units like inventory does
  const qtyPrimary = qty * (uomIndex === 2 ? conv : 1);

  // if zero/negative, just allow (they might be clearing row / making it tiny)
  if (qtyPrimary <= 0) {
    return true;
  }

  // fetch available qty in store
  const storeQty = await fetchStoreStockQty(row.id);
  const alreadyOther = sumOtherRowsPrimary(row.id, idx);
  const remaining = storeQty - alreadyOther;

  // if no stock data, just allow – backend will still block on finalize if needed
  if (round2(qtyPrimary) > round2(remaining)) {
  const name = row.name || `#${row.id}`;

  showPosError(
    `لا يمكن تحديد كمية أكبر من المخزون للمنتج «${name}».`,
    `
      <div>المخزون في المتجر: <strong>${fmt(storeQty)}</strong></div>
      <div>مطلوب في سطور أخرى: <strong>${fmt(alreadyOther)}</strong></div>
      <div>المتبقي لهذه الإضافة: <strong>${fmt(remaining)}</strong></div>
      <div>المطلوب في هذا السطر: <strong>${fmt(qtyPrimary)}</strong></div>
    `
  );
  return false;
  }

  return true;
}

/* ===== Product inquiry overlay ===== */
const inqOverlayEl   = document.getElementById("posInquiryOverlay");
const inqNameEl      = document.getElementById("posInqName");
const inqPriceEl     = document.getElementById("posInqPrice");
const inqStoreQtyEl  = document.getElementById("posInqStoreQty");
const inqU1El        = document.getElementById("posInqU1");
const inqU2El        = document.getElementById("posInqU2");
const inqConvEl      = document.getElementById("posInqConv");
const inqCloseBtn    = document.getElementById("posInqClose");

function closeInquiryOverlay() {
  if (!inqOverlayEl) return;
  inqOverlayEl.hidden = true;
  // رجّع الفوكس للباركود للسكّانر
  setTimeout(() => barcode?.focus(), 0);
}

async function openInquiryOverlay(product) {
  if (!inqOverlayEl || !product) return;

  // 1) كمية المتجر
  let storeQty = 0;
  try {
    storeQty = await fetchStoreStockQty(product.id);
  } catch (err) {
    console.error("inquiry store qty error:", err);
  }
  if (!isFinite(storeQty)) storeQty = 0;

  // 2) الوحدات + عامل التحويل
  const u1Label = product.units?.primary?.label || "الوحدة الأولى";
  const u2Label = product.units?.secondary?.label || "";
  const convRaw = product.units?.conversion_factor;
  const conv    = convRaw != null ? String(convRaw) : null;

  // 3) السعر: يعتمد فقط على defaults per-currency + effective currency
  const priceCur = (product.effective_default_sale_currency || CUR_SYP).toUpperCase();
  const priceNum = (
    priceCur === CUR_USD
      ? Number(product.default_price_usd || 0)
      : Number(product.default_price_syp || 0)
  );

  if (inqNameEl)     inqNameEl.textContent     = product.name || "";
  if (inqPriceEl)    inqPriceEl.textContent    = `${fmtPrice(priceNum)} ${priceCur} / ${u1Label}`;
  if (inqStoreQtyEl) inqStoreQtyEl.textContent = fmt(storeQty);
  if (inqU1El)       inqU1El.textContent       = u1Label;
  if (inqU2El)       inqU2El.textContent       = u2Label || "—";
  if (inqConvEl)     inqConvEl.textContent     = conv || "—";

  inqOverlayEl.hidden = false;
  inqCloseBtn?.focus();
}

// زر إغلاق
inqCloseBtn?.addEventListener("click", (e) => {
  e.preventDefault();
  closeInquiryOverlay();
});

// كليك برا المودال → إغلاق
inqOverlayEl?.addEventListener("click", (e) => {
  if (e.target === inqOverlayEl) {
    closeInquiryOverlay();
  }
});

// Enter / Esc ونافذة الاستعلام مفتوحة → إغلاق
document.addEventListener("keydown", (e) => {
  if (!inqOverlayEl || inqOverlayEl.hidden) return;
  if (e.key === "Escape" || e.key === "Enter") {
    e.preventDefault();
    closeInquiryOverlay();
  }
});

/* ===== Render bill rows ===== */
const tbody = document.getElementById("billRows");

function renderRows() {
  if (!tbody) return;
  tbody.innerHTML = "";
  state.rows.forEach((r, idx) => {
    const tr = document.createElement("tr");
    tr.dataset.index = idx;
    if (state.editing && idx === state.selectedIndex) tr.classList.add("is-active");

    const tdName = document.createElement("td"); 
    tdName.textContent = r.name;

    const tdQty  = document.createElement("td"); 
    tdQty.className = "col-qty";  
    tdQty.textContent = fmt(r.qty);

    const tdUnit = document.createElement("td"); 
    tdUnit.className = "col-unit";
    tdUnit.textContent = (r.uomIndex === 2 ? r.u2Label : r.u1Label) || (r.uomIndex === 2 ? "الوحدة الثانية" : "الوحدة الأولى");

    const tdCur = document.createElement("td");
    tdCur.className = "col-currency";
    tdCur.textContent = r.currency || CUR_SYP;

    // NEW: unit price (always price per الوحدة الأساسية)
    const tdPrice = document.createElement("td");
    tdPrice.className = "col-unitprice";
    tdPrice.textContent = fmtPrice(r.price);

    // always show discount as AMOUNT in the middle table
    const tdDisc = document.createElement("td"); 
    tdDisc.className = "col-disc";
    const discAmount = Math.max(Number(r.discAmt || 0), 0);
    tdDisc.textContent = discAmount > 0 ? fmtMoney(discAmount) : "—";

    const tdTotal = document.createElement("td"); 
    tdTotal.className = "col-total"; 
    tdTotal.textContent = fmtMoney(rowTotal(r));

    const tdNotes = document.createElement("td"); 
    tdNotes.textContent = r.notes || "—";

    tr.append(tdName, tdQty, tdUnit, tdCur, tdPrice, tdDisc, tdTotal, tdNotes);


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
const totalSypEl = document.getElementById("totalSyp");
const totalUsdEl = document.getElementById("totalUsd");
const settlementModeEl = document.getElementById("settlementMode");
const settlementTotalEl = document.getElementById("settlementTotal");
const settleAllSypBtn = document.getElementById("settleAllSyp");
const settleAllUsdBtn = document.getElementById("settleAllUsd");

// footer edit / readonly + readonly labels
const footerEditBox  = document.getElementById("billFooterEdit");
const footerViewBox  = document.getElementById("billFooterReadonly");
const roTotalEl      = document.getElementById("roTotal");
const roStatusEl     = document.getElementById("roPayStatus");
const roPaidEl       = document.getElementById("roPaid");
const roLeftEl       = document.getElementById("roLeft");
const roCustEl       = document.getElementById("roCustomer");

function totalsByCurrency() {
  let syp = 0;
  let usd = 0;
  state.rows.forEach((r) => {
    const t = rowTotal(r);
    if ((r.currency || CUR_SYP) === CUR_USD) usd = money2(usd + t);
    else syp = money2(syp + t);
  });
  return { syp: money2(syp), usd: money2(usd) };
}

function calcSettlementTotal(totalSyp, totalUsd) {
  let mode = state.bill.settlementMode || "split";
  let currency = "";
  let total = totalSyp + totalUsd;

  if (mode === "all_syp") {
    if (POS_FX_SYP_PER_USD > 0) {
      total = totalSyp + (totalUsd * POS_FX_SYP_PER_USD);
      currency = CUR_SYP;
    } else {
      mode = "split";
    }
  } else if (mode === "all_usd") {
    if (POS_FX_SYP_PER_USD > 0) {
      total = totalUsd + (totalSyp / POS_FX_SYP_PER_USD);
      currency = CUR_USD;
    } else {
      mode = "split";
    }
  }

  state.bill.settlementMode = mode;
  state.bill.settlementCurrency = currency;
  return { total: money2(total), mode, currency };
}

function updateGrandTotal() {
  const totals = totalsByCurrency();
  const settlement = calcSettlementTotal(totals.syp, totals.usd);

  const el = document.getElementById("grandTotal");
  if (el) el.textContent = fmtMoney(settlement.total);

  if (totalSypEl) totalSypEl.textContent = fmtMoney(totals.syp);
  if (totalUsdEl) totalUsdEl.textContent = fmtMoney(totals.usd);
  if (settlementModeEl) settlementModeEl.textContent = settlement.mode;
  if (settlementTotalEl) settlementTotalEl.textContent = fmtMoney(settlement.total);

  state.bill.totalAmount = money2(settlement.total);
  state.bill.totalSyp = money2(totals.syp);
  state.bill.totalUsd = money2(totals.usd);

  // clamp paid to total whenever total changes
  state.bill.paidAmount = money2(Math.max(0, Math.min(state.bill.paidAmount, state.bill.totalAmount)));

  state.bill.leftAmount = money2(Math.max(state.bill.totalAmount - state.bill.paidAmount, 0));
  if (leftAmtEl) leftAmtEl.textContent = fmtMoney(state.bill.leftAmount);
}

function toggleSettlementMode(mode) {
  if (state.bill.settlementMode === mode) {
    state.bill.settlementMode = "split";
    state.bill.settlementCurrency = "";
  } else {
    state.bill.settlementMode = mode;
    state.bill.settlementCurrency = (mode === "all_usd") ? CUR_USD : CUR_SYP;
  }
  updateGrandTotal();
}

settleAllSypBtn?.addEventListener("click", () => toggleSettlementMode("all_syp"));
settleAllUsdBtn?.addEventListener("click", () => toggleSettlementMode("all_usd"));

if (POS_FX_SYP_PER_USD <= 0) {
  if (settleAllSypBtn) settleAllSypBtn.disabled = true;
  if (settleAllUsdBtn) settleAllUsdBtn.disabled = true;
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

  updateCurrencySelectForRow(r);

  document.getElementById("discPct").value = r.discPct ?? "";
  document.getElementById("discAmt").value = r.discAmt ?? "";
  document.getElementById("notes").value   = r.notes ?? "";

  setQtyPrevSnapshot();
}

function updateCurrencySelectForRow(r) {
  const curEl = document.getElementById("saleCurrency");
  if (!curEl) return;

  const allowSyp = (r.allowSypSales !== false);
  const allowUsd = (r.allowUsdSales !== false) && Number(r.defaultPriceUsd || 0) > 0;

  const optSyp = [...curEl.options].find(o => o.value === CUR_SYP);
  const optUsd = [...curEl.options].find(o => o.value === CUR_USD);
  if (optSyp) optSyp.disabled = !allowSyp;
  if (optUsd) optUsd.disabled = !allowUsd;

  let cur = (r.currency || CUR_SYP).toUpperCase();
  if (cur === CUR_USD && !allowUsd && allowSyp) cur = CUR_SYP;
  if (cur === CUR_SYP && !allowSyp && allowUsd) cur = CUR_USD;

  curEl.value = cur;
  curEl.disabled = (allowSyp && !allowUsd) || (!allowSyp && allowUsd);
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

  r.discAmt  = money2(amt);
  r.discPct  = base > 0 ? (amt / base) * 100 : 0;

  r.notes    = document.getElementById("notes")?.value || "";
}

function clearRightPanel() {
  ["barcode","pname","pcode","pid","qty","discPct","discAmt","notes"].forEach(id => {
    const el = document.getElementById(id); if (el) el.value = "";
  });
  const u = document.getElementById("uom"); if (u) u.value = "1";
  const cur = document.getElementById("saleCurrency"); if (cur) { cur.value = CUR_SYP; cur.disabled = false; }
}

function goIdle() {
  state.editing = false;
  state.selectedIndex = -1;
  clearRightPanel();
  setEditingLock(false);
  // لا نلمس setBillLocked() هنا، لأنه يستخدم فقط عند التحويل لحالة قراءة
  setTimeout(() => document.getElementById("barcode")?.focus(), 0);
}

async function saveEditAndGoIdle() {
  if (state.selectedIndex >= 0) {
    // 🔍 check stock for this row before saving
    const ok = await validateRowStockBeforeSave(state.selectedIndex);
    if (!ok) {
      // stay in edit mode, keep form as-is
      return;
    }

    // OK → save edits and re-render
    saveRightToRow(state.selectedIndex);
    renderRows();
  }
  goIdle();
}

/* ===== Mode switch ===== */
document.getElementById("modeAdd")?.addEventListener("change", () => { state.mode = "add"; });
document.getElementById("modeInq")?.addEventListener("change", () => { state.mode = "inq"; });

function togglePosMode() {
  const addR = document.getElementById("modeAdd");
  const inqR = document.getElementById("modeInq");
  if (!addR || !inqR) return;

  if (addR.checked) {
    inqR.checked = true;
    state.mode = "inq";
    // fire change for any listeners, just in case
    inqR.dispatchEvent(new Event("change"));
  } else {
    addR.checked = true;
    state.mode = "add";
    addR.dispatchEvent(new Event("change"));
  }
}

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
  if (bcErr) bcErr.style.display = "none";

  try {
    const res = await fetch(`/pos/api/barcode/${encodeURIComponent(code)}/`);
    const j = await res.json();
    if (!j.ok) {
      try { new Audio("data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAESsAACJWAAACABYAAA==").play(); } catch {}
      if (bcErr) bcErr.style.display = "block";
      return;
    }

    const p = j.product;

    // INQ mode
    if (state.mode === "inq") {
      await openInquiryOverlay(p);
      return;
    }

    // Determine unit index that this barcode matched
    const matchedUomIndex = Number(p.matched_unit_index || 1);

    // Find existing row with same product + same uomIndex
    const existingIdx = state.rows.findIndex(r =>
      String(r.id) === String(p.id) &&
      Number(r.uomIndex || 1) === matchedUomIndex
    );

    // Helper: how much 1 scan adds in PRIMARY units
    const conv = Number(p.units?.conversion_factor || 1);
    const addPrimary = 1 * (matchedUomIndex === 2 ? conv : 1);

    // Fetch store qty once
    const storeQty = await fetchStoreStockQty(p.id);

    // How much already requested (ALL rows for that product)
    const already = sumOtherRowsPrimary(p.id, null);

    // If existing row -> we are increasing total requested by addPrimary
    const remainingAfter = storeQty - (already + addPrimary);

    if (round2(remainingAfter) < 0) {
      const name = p.name || `#${p.id}`;
      showPosError(
        `لا يمكن زيادة كمية المنتج «${name}» لأن المخزون في المتجر غير كافٍ.`,
        `
          <div>المخزون في المتجر: <strong>${fmt(storeQty)}</strong></div>
          <div>مطلوب بالفعل في هذه الفاتورة: <strong>${fmt(already)}</strong></div>
          <div>الإضافة المطلوبة الآن: <strong>${fmt(addPrimary)}</strong></div>
          <div>المتبقي بعد الإضافة: <strong>${fmt(remainingAfter)}</strong></div>
        `
      );
      return;
    }

    // OK: increment if exists
    if (existingIdx >= 0) {
      state.rows[existingIdx].qty = Number(state.rows[existingIdx].qty || 0) + 1;
      state.selectedIndex = existingIdx;
      renderRows();
      clearRightPanel();
      focusBarcodeSoon();
      return;
    }

    // Otherwise add a new row
    const row = toRow(p);
    row.qty = 1;
    row.uomIndex = matchedUomIndex;
    row.lastBarcode = code;

    state.rows.push(row);
    state.selectedIndex = state.rows.length - 1;
    renderRows();
    clearRightPanel();
    focusBarcodeSoon();

  } catch (err) {
    console.error(err);
  } finally {
    bcInput.value = "";
  }
}


function toRow(p) {
  const allowSyp = !!p.allow_syp_sales;
  const allowUsd = !!p.allow_usd_sales;
  const effCur = (p.effective_default_sale_currency || CUR_SYP).toUpperCase();

  const priceSyp = Number(p.default_price_syp || 0);
  const priceUsd = Number(p.default_price_usd || 0);

  let cur = effCur;
  if (cur === CUR_USD && (!allowUsd || priceUsd <= 0) && allowSyp) cur = CUR_SYP;
  if (cur === CUR_SYP && !allowSyp && allowUsd) cur = CUR_USD;

  const price = (cur === CUR_USD) ? priceUsd : priceSyp;

  return {
    id: p.id,
    name: p.name,
    number: p.number,
    price: price,                                // price per PRIMARY unit
    currency: cur,
    qty: 1,
    uomIndex: Number(p.matched_unit_index || 1), // 1 or 2
    conv: Number(p.units?.conversion_factor || 1),
    u1Label: p.units?.primary?.label,
    u2Label: p.units?.secondary?.label,
    allowSypSales: allowSyp,
    allowUsdSales: allowUsd,
    defaultPriceSyp: priceSyp,
    defaultPriceUsd: priceUsd,
    discPct: 0,
    discAmt: 0,
    notes: "",
  };
}

/* ===== Right panel key handling ===== */
// Enter inside any right-panel input => save and go idle
["qty","uom","discPct","discAmt","notes"].forEach((id) => {
  const el = document.getElementById(id);
  el?.addEventListener("keydown", async (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      await saveEditAndGoIdle();
    }
  });
});

// Two-way discount sync and live render
const qtyEl  = document.getElementById("qty");
const uomEl  = document.getElementById("uom");
const pctEl  = document.getElementById("discPct");
const amtEl  = document.getElementById("discAmt");
const notesEl= document.getElementById("notes");
const curEl  = document.getElementById("saleCurrency");

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
    if (pctEl) pctEl.value = "0.00";
    if (amtEl) amtEl.value = "0.00";
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
      amtEl.value = base ? fmtMoney(amt) : "0.00";
    } else if (currentAmt > 0) {
      const pct = base > 0 ? (currentAmt / base) * 100 : 0;
      pctEl.value = isFinite(pct) ? fmtMoney(pct) : "0.00";
    }
  }
  pushFormToStateAndRender();
});

// typing % → compute amount from %
pctEl?.addEventListener("input", () => {
  const base = formBase();
  const pct = Math.max(Number(pctEl.value || 0), 0);
  const amt = base > 0 ? (base * pct) / 100 : 0;
  if (amtEl) amtEl.value = base ? fmtMoney(amt) : "0.00";
  pushFormToStateAndRender();
});

// Amount → Percentage
amtEl?.addEventListener("input", () => {
  const base = formBase();
  const amt  = Math.max(Number(amtEl.value || 0), 0);
  const pct  = base > 0 ? (amt / base) * 100 : 0;
  if (pctEl) pctEl.value = isFinite(pct) ? fmtMoney(pct) : "0.00";
  pushFormToStateAndRender();
});

curEl?.addEventListener("change", () => {
  if (state.selectedIndex < 0 || !state.editing) return;
  const r = state.rows[state.selectedIndex];
  if (!r) return;

  let next = (curEl.value || CUR_SYP).toUpperCase();
  const allowSyp = (r.allowSypSales !== false);
  const allowUsd = (r.allowUsdSales !== false) && Number(r.defaultPriceUsd || 0) > 0;

  if (next === CUR_USD && !allowUsd) next = CUR_SYP;
  if (next === CUR_SYP && !allowSyp) next = CUR_USD;

  r.currency = next;
  r.price = (next === CUR_USD) ? Number(r.defaultPriceUsd || 0) : Number(r.defaultPriceSyp || 0);

  // reset discounts on currency switch
  r.discAmt = 0;
  r.discPct = 0;
  if (pctEl) pctEl.value = "0.00";
  if (amtEl) amtEl.value = "0.00";

  renderRows();
});

// notes live update
notesEl?.addEventListener("input", () => { pushFormToStateAndRender(); });

// If we're editing and the user presses Enter anywhere in the right panel, save.
const rightPanel = document.querySelector(".right-panel");
rightPanel?.addEventListener("keydown", async (e) => {
  if (e.key === "Enter" && state.editing) {
    if (!["TEXTAREA"].includes(e.target.tagName)) e.preventDefault();
    await saveEditAndGoIdle();
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

  if (roTotalEl)  roTotalEl.textContent  = fmtMoney(state.bill.totalAmount || 0);
  if (roPaidEl)   roPaidEl.textContent   = fmtMoney(state.bill.paidAmount || 0);
  if (roLeftEl)   roLeftEl.textContent   = fmtMoney(state.bill.leftAmount || 0);
  if (roStatusEl) roStatusEl.textContent = paymentStatusLabel(state.bill.payStatus);
  if (roCustEl)   roCustEl.textContent   = state.bill.customerName || "—";
}

function syncBillFromFooter() {
  // pay status
  payRadios.forEach((r) => {
    if (r.checked) state.bill.payStatus = r.value;
  });

  state.bill.paidAmount = money2(Number(partAmtEl?.value || 0));
  // total already tracked in updateGrandTotal
  state.bill.leftAmount = money2(Math.max(state.bill.totalAmount - state.bill.paidAmount, 0));

  if (leftAmtEl) leftAmtEl.textContent = fmtMoney(state.bill.leftAmount);

  state.bill.customerName = custNameEl?.value.trim() || "";
  state.bill.createNewCustomer = !!custNewEl?.checked;
  syncContainerFromSelect();
}

function syncFooterFromBill() {
  payRadios.forEach((r) => {
    r.checked = (r.value === state.bill.payStatus);
  });

  if (partAmtEl) {
    partAmtEl.value = state.bill.paidAmount ? fmtMoney(state.bill.paidAmount) : "";
  }
  if (leftAmtEl) leftAmtEl.textContent = fmtMoney(state.bill.leftAmount || 0);
  if (custNameEl) custNameEl.value = state.bill.customerName || "";
  if (custNewEl) custNewEl.checked = !!state.bill.createNewCustomer;
  if (posContainerSelect) {
    posContainerSelect.value = state.bill.moneyContainerId ? String(state.bill.moneyContainerId) : "";
  }
}

payRadios.forEach((r) => {
  r.addEventListener("change", () => {
    // only set status — DO NOT overwrite the textbox
    payRadios.forEach((x) => { if (x.checked) state.bill.payStatus = x.value; });

    // keep "math" behavior: paidAmount follows textbox
    const typed = Number(partAmtEl?.value || 0);
    state.bill.paidAmount = money2(Math.max(0, Math.min(typed, state.bill.totalAmount)));

    // left always reflects typed amount
    state.bill.leftAmount = money2(Math.max(state.bill.totalAmount - state.bill.paidAmount, 0));
    if (leftAmtEl) leftAmtEl.textContent = fmtMoney(state.bill.leftAmount);
  });
});


partAmtEl?.addEventListener("input", () => {
  state.bill.paidAmount = money2(Number(partAmtEl.value || 0));
  if (state.bill.paidAmount < 0) state.bill.paidAmount = 0;
  if (state.bill.paidAmount > state.bill.totalAmount) {
    state.bill.paidAmount = state.bill.totalAmount;
    partAmtEl.value = fmtMoney(state.bill.paidAmount);
  }
  state.bill.leftAmount = money2(Math.max(state.bill.totalAmount - state.bill.paidAmount, 0));
  if (leftAmtEl) leftAmtEl.textContent = fmtMoney(state.bill.leftAmount);
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

  // let Space behave normally ONLY inside customer name + product name
  const t = e.target;
  if (
    t &&
    (t.tagName === "INPUT" || t.tagName === "TEXTAREA") &&
    ["custName", "pname"].includes(t.id)
  ) {
    // no preventDefault, no row editing — just type the space
    return;
  }

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
    // 🔥 mark as loading and render skeleton
    state.leftLoading = true;
    renderLeftBills();

    const q = leftSearchEl?.value.trim() || "";
    const url = q
      ? `/pos/api/bills/today/?q=${encodeURIComponent(q)}`
      : "/pos/api/bills/today/";


    const res = await fetch(url);
    const j = await res.json();
    if (!j.ok) throw new Error(j.error || "failed");

    state.todayBills = j.bills || [];
  } catch (err) {
    console.error(err);
  } finally {
    // ✅ done loading
    state.leftLoading = false;
    renderLeftBills();
  }
}

function renderLeftBills() {
  if (!leftListEl) return;
  leftListEl.innerHTML = "";

  // NEW: loading skeleton when refreshing
  if (state.leftLoading) {
    for (let i = 0; i < 3; i++) {
      const sk = document.createElement("div");
      sk.style.cssText = `
        padding:8px 12px;
        margin:2px 0;
        border-radius:6px;
        background:linear-gradient(90deg, #e5e7eb 0%, #f3f4f6 50%, #e5e7eb 100%);
        background-size:200% 100%;
        animation: pos-skeleton 1.2s infinite linear;
      `;
      leftListEl.appendChild(sk);
    }
    // tiny inline keyframes (only once)
    if (!document.getElementById("posSkeletonStyle")) {
      const style = document.createElement("style");
      style.id = "posSkeletonStyle";
      style.textContent = `
        @keyframes pos-skeleton {
          0% { background-position: 200% 0; }
          100% { background-position: -200% 0; }
        }
      `;
      document.head.appendChild(style);
    }
    return;
  }

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

    div.dataset.id = String(b.id);

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
      <div>${fmtMoney(b.total_amount || 0)} إجمالي</div>
      <div class="muted">${fmtMoney(b.paid_amount || 0)} مدفوع</div>
    `;

    // base parked styling (gets enhanced by pos_customers.js override)
    if (b.parked) {
      div.style.border = "1px dashed #dc8c53ff"; // orange border
      div.style.background = "rgba(255,180,80,0.15)"; // light orange
    }

    div.appendChild(main);
    div.appendChild(amounts);

    div.addEventListener("click", () => {
      loadBillFromBackend(b.id);
    });

    const activeId = state.selectedBillId ? String(state.selectedBillId) : null;
    const isActive = String(b.id) === activeId;

    if (isActive) {
      div.style.background = "var(--accent-light)";
      div.style.border = "2px solid var(--accent)";
    }

    leftListEl.appendChild(div);
  });
}

let leftSearchTimer = null;
leftSearchEl?.addEventListener("input", () => {
  clearTimeout(leftSearchTimer);
  leftSearchTimer = setTimeout(loadTodayBills, 200);
});


async function loadBillFromBackend(id) {
  try {
    const res = await fetch(`/pos/api/bill/${id}/`);
    const j = await res.json();
    if (!j.ok) throw new Error(j.error || "failed");

    const b = j.bill;
    resetBillState();

    const rawRows = b.rows || [];

    // 🔥 Hydrate each row with product info (conv + unit labels)
    const hydratedRows = await Promise.all(
      rawRows.map(async (r) => {
        let conv = 1;
        let u1Label = "الوحدة الأولى";
        let u2Label = null;
        let price = Number(r.unit_price || 0);
        let allowSyp = true;
        let allowUsd = true;
        let priceSyp = 0;
        let priceUsd = 0;
        let effCur = (r.currency || CUR_SYP).toUpperCase();

        try {
          const pRes = await fetch(`/pos/api/lookup/id/${r.product_id}/`);
          const pJson = await pRes.json();
          if (pJson.ok && pJson.product) {
            const p = pJson.product;
            // conv from product payload
            const convRaw = p.units?.conversion_factor;
            if (convRaw != null) {
              conv = Number(convRaw) || 1;
            }
            u1Label = p.units?.primary?.label || "الوحدة الأولى";
            u2Label = p.units?.secondary?.label || null;

            // If for some reason unit_price is 0, fall back to explicit default price.
            if (!price) {
              const fallbackCur = ((r.currency || p.effective_default_sale_currency || CUR_SYP) + "").toUpperCase();
              price = fallbackCur === CUR_USD
                ? Number(p.default_price_usd || 0)
                : Number(p.default_price_syp || 0);
            }

            allowSyp = !!p.allow_syp_sales;
            allowUsd = !!p.allow_usd_sales;
            priceSyp = Number(p.default_price_syp || 0);
            priceUsd = Number(p.default_price_usd || 0);
            if (!r.currency) {
              effCur = (p.effective_default_sale_currency || CUR_SYP).toUpperCase();
            }
          }
        } catch (err) {
          console.error("hydrate row product fetch failed:", err);
        }

        return {
          id: r.product_id,
          name: r.name,
          number: r.number,
          price: money2(price),
          currency: effCur,
          qty: Number(r.qty || 0),
          uomIndex: Number(r.uom_index || 1),
          conv: conv,
          u1Label: u1Label,
          u2Label: u2Label,
          allowSypSales: allowSyp,
          allowUsdSales: allowUsd,
          defaultPriceSyp: priceSyp,
          defaultPriceUsd: priceUsd,
          discAmt: money2(Number(r.disc_amount || 0)),
          discPct: Number(r.disc_pct || 0),
          notes: r.notes || "",
        };
      })
    );

    // fill rows
    state.rows = hydratedRows;

    state.bill.id           = b.id;
    state.bill.parked       = !!b.parked;
    state.bill.payStatus    = b.pay_status;
    state.bill.paidAmount   = money2(Number(b.paid_amount || 0));
    state.bill.totalAmount  = money2(Number(b.total_amount || 0));
    state.bill.totalSyp     = money2(Number(b.total_syp || 0));
    state.bill.totalUsd     = money2(Number(b.total_usd || 0));
    state.bill.settlementMode = b.settlement_mode || "split";
    state.bill.settlementCurrency = b.settlement_currency || "";
    state.bill.leftAmount   = money2(Math.max(state.bill.totalAmount - state.bill.paidAmount, 0));
    state.bill.customerName = b.customer_name || "";
    state.bill.customerId   = b.customer_id || null;
    state.bill.createdAt    = b.created_at;
    state.bill.moneyContainerId = b.money_container_id || null;
    state.selectedBillId    = String(b.id);

    state.editing = false;
    state.selectedIndex = -1;
    setEditingLock(false);

    renderRows();         // will also recompute total & left based on rows
    syncFooterFromBill(); // push bill state to footer inputs

    // locked if NOT parked (saved/final)
    setBillLocked(!state.bill.parked);

    clearRightPanel();

    // update left list highlight so currently opened bill is clearly marked
    if (typeof renderLeftBills === "function") {
      renderLeftBills();
    }
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
  const errOverlay   = document.getElementById("posErrorOverlay");
  const shiftOverlay = document.getElementById("shiftConfirmOverlay");
  if (
    (overlayEl && !overlayEl.hidden)     || // delete bill modal
    (errOverlay && !errOverlay.hidden)   || // error modal
    (inqOverlayEl && !inqOverlayEl.hidden) || // inquiry modal
    (shiftOverlay && !shiftOverlay.hidden)   // NEW: shift end confirm
  ) {
    return;
  }

    // Alt + A / Alt + ش => toggle between "add" and "inq" modes
  if (
    e.altKey &&
    !e.ctrlKey &&
    !e.metaKey &&
    (
      e.code === "KeyA" ||              // physical A key
      e.key === "a" || e.key === "A" || // Latin
      e.key === "ش"                     // Arabic keyboard
    )
  ) {
    e.preventDefault();
    togglePosMode();
    return;
  }



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

async function chooseName(i) {
  const it = suggestItems[i]; 
  if (!it || state.bill.locked) return;

  try {
    const r = await fetch(`/pos/api/lookup/id/${it.id}/`);
    const j = await r.json();
    if (!j.ok) return;

    const p = j.product;

     if (state.mode === "inq") {
      await openInquiryOverlay(p);
    } else {
      const ok = await ensureProductAvailableInStore(p);
      if (!ok) return;

      const row = toRow(p);
      row.qty = 1;
      row.uomIndex = 1;
      state.rows.push(row);
      state.selectedIndex = state.rows.length - 1;
      renderRows();
      clearRightPanel();
      focusBarcodeSoon();
    }
  } catch (err) {
    console.error(err);
  } finally {
    nameInput.value = "";
    suggest.style.display = "none";
  }
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
document.getElementById("pcode")?.addEventListener("keydown", async (e) => {
  if (e.code === "Enter" || e.code === "NumpadEnter") {
    if (state.bill.locked) return;
    const v = e.target.value.trim(); 
    if (!v) return;

    try {
      const r = await fetch(`/pos/api/lookup/code/${encodeURIComponent(v)}/`);
      const j = await r.json();
      if (!j.ok) return;

      const p = j.product;

      if (state.mode === "inq") {
        await openInquiryOverlay(p);
      } else {
        const ok = await ensureProductAvailableInStore(p);
        if (!ok) return;

        const row = toRow(p);
        row.qty = 1;
        row.uomIndex = Number(p.matched_unit_index || 1);
        row.lastCode = v;
        state.rows.push(row);
        state.selectedIndex = state.rows.length - 1;
        renderRows();
        clearRightPanel();
        focusBarcodeSoon();
      }

    } catch (err) {
      console.error(err);
    } finally {
      e.target.value = "";
    }
  }
});

document.getElementById("pid")?.addEventListener("keydown", async (e) => {
  if (e.code === "Enter" || e.code === "NumpadEnter") {
    if (state.bill.locked) return;
    const v = parseInt(e.target.value.trim() || "0", 10); 
    if (!v) return;

    try {
      const r = await fetch(`/pos/api/lookup/id/${v}/`);
      const j = await r.json();
      if (!j.ok) return;

      const p = j.product;

      if (state.mode === "inq") {
        await openInquiryOverlay(p);
      } else {
        const ok = await ensureProductAvailableInStore(p);
        if (!ok) return;

        const row = toRow(p);
        row.qty = 1;
        row.uomIndex = 1;
        state.rows.push(row);
        state.selectedIndex = state.rows.length - 1;
        renderRows();
        clearRightPanel();
        focusBarcodeSoon();
      }

    } catch (err) {
      console.error(err);
    } finally {
      e.target.value = "";
    }
  }
});

/* ===== Click outside to save & return to barcode ===== */
document.addEventListener("click", async (e) => {
  if (!state.editing) return;
  if (state.bill.locked) return;
  const ignore = e.target.closest("input, textarea, select, button, .btn, [role='button'], #nameSuggest, #posContextMenu");
  if (ignore) return;
  await saveEditAndGoIdle();
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
          "X-CSRFToken": csrfToken() || "",
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

  // no bill is selected in left panel now
  state.selectedBillId = null;
  // refresh left-list highlight (active bill)
  if (typeof renderLeftBills === "function") {
    renderLeftBills();
  }
}

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

function finalizePaidForSave() {
  // 🔥 enforce save rules (ignore textbox for full/none)
  if (state.bill.payStatus === "full") {
    state.bill.paidAmount = money2(state.bill.totalAmount);
    state.bill.leftAmount = 0;
    return;
  }
  if (state.bill.payStatus === "none") {
    state.bill.paidAmount = 0;
    state.bill.leftAmount = money2(state.bill.totalAmount);
    return;
  }
  // partial: keep whatever user typed (already in state.bill.paidAmount)
  state.bill.leftAmount = money2(Math.max(state.bill.totalAmount - state.bill.paidAmount, 0));
}

function pruneZeroRows() {
  state.rows = state.rows.filter(r => rowQtyPrimary(r) > 0);
}

async function validateBillBeforeSave(options) {
  const parked = !!(options && options.parked);

  // 0) Don’t save while a row is being edited — commit it first
  if (state.editing && state.selectedIndex >= 0) {
    // try to save edits (this already validates stock for that row)
    await saveEditAndGoIdle();

    // if still editing, it means validation failed and user must fix it
    if (state.editing) return false;
  }

  // 1) remove zero/negative rows
  pruneZeroRows();

  if (!state.rows.length) {
    alert("لا يمكن حفظ فاتورة فارغة.");
    return false;
  }

  // 2) row sanity checks (fast, local)
  for (let i = 0; i < state.rows.length; i++) {
    const r = state.rows[i];

    const qty = Number(r.qty || 0);
    const uomIndex = Number(r.uomIndex || 1);
    const conv = Number(r.conv || 1);
    const price = Number(r.price || 0);
    const discAmt = Math.max(Number(r.discAmt || 0), 0);

    if (!isFinite(qty) || qty <= 0) {
      showPosError("كمية غير صالحة.", `السطر رقم <strong>${i + 1}</strong> يحتوي كمية غير صحيحة.`);
      return false;
    }

    if (![1, 2].includes(uomIndex)) {
      showPosError("وحدة قياس غير صالحة.", `السطر رقم <strong>${i + 1}</strong> يحتوي uomIndex غير صحيح.`);
      return false;
    }

    if (!isFinite(conv) || conv <= 0) {
      // conv matters only when uomIndex=2 but keep it sane anyway
      showPosError("عامل تحويل غير صالح.", `السطر رقم <strong>${i + 1}</strong> يحتوي conv غير صحيح.`);
      return false;
    }

    if (!isFinite(price) || price < 0) {
      showPosError("سعر غير صالح.", `السطر رقم <strong>${i + 1}</strong> يحتوي سعر غير صحيح.`);
      return false;
    }

    const cur = (r.currency || CUR_SYP).toUpperCase();
    if (cur === CUR_USD && r.allowUsdSales === false) {
      showPosError("عملة غير مسموحة.", `السطر رقم <strong>${i + 1}</strong> لا يسمح بالبيع بالدولار لهذا المنتج.`);
      return false;
    }
    if (cur === CUR_SYP && r.allowSypSales === false) {
      showPosError("عملة غير مسموحة.", `السطر رقم <strong>${i + 1}</strong> لا يسمح بالبيع بالليرة لهذا المنتج.`);
      return false;
    }

    // discount clamp safety for any hydrated/old data
    const base = rowBase(r);
    if (!isFinite(base) || base < 0) {
      showPosError("خطأ في حساب السطر.", `السطر رقم <strong>${i + 1}</strong> لا يمكن حساب قيمته.`);
      return false;
    }
    if (!isFinite(discAmt) || discAmt < 0) {
      showPosError("حسم غير صالح.", `السطر رقم <strong>${i + 1}</strong> يحتوي حسم غير صحيح.`);
      return false;
    }
    if (round2(discAmt) > round2(base)) {
      // clamp it rather than failing hard (your choice)
      r.discAmt = money2(base);
      r.discPct = base > 0 ? 100 : 0;
    }
  }

  // 3) sync footer -> bill state
  syncBillFromFooter();

  // guard: paidAmount must be numeric
  if (!isFinite(state.bill.paidAmount)) state.bill.paidAmount = 0;
  state.bill.paidAmount = money2(state.bill.paidAmount);

  // 4) apply your save rules (full/none override textbox)
  finalizePaidForSave();

  // 4.5) settlement + container rules
  if (!parked && state.bill.payStatus !== "none") {
    if (!state.bill.moneyContainerId) {
      alert("يرجى اختيار الصندوق قبل حفظ فاتورة مدفوعة.");
      posContainerSelect?.focus();
      return false;
    }

    const enabled = getSelectedContainerCurrencies();
    if (state.bill.settlementMode === "split") {
      if (state.bill.totalSyp > 0 && !enabled.includes(CUR_SYP)) {
        alert("الصندوق المحدد لا يدعم SYP. اختر صندوقاً آخر أو حوّل الإجمالي.");
        return false;
      }
      if (state.bill.totalUsd > 0 && !enabled.includes(CUR_USD)) {
        alert("الصندوق المحدد لا يدعم USD. اختر صندوقاً آخر أو حوّل الإجمالي.");
        return false;
      }
    } else {
      const cur = state.bill.settlementCurrency || (state.bill.settlementMode === "all_usd" ? CUR_USD : CUR_SYP);
      if (cur && !enabled.includes(cur)) {
        alert(`الصندوق المحدد لا يدعم ${cur}.`);
        return false;
      }
    }
  }

  if (!parked && state.bill.payStatus === "partial" && state.bill.settlementMode === "split") {
    alert("الدفع الجزئي يتطلب اختيار عملة تسوية واحدة.");
    return false;
  }

  // 5) your business rules
  if (state.bill.payStatus !== "full") {
    if (!state.bill.customerName) {
      alert("يجب إدخال اسم الزبون أو إنشاء بطاقة زبون عندما تكون الفاتورة غير مدفوعة بالكامل.");
      custNameEl?.focus();
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

  // 6) set parked flag
  state.bill.parked = parked;
  return true;
}



async function sendBillToBackend(options) {
  const parked = !!(options && options.parked);

  // enforce save rules right before sending (bulletproof)
  syncBillFromFooter();
  finalizePaidForSave();

  // grab current shift from global set by shift box
  const shiftId = window.POS_ACTIVE_SHIFT_ID || null;

  const payload = {
    id: state.bill.id,
    parked: parked,
    pay_status: state.bill.payStatus,
    paid_amount: money2(state.bill.paidAmount),
    total_amount: money2(state.bill.totalAmount),
    total_syp: money2(state.bill.totalSyp),
    total_usd: money2(state.bill.totalUsd),
    settlement_mode: state.bill.settlementMode,
    settlement_currency: state.bill.settlementCurrency || null,
    customer_name: state.bill.customerName || null,
    create_new_customer: state.bill.createNewCustomer,
    shift_id: shiftId,   // 🔥 NEW
    money_container_id: state.bill.moneyContainerId,
    rows: state.rows.map((r) => ({
      product_id: r.id,
      name: r.name,
      number: r.number,
      qty: r.qty,
      uom_index: r.uomIndex,
      unit_price: r.price,
      currency: r.currency || CUR_SYP,
      disc_amount: money2(r.discAmt || 0),
      disc_pct: r.discPct || 0,
      notes: r.notes || "",
    })),
  };

  const url = "/pos/api/bill/save/";
  const res = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-CSRFToken": csrfToken() || "",
    },
    body: JSON.stringify(payload),
  });

  let j = null;
  try {
    j = await res.json();
  } catch (err) {
    // no JSON body or parse error
  }

  if (!res.ok || !j || !j.ok) {
    const err = new Error((j && j.error) || ("HTTP " + res.status));

    if (j && j.error === "INSUFFICIENT_STOCK") {
      err.code = "INSUFFICIENT_STOCK";
      err.items = j.items || [];
    }

    throw err;
  }

  return j;
}


async function handleParkBill() {
  if (state.bill.locked) return;
  if (!await validateBillBeforeSave({ parked: true })) return;


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
  if (!await validateBillBeforeSave({ parked: false })) return;

  try {
    const j = await sendBillToBackend({ parked: false });
    state.bill.id = j.bill.id;
    state.bill.parked = false;

    // ✅ بدال ما نوقف على الفاتورة ونقفلها، نحدّث قائمة الفواتير
    await loadTodayBills();

    alert("تم حفظ الفاتورة بنجاح.");



    // ✅ نبدأ فاتورة جديدة مباشرة
    deleteWholeBill();  // يمسح الصفوف + يرجّع الحالة لبيل جديدة
    setTimeout(() => document.getElementById("barcode")?.focus(), 0);

  } catch (err) {
    console.error(err);

    if (err.code === "INSUFFICIENT_STOCK") {
      showInsufficientStockError(err.items || []);
      return;
}


    alert("حدث خطأ أثناء حفظ الفاتورة.");
  }
}

async function handleNewBillClick() {
  // 1) Saved bill view: bill is locked (finalized)
  if (state.bill.locked) {
    state.selectedBillId = null;
    renderLeftBills();
    deleteWholeBill();
    setTimeout(() => document.getElementById("barcode")?.focus(), 0);
    return;
  }

  // 2) Pending bill OR new unsaved bill
  await handleParkBill();

  // 🚀 FIX: clear highlight from the pending bill we just parked
  state.selectedBillId = null;
  renderLeftBills();

  // 🚀 FIX: start a brand-new empty bill immediately
  deleteWholeBill();

  setTimeout(() => document.getElementById("barcode")?.focus(), 0);
}

newBillBtn?.addEventListener("click", (e) => {
  e.preventDefault();
  handleNewBillClick();
});

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
restoreContainerSelection();
renderRows();
syncFooterFromBill();
loadTodayBills();
