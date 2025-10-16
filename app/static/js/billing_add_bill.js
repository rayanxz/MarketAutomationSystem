// static/js/billing_add_bill.js
// Product lookup + add-to-bill table (prefills cost/price, keeps totals live)
// Keyboard navigation: Tab / Shift+Tab / ↑ / ↓ to move, Enter to pick, Esc to close.
(() => {
  // ---- DOM ----
  const q        = document.getElementById("prodQ");
  const sug      = document.getElementById("prodSug");
  const btnAdd   = document.getElementById("btnAddProd");
  const tbody    = document.getElementById("billBody");
  const totalBox = document.getElementById("billTotalBox");
  if (!q || !sug || !btnAdd || !tbody) return;

  // API root (provided by <body data-url-api-search="...">)
  const API_SEARCH = (document.body.dataset.urlApiSearch || "/manager/products/api/search/").replace(/\/+$/, "/");

  // ---- Search mode ----
  let mode = "name";
  document.querySelectorAll('input[name="prodMode"]').forEach(r => {
    if (r.checked) mode = r.value;
    r.addEventListener("change", () => { mode = r.value; clearSug(); q.focus(); });
  });

  // ---- Helpers ----
  const debounce = (fn, ms = 180) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
  const num  = v => { const n = parseFloat(String(v ?? "").trim().replace(",", ".")); return Number.isFinite(n) ? n : 0; };
  const fmt2 = v => (Number(v || 0)).toFixed(2);
  const onlyProducts = items => (items || []).filter(x => (x.type ?? "product") === "product");

  // ---- Suggestions state ----
  let lastItems = [];   // raw items from API
  let activeIndex = -1; // index into rendered product-only list

  function clearSug(){
    sug.hidden = true;
    sug.innerHTML = "";
    lastItems = [];
    activeIndex = -1;
  }

  function setActive(i){
    const lis = Array.from(sug.querySelectorAll("li"));
    if (!lis.length) { activeIndex = -1; return; }
    activeIndex = ((i % lis.length) + lis.length) % lis.length; // wrap-around
    lis.forEach((li, idx) => li.classList.toggle("active", idx === activeIndex));
  }

  // ---- Render suggestions (products only) ----
  function renderSug(items){
    const prods = onlyProducts(items);
    if (!prods.length){ clearSug(); return; }

    let html = "";
    prods.slice(0, 8).forEach((p, i) => {
      const path = `${p.col_name || p.col_code || ""}${(p.set_name || p.set_code) ? " · " + (p.set_name || p.set_code) : ""}`;
      html += `
        <li data-i="${i}" style="display:flex;gap:8px;align-items:center;cursor:pointer;">
          <span class="s-code">📦</span>
          <span>${p.name}</span>
          <span class="s-path" style="margin-inline-start:auto;color:#6b7280;font-size:12px;">${path} — ${p.code || ""}</span>
        </li>`;
    });
    sug.innerHTML = html;
    sug.hidden = false;

    // default highlight first row
    setActive(0);

    // mouse: hover tracks active; mousedown picks
    Array.from(sug.querySelectorAll("li")).forEach((li, i) => {
      li.addEventListener("mouseenter", () => setActive(i));
      li.addEventListener("mousedown", e => {
        e.preventDefault();
        const it = onlyProducts(lastItems)[i];
        if (it) pick(it);
      });
    });
  }

  // ---- Search ----
  const doSearch = async () => {
    const val = (q.value || "").trim();
    if (!val) { clearSug(); return; }

    const url = `${API_SEARCH}?mode=${encodeURIComponent(mode)}&q=${encodeURIComponent(val)}`;
    const r = await fetch(url, { headers: { "Accept": "application/json" } });
    if (!r.ok) { clearSug(); return; }
    const d = await r.json();
    if (!d.ok) { clearSug(); return; }

    lastItems = d.items || [];
    if (mode === "barcode") { clearSug(); return; } // barcode adds on Enter
    renderSug(lastItems);
  };
  const onType = debounce(doSearch, 180);

  q.addEventListener("input", onType);
  q.addEventListener("focus", onType);
  q.addEventListener("blur", () => setTimeout(clearSug, 120));

  // Keyboard: Tab/Shift+Tab/Arrows to move, Enter to pick, Esc to close
  q.addEventListener("keydown", async (e) => {
    // Escape → close list
    if (e.key === "Escape"){ clearSug(); return; }

    // Barcode mode → Enter triggers direct lookup
    if (mode === "barcode") {
      if (e.key === "Enter") {
        e.preventDefault();
        const val = (q.value || "").trim();
        if (!val) return;
        const url = `${API_SEARCH}?mode=barcode&q=${encodeURIComponent(val)}`;
        const r = await fetch(url, { headers: { "Accept": "application/json" } });
        const d = r.ok ? await r.json() : { ok:false };
        const it = d.ok ? onlyProducts(d.items)[0] : null;
        if (it) pick(it);
      }
      return;
    }

    // If suggestions are visible, handle navigation
    const hasList = !sug.hidden && sug.querySelectorAll("li").length > 0;

    if (hasList && (e.key === "Tab" || e.key === "ArrowDown" || e.key === "ArrowUp")) {
      e.preventDefault();
      const delta =
        (e.key === "ArrowDown" || (!e.shiftKey && e.key === "Tab")) ? 1 :
        (e.key === "ArrowUp"  || (e.shiftKey && e.key === "Tab")) ? -1 : 0;
      setActive(activeIndex + delta);
      return;
    }

    if (e.key === "Enter") {
      e.preventDefault();
      const val = (q.value || "").trim();
      if (!val) return;

      if (hasList && activeIndex >= 0) {
        const it = onlyProducts(lastItems)[activeIndex];
        if (it) pick(it);
        return;
      }

      // fallback: pick first product from fresh search
      const url = `${API_SEARCH}?mode=${encodeURIComponent(mode)}&q=${encodeURIComponent(val)}`;
      const r = await fetch(url, { headers: { "Accept": "application/json" } });
      const d = r.ok ? await r.json() : { ok:false };
      const it = d.ok ? onlyProducts(d.items)[0] : null;
      if (it) pick(it);
    }
  });

  // Add button: first suggestion or try a fresh query
  btnAdd.addEventListener("click", async () => {
    const li = sug.querySelector("li");
    if (li && !sug.hidden) {
      const idx = activeIndex >= 0 ? activeIndex : 0;
      const it = onlyProducts(lastItems)[idx];
      if (it) { pick(it); return; }
    }
    const val = (q.value || "").trim();
    if (!val) return;
    const url = `${API_SEARCH}?mode=${encodeURIComponent(mode)}&q=${encodeURIComponent(val)}`;
    const r = await fetch(url, { headers: { "Accept": "application/json" } });
    const d = r.ok ? await r.json() : { ok:false };
    const it = d.ok ? onlyProducts(d.items)[0] : null;
    if (it) pick(it);
  });

  // ---- Add row to table (prefill cost/price; real unit labels; lock for id/barcode) ----
  function pick(prod){
    clearSug();
    q.value = "";

    // de-dupe: focus qty if exists
    const exists = tbody.querySelector(`tr[data-pid="${prod.id}"]`);
    if (exists){
      const qty = exists.querySelector('input[name="qty[]"]');
      if (qty) qty.focus();
      return;
    }

    // unit info
    const hasU2 = !!(prod.unit_secondary && String(prod.unit_secondary).trim().length);
    const cf    = prod.conversion_factor ? String(prod.conversion_factor) : "";

    // When mode=id/barcode and API returned matched_unit (1 or 2), lock the select to that unit
    const isSingleUnit = (mode === "id" || mode === "barcode") && prod.matched_unit;
    const showU1 = !isSingleUnit || (prod.matched_unit === 1);
    const showU2 = hasU2 && (!isSingleUnit || (prod.matched_unit === 2));
    const lockSelect = !!isSingleUnit;

    const u1Label = prod.unit_primary_label || "الوحدة الأولى";
    const u2Label = prod.unit_secondary_label || "الوحدة الثانية";

    const tr = document.createElement("tr");
    tr.dataset.pid = String(prod.id);
    tr.dataset.hasU2 = hasU2 ? "1" : "0";
    tr.dataset.cf = cf;
    tr.dataset.mode = mode || "name";

    tr.innerHTML = `
      <td class="pname">${prod.name}</td>

      <td>
        <input name="cost[]" class="input" type="number" step="0.01" value="${prod.cost ?? ""}">
      </td>

      <td>
        <input name="price[]" class="input" type="number" step="0.01" value="${prod.price ?? ""}">
      </td>

      <td>
        <div style="display:flex; gap:6px; align-items:center;">
          <input name="qty[]" class="input" type="number" step="0.001" min="0" placeholder="0">
          <select name="qty_unit[]" class="input" style="max-width:160px;" ${lockSelect ? "disabled" : ""}>
            ${showU1 ? `<option value="u1">${u1Label}</option>` : ``}
            ${showU2 ? `<option value="u2">${u2Label}</option>` : ``}
          </select>
        </div>
      </td>

      <td>
        <input name="total_cost[]" class="input" type="number" step="0.01" placeholder="0.00">
      </td>

      <td style="text-align:center;">
        <button type="button" class="btn-danger btn-del">✕</button>
      </td>

      <input type="hidden" name="product_id[]" value="${prod.id}">
    `;

    // Ensure the locked unit is selected for single-unit rows
    if (isSingleUnit) {
      const sel = tr.querySelector('select[name="qty_unit[]"]');
      if (sel) sel.value = (prod.matched_unit === 2 ? "u2" : "u1");
    }

    tr.querySelector(".btn-del").addEventListener("click", () => {
      tr.remove();
      recalcBillTotal();
    });

    // Recalc on input or unit change
    tr.addEventListener("input", handleRowChange);
    tr.addEventListener("change", handleRowChange);

    tbody.appendChild(tr);

    // focus qty for speed
    const qty = tr.querySelector('input[name="qty[]"]');
    if (qty) qty.focus();

    recalcBillTotal();
  }

  function handleRowChange(e){
    const nm = e.target.name || "";
    if (nm === "qty[]" || nm === "cost[]" || nm === "total_cost[]" || nm === "qty_unit[]") {
      recalcBillTotal();
    }
  }

  // ---- Bill total (uses override when provided; otherwise qty * cost * CF if unit-2) ----
  function recalcBillTotal(){
    let total = 0;
    tbody.querySelectorAll("tr").forEach(tr => {
      const qty  = num(tr.querySelector('input[name="qty[]"]')?.value);
      const cost = num(tr.querySelector('input[name="cost[]"]')?.value);
      const overrideRaw = tr.querySelector('input[name="total_cost[]"]')?.value ?? "";

      const unitSel = tr.querySelector('select[name="qty_unit[]"]');
      const isU2 = unitSel && unitSel.value === "u2";
      const cf = num(tr.dataset.cf || "0");

      let line;
      if (overrideRaw.trim().length) {
        line = num(overrideRaw);
      } else {
        line = qty * cost * (isU2 ? (cf || 1) : 1);
      }

      if (Number.isFinite(line)) total += line;
    });
    if (totalBox) totalBox.textContent = fmt2(total);
  }

  // ---- Pay mode UI (enable #paidAmount only for partial) ----
  (() => {
    const paidInput = document.getElementById("paidAmount");
    if (!paidInput) return;
    const radios = document.querySelectorAll('input[name="pay"]');

    function syncPayUI() {
      const sel = document.querySelector('input[name="pay"]:checked')?.value || "unpaid";
      if (sel === "partial") {
        paidInput.disabled = false;
      } else {
        paidInput.value = "";
        paidInput.disabled = true;
      }
    }

    radios.forEach(r => r.addEventListener("change", syncPayUI));
    syncPayUI(); // initial
  })();
})();
