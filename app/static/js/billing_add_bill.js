// static/js/billing_add_bill.js
(() => {
  "use strict";

  // ====== DOM ======
  // Provider AC
  const provInput = document.getElementById("provInput");
  const provList  = document.getElementById("provList");
  const provIdEl  = document.getElementById("provId");
  const provErr   = document.getElementById("provErr");

  // Bill serial / errors
 
  const saveErr   = document.getElementById("saveErr");
  const autoSerialBadge = document.getElementById("billAutoSerial");

  // Product search + table
  const q        = document.getElementById("prodQ");
  const sug      = document.getElementById("prodSug");
  const btnAdd   = document.getElementById("btnAddProd");
  const tbody    = document.getElementById("billBody");
  const totalBox = document.getElementById("billTotalBox");
  const totalSypBox = document.getElementById("billTotalSyp");
  const totalUsdBox = document.getElementById("billTotalUsd");
  const grandTotals = document.getElementById("grandTotals");
  const payCurrency = document.getElementById("payCurrency");
  const fxBadge = document.getElementById("fxBadge");
  const settleCurLabel = document.getElementById("settleCurLabel");

  // Pay widgets
  const paidInput = document.getElementById("paidAmount");
  const payRadios = document.querySelectorAll('input[name="pay"]');
  const costWarnModal = document.getElementById("costWarnModal");
  const costWarnRows = document.getElementById("costWarnRows");
  const costWarnConfirm = document.getElementById("costWarnConfirm");
  const costWarnCancel = document.getElementById("costWarnCancel");

  // ====== URLs / Config ======
  const BILLING   = window.__BILLING__ || {};
  const API_PROV  = (BILLING.providersAcUrl || "/manager/billing/api/providers/ac/").replace(/\/+$/,"/");
  const API_SEARCH= (document.body?.dataset?.urlApiSearch || BILLING.searchUrl || "/manager/billing/api/products/search/").replace(/\/+$/,"/");
  const SAVE_URL  = (BILLING.saveBillUrl || "/manager/debts/api/bill/save/").replace(/\/+$/,"/");
  const LIST_URL  = (BILLING.listUrl || document.body?.dataset?.urlList || "/manager/billing/").replace(/\/+$/,"/");

   const apiModeFor = (m) => (m || "name");

  // ====== Utils ======
  const debounce = (fn, ms=180)=>{ let t; return (...a)=>{ clearTimeout(t); t=setTimeout(()=>fn(...a),ms); }; };
  const num  = v => { const n = parseFloat(String(v ?? "").trim().replace(",", ".")); return Number.isFinite(n) ? n : 0; };
  const fmt2 = v => (Number(v || 0)).toFixed(2);
  const fmt4 = v => (Number(v || 0)).toFixed(4);
  const formatDisplay2 = (v) => {
    const n = Number(v ?? 0);
    if (!Number.isFinite(n)) return "0";
    const clipped = Math.trunc(n * 100) / 100;
    if (clipped === 0 || Object.is(clipped, -0)) return "0";
    const [intPartRaw, fracRaw = ""] = clipped.toFixed(2).split(".");
    const intPart = intPartRaw.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
    const frac = fracRaw.replace(/0+$/, "");
    return frac ? `${intPart}.${frac}` : intPart;
  };
  const readFxRate = () => {
    const fxVal = num(fxBadge?.dataset?.fxRaw || "");
    return (Number.isFinite(fxVal) && fxVal > 0) ? fxVal : null;
  };
  const refreshFxBadgeDisplay = () => {
    if (!fxBadge) return;
    const fxVal = readFxRate();
    fxBadge.textContent = fxVal ? formatDisplay2(fxVal) : "NOT SET";
  };

  const looksLikeProduct = (x) => x && typeof x === "object" && ("id" in x) && ("name" in x);

  function defaultPurchaseCurForProduct(p){
    const def = (p.effective_default_purchase_currency || p.default_purchase_currency || "").toUpperCase();
    if (def === "USD" && p.allow_usd_purchasing) return "USD";
    if (def === "SYP" && p.allow_syp_purchasing) return "SYP";
    if (p.allow_syp_purchasing) return "SYP";
    if (p.allow_usd_purchasing) return "USD";
    return "SYP";
  }

  function defaultSaleCurForProduct(p){
    const def = (p.effective_default_sale_currency || p.default_sale_currency || "").toUpperCase();
    if (def === "USD" && p.allow_usd_sales) return "USD";
    if (def === "SYP" && p.allow_syp_sales) return "SYP";
    if (p.allow_syp_sales) return "SYP";
    if (p.allow_usd_sales) return "USD";
    return "SYP";
  }

  function defaultCostFor(p, cur){
    if (cur === "USD") return (p.default_cost_usd ?? p.cost_usd ?? "");
    return (p.default_cost_syp ?? p.cost_syp ?? "");
  }

  function defaultPriceFor(p, cur){
    if (cur === "USD") return (p.default_price_usd ?? p.price_usd ?? "");
    return (p.default_price_syp ?? p.price_syp ?? "");
  }

  function setProductNameCell(cell, fullName){
    if (!cell) return;
    const name = String(fullName ?? "");
    const textEl = cell.querySelector(".pname-text") || cell;
    textEl.textContent = name;
    cell.title = name;
  }

  tbody?.querySelectorAll("td.pname").forEach((cell) => {
    setProductNameCell(cell, cell.textContent || "");
  });


  function normalize(items){
    return (items || []).filter(looksLikeProduct).map(p => ({
      id: p.id,
      name: p.name,
      // backend returns product id as 'code'
      code: p.code || p.prod_code || "",
      col_name: p.col_name || p.col || "",
      col_code: p.col_code || "",
      set_name: p.set_name || "",
      set_code: p.set_code || "",
      unit_primary_label: p.unit_primary_label || p.u1_label || "الوحدة الأولى",
      unit_secondary_label: p.unit_secondary_label || p.u2_label || "الوحدة الثانية",
      unit_secondary: p.unit_secondary,
      conversion_factor: p.conversion_factor || p.cf || 0,
      matched_unit: p.matched_unit || null,
      cost: "",
      price: "",
      cost_syp: p.cost_syp ?? "",
      cost_usd: p.cost_usd ?? "",
      price_syp: p.price_syp ?? "",
      price_usd: p.price_usd ?? "",
      default_cost_syp: p.default_cost_syp ?? p.cost_syp ?? "",
      default_cost_usd: p.default_cost_usd ?? p.cost_usd ?? "",
      default_price_syp: p.default_price_syp ?? p.price_syp ?? "",
      default_price_usd: p.default_price_usd ?? p.price_usd ?? "",
      enable_syp: !!p.enable_syp,
      enable_usd: !!p.enable_usd,
      allow_syp_purchasing: ("allow_syp_purchasing" in p) ? !!p.allow_syp_purchasing : !!p.enable_syp,
      allow_usd_purchasing: ("allow_usd_purchasing" in p) ? !!p.allow_usd_purchasing : !!p.enable_usd,
      allow_syp_sales: ("allow_syp_sales" in p) ? !!p.allow_syp_sales : !!p.enable_syp,
      allow_usd_sales: ("allow_usd_sales" in p) ? !!p.allow_usd_sales : !!p.enable_usd,
      default_currency: (p.default_currency || "").toUpperCase(),
      default_purchase_currency: (p.default_purchase_currency || "").toUpperCase(),
      default_sale_currency: (p.default_sale_currency || "").toUpperCase(),
      effective_default_purchase_currency: (p.effective_default_purchase_currency || "").toUpperCase(),
      effective_default_sale_currency: (p.effective_default_sale_currency || "").toUpperCase(),
    }));
  }

  // ====== Product search (API) ======
  async function fetchSearch(params){
    try{
      const u = new URL(API_SEARCH, window.location.origin);
      const modeParam = params.mode ? apiModeFor(params.mode) : null;
      if (params.q != null)   u.searchParams.set("q", String(params.q));
      if (modeParam)          u.searchParams.set("mode", modeParam);
      const r = await fetch(u.toString(), { headers: { "Accept": "application/json" } });
      if (!r.ok) return { ok:false, items:[] };
      const d = await r.json();
      return { ok: !!d.ok, items: normalize(d.items) };
    }catch(err){
      console.warn("[billing] fetchSearch failed", err);
      return { ok:false, items:[] };
    }
  }

    // STRICT: query only the selected mode (no fallbacks).
  async function searchCascade(query, mode){
    const triedIds = new Set();
    const out = [];
    const pushUnique = (arr)=> (arr || []).forEach(it => {
      if (it && it.id != null && !triedIds.has(it.id)) { triedIds.add(it.id); out.push(it); }
    });

    const start = apiModeFor(mode);
    const order = [start]; // only the picked mode

    for (const m of order){

      const res1 = await fetchSearch({ q: query, mode: m });
      if (res1.ok) pushUnique(res1.items);

      if (out.length > 0) break;
    }
    return out;
  }
  async function refreshAutoSerial(){
  if (!autoSerialBadge) return;
  try{
    const r = await fetch(BILLING.nextSerialUrl || "/manager/billing/api/bill/next-serial/", {
      headers: { "Accept": "application/json" }
    });
    const d = await r.json();
    if (d?.ok && d.next_serial != null){
      const n = String(d.next_serial).padStart(3, "0");
      autoSerialBadge.textContent = n;
      autoSerialBadge.setAttribute("data-serial", String(d.next_serial));
    }
  }catch{/* silent */}
}
refreshAutoSerial();

  // ====== ARIA helper ======
  function enhanceListAsListbox(ul){
    if (!ul) return;
    ul.setAttribute("role","listbox");
    ul.querySelectorAll("li").forEach(li=>{
      li.setAttribute("role","option");
      li.setAttribute("tabindex","-1");
    });
  }

  // ======================================================================
  // PROVIDER AUTOCOMPLETE
  // ======================================================================
  let provItems = [];
  let provActive = -1;

  function clearProvList(){
    provList.hidden = true;
    provList.innerHTML = "";
    provItems = [];
    provActive = -1;
  }

  function setProvActive(i){
    const lis = Array.from(provList.querySelectorAll("li"));
    if (!lis.length) { provActive = -1; return; }
    provActive = ((i % lis.length) + lis.length) % lis.length;
    lis.forEach((li, idx) => li.classList.toggle("active", idx === provActive));
  }

  async function provSearch(){
    const val = (provInput?.value || "").trim();
    provIdEl.value = ""; // typing clears selection
    if (!val) { clearProvList(); return; }
    try{
      const r = await fetch(`${API_PROV}?q=${encodeURIComponent(val)}`, {headers:{"Accept":"application/json"}});
      const d = await r.json();
      const items = (d && d.ok) ? (d.items || []) : [];
      provItems = items;
      if (!items.length){ clearProvList(); return; }
      provList.innerHTML = items.map((it,i)=>`<li data-i="${i}">${it.name}</li>`).join("");
      enhanceListAsListbox(provList);
      provList.hidden = false;
      setProvActive(0);
      Array.from(provList.querySelectorAll("li")).forEach((li,i)=>{
        li.addEventListener("mouseenter", ()=> setProvActive(i));
        li.addEventListener("mousedown", e => { e.preventDefault(); pickProv(items[i]); });
      });
    }catch{ clearProvList(); }
  }

  function pickProv(it){
    provInput.value = it.name;
    provIdEl.value = String(it.id);
    clearProvList();
    provErr.hidden = true;
  }
   async function validateProviderExact(){
   const val = (provInput?.value || "").trim();
   if (!val){ provErr.hidden = true; return; }
   // If user has already picked from the list, we're good.
   if ((provIdEl.value || "").trim()) { provErr.hidden = true; return; }
   try{
     const r = await fetch(`${API_PROV}?q=${encodeURIComponent(val)}`, {headers:{"Accept":"application/json"}});
     const d = await r.json();
     const items = (d && d.ok) ? (d.items || []) : [];
     const match = items.find(it => (it.name || "").trim().toLowerCase() === val.toLowerCase());
     if (match){
       pickProv(match); // auto-resolve to the exact one
       return;
     }
     // No exact active provider by that name:
     provErr.textContent = "لا يوجد مورد بهذا الاسم.";
     provErr.hidden = false;
   }catch{
     // If the check fails, don't block — the save button will still guard via provId
   }
 }

  provInput?.addEventListener("input", debounce(provSearch, 180));
  provInput?.addEventListener("focus", provSearch);
   provInput?.addEventListener("blur", () => {
   setTimeout(() => {
     clearProvList();
     validateProviderExact();
   }, 120);
 });
  provInput?.addEventListener("input", () => { provErr.hidden = true; });
  provInput?.addEventListener("keydown", (e)=>{
    const hasList = !provList.hidden && provList.querySelectorAll("li").length > 0;
    if (e.key === "Escape"){ clearProvList(); return; }
    if (!hasList) return;
    if (["ArrowDown","Tab"].includes(e.key) && !e.shiftKey){ e.preventDefault(); setProvActive(provActive+1); }
    else if (["ArrowUp"].includes(e.key) || (e.key==="Tab" && e.shiftKey)){ e.preventDefault(); setProvActive(provActive-1); }
    else if (e.key==="Enter"){ e.preventDefault(); const it = provItems[provActive>=0?provActive:0]; if (it) pickProv(it); }
  });

  // ======================================================================
  // PRODUCT SEARCH + SUGGESTIONS + ROW BUILDER
  // ======================================================================
  let mode = "name";
  document.querySelectorAll('input[name="prodMode"]').forEach(r => {
    if (r.checked) mode = r.value;
    r.addEventListener("change", () => { mode = r.value; clearSug(); q?.focus(); });
  });

  let lastItems = [];
  let activeIndex = -1;

  // Position the suggestion list right under the input
  const placeSug = ()=>{
    if (!q || !sug) return;
    const r = q.getBoundingClientRect();
    const s = sug.style;
    s.position = "fixed";     // detach from parents
    s.left     = `${r.left}px`;
    s.top      = `${r.bottom + 4}px`;
    s.width    = `${r.width}px`;
    s.maxHeight= "280px";
    s.overflow = "auto";
    s.zIndex   = "4000";
    s.right    = "auto";
    s.display  = "block";
    sug.hidden = false;
  };

  // Fully hide + reset styles to avoid sticky UI
  function clearSug(){
    if (!sug) return;
    sug.hidden = true;
    sug.innerHTML = "";
    sug.style.display = "";
    sug.style.position = "";
    sug.style.left = "";
    sug.style.top = "";
    sug.style.width = "";
    sug.style.maxHeight = "";
    sug.style.overflow = "";
    sug.style.zIndex = "";
    lastItems = [];
    activeIndex = -1;
  }

  function setActive(i){
    const lis = [...(sug?.querySelectorAll("li") || [])];
    if (!lis.length){ activeIndex=-1; return; }
    activeIndex = ((i % lis.length) + lis.length) % lis.length;
    lis.forEach((li, idx)=> li.classList.toggle("active", idx===activeIndex));
  }

  function renderSug(items){
    if (!sug) { console.warn("[billing] #prodSug not found"); return; }
    if (!items.length){ clearSug(); return; }

    let html = "";
    items.slice(0,8).forEach((p,i)=>{
      const pathBits = [];
      const col = p.col_name || p.col_code; if (col) pathBits.push(col);
      const set = p.set_name || p.set_code; if (set) pathBits.push(set);
      const meta = [pathBits.join(" · "), p.code].filter(Boolean).join(" — ");
      html += `
        <li data-i="${i}">
          <span>📦</span>
          <span>${p.name}</span>
          <span style="margin-inline-start:auto;color:#6b7280;font-size:12px;">${meta}</span>
        </li>`;
    });

    sug.innerHTML = html;
    enhanceListAsListbox(sug);
    setActive(0);

    [...sug.querySelectorAll("li")].forEach((li,i)=>{
      li.addEventListener("mouseenter", ()=> setActive(i));
      li.addEventListener("mousedown", e=>{ e.preventDefault(); const it=lastItems[i]; if(it) pick(it); });
    });

    placeSug(); // pin after render
  }

    const doSearch = async ()=>{
      const val = (q?.value || "").trim();
      if (!val) { clearSug(); return; }   // hide if empty, no recursion
      try{
        const items = await searchCascade(val, mode);
        lastItems = items;
        renderSug(lastItems);
    }catch{
      clearSug();
    }
  };
  const onType = debounce(doSearch, 180);

  q?.addEventListener("input", onType);
  q?.addEventListener("focus", ()=>{
    const val = (q?.value || "").trim();
    if (val) onType(); else clearSug();
  });
  q?.addEventListener("blur", ()=> setTimeout(clearSug, 120));

  // Keep it pinned while visible
  window.addEventListener("resize", ()=> { if (!sug?.hidden) placeSug(); });
  window.addEventListener("scroll", ()=> { if (!sug?.hidden) placeSug(); }, { passive:true });

  q?.addEventListener("keydown", async (e)=>{
    if (e.key==="Escape"){ clearSug(); return; }
    const hasList = !!(sug && !sug.hidden && sug.querySelectorAll("li").length>0);

    if (hasList && (e.key==="Tab" || e.key==="ArrowDown" || e.key==="ArrowUp")){
      e.preventDefault();
      const delta = (e.key==="ArrowDown" || (!e.shiftKey && e.key==="Tab")) ? 1 : -1;
      setActive(activeIndex + delta);
      return;
    }

    if (e.key==="Enter"){
      e.preventDefault();
      const val=(q?.value||"").trim(); if(!val) { clearSug(); return; }
      if (hasList && activeIndex>=0){
        const it=lastItems[activeIndex]; if (it) { pick(it); return; }
      }
      const items = await searchCascade(val, mode);
      if (items.length) pick(items[0]); else clearSug();
    }
  });

  btnAdd?.addEventListener("click", async ()=>{
    if (sug && !sug.hidden){
      const idx = activeIndex>=0?activeIndex:0;
      const it = lastItems[idx];
      if (it){ pick(it); return; }
    }
    const val=(q?.value||"").trim(); if(!val) { clearSug(); return; }
    const items = await searchCascade(val, mode);
    if (items.length) pick(items[0]); else clearSug();
  });

  function qtyMultiplierForRow(tr){
    const isU2 = tr.querySelector('select[name="qty_unit[]"]')?.value === "u2";
    const cf = num(tr.dataset.cf || "0");
    return isU2 ? (cf > 0 ? cf : 1) : 1;
  }

  function isRowQtyValid(tr){
    if (!tr) return true;
    const qtyInput = tr.querySelector('input[name="qty[]"]');
    if (!qtyInput) return true;
    return num(qtyInput.value) > 0;
  }

  function updateRowQtyWarning(tr){
    const qtyCell = tr?.querySelector("td.qty-cell") || tr?.querySelector("td:nth-child(4)");
    if (!qtyCell) return;
    qtyCell.classList.toggle("qty-warn", !isRowQtyValid(tr));
  }

  function isRowCostValid(tr){
    if (!tr) return true;
    const costInput = tr.querySelector('input[name="cost[]"]');
    if (!costInput) return true;
    return num(costInput.value) > 0;
  }

  function updateRowCostWarning(tr){
    const costCell = tr?.querySelector("td.cost-cell") || tr?.querySelector("td:nth-child(2)");
    if (!costCell) return;
    costCell.classList.toggle("qty-warn", !isRowCostValid(tr));
  }

  function invalidCostRowNumbers(rows){
    return rows.reduce((out, tr, idx) => {
      if (!isRowCostValid(tr)) out.push(idx + 1);
      return out;
    }, []);
  }

  function confirmSaveWithInvalidCosts(rowNumbers){
    if (!rowNumbers.length) return true;
    if (!costWarnModal || !costWarnConfirm || !costWarnCancel){
      saveErr.textContent = "تعذر إظهار نافذة تأكيد التكلفة.";
      saveErr.hidden = false;
      return Promise.resolve(false);
    }

    const rowsText = rowNumbers.join("، ");
    if (costWarnRows) costWarnRows.textContent = rowsText;

    costWarnModal.hidden = false;
    document.body.classList.add("modal-open");

    return new Promise((resolve) => {
      let settled = false;

      const close = (result) => {
        if (settled) return;
        settled = true;
        costWarnModal.hidden = true;
        document.body.classList.remove("modal-open");
        costWarnConfirm.removeEventListener("click", onConfirm);
        costWarnCancel.removeEventListener("click", onCancel);
        costWarnModal.removeEventListener("click", onBackdrop);
        document.removeEventListener("keydown", onKeyDown);
        resolve(result);
      };

      const onConfirm = () => close(true);
      const onCancel = () => close(false);
      const onBackdrop = (e) => {
        if (e.target === costWarnModal) close(false);
      };
      const onKeyDown = (e) => {
        if (e.key !== "Escape") return;
        e.preventDefault();
        close(false);
      };

      costWarnConfirm.addEventListener("click", onConfirm);
      costWarnCancel.addEventListener("click", onCancel);
      costWarnModal.addEventListener("click", onBackdrop);
      document.addEventListener("keydown", onKeyDown);
      costWarnCancel.focus();
    });
  }

  function syncRowCostAndTotal(tr, source){
    if (!tr) return;
    const qtyInput = tr.querySelector('input[name="qty[]"]');
    const costInput = tr.querySelector('input[name="cost[]"]');
    const totalInput = tr.querySelector('input[name="total_cost[]"]');
    if (!qtyInput || !costInput || !totalInput) return;

    const qty = num(qtyInput.value);
    const multiplier = qtyMultiplierForRow(tr);
    const denom = qty * multiplier;

    if (source === "total") {
      totalInput.dataset.auto = "0";
      if (!(denom > 0)) return; // avoid divide-by-zero when qty is 0
      const lineTotal = num(totalInput.value);
      costInput.value = fmt4(lineTotal / denom);
      costInput.dataset.auto = "0";
      return;
    }

    const unitCost = num(costInput.value);
    const lineTotal = (denom > 0) ? (unitCost * denom) : 0;
    totalInput.value = fmt2(lineTotal);
    totalInput.dataset.auto = "1";
  }

  function indicatorForPurchaseCurrency(cur){
    return (String(cur || "SYP").toUpperCase() === "USD") ? "$" : "SYP";
  }

  function updateRowTotalCostIndicator(tr, cur){
    const el = tr?.querySelector(".total-cost-cur");
    if (!el) return;
    el.textContent = indicatorForPurchaseCurrency(cur);
  }

  function revealExistingRow(tr){
    if (!tr) return;

    const scrollBox = tr.closest(".items-scroll");
    if (scrollBox) {
      const boxRect = scrollBox.getBoundingClientRect();
      const rowRect = tr.getBoundingClientRect();
      const targetTop =
        scrollBox.scrollTop +
        (rowRect.top - boxRect.top) -
        ((scrollBox.clientHeight - rowRect.height) / 2);
      scrollBox.scrollTo({ top: Math.max(0, targetTop), behavior: "smooth" });
    } else {
      tr.scrollIntoView({ block: "center", inline: "nearest", behavior: "smooth" });
    }

    const pnameCell = tr.querySelector("td.pname");
    if (!pnameCell) return;
    pnameCell.classList.remove("pname-hit");
    void pnameCell.offsetWidth; // restart flash animation on repeated searches
    pnameCell.classList.add("pname-hit");
    if (pnameCell.__pnameHitTimer) clearTimeout(pnameCell.__pnameHitTimer);
    pnameCell.__pnameHitTimer = setTimeout(() => {
      pnameCell.classList.remove("pname-hit");
      pnameCell.__pnameHitTimer = null;
    }, 3600);
  }

  function pick(prod){
    clearSug(); if (q) q.value="";
    const exists = tbody?.querySelector(`tr[data-pid="${prod.id}"]`);
    if (exists){
      revealExistingRow(exists);
      exists.querySelector('input[name="qty[]"]')?.focus();
      return;
    }

    const hasU2 = !!(prod.unit_secondary && String(prod.unit_secondary).trim().length);
    const cf    = prod.conversion_factor ? String(prod.conversion_factor) : "";
    const isSingleUnit = (mode==="id" || mode==="barcode") && prod.matched_unit;
    const showU1 = !isSingleUnit || (prod.matched_unit===1);
    const showU2 = hasU2 && (!isSingleUnit || (prod.matched_unit===2));
    const lockSelect = !!isSingleUnit;

    const u1Label = prod.unit_primary_label || "الوحدة الأولى";
    const u2Label = prod.unit_secondary_label || "الوحدة الثانية";

    const tr = document.createElement("tr");
    tr.dataset.pid = String(prod.id);
    tr.dataset.cf = cf;
    tr.dataset.costSyp = prod.default_cost_syp ?? prod.cost_syp ?? "";
    tr.dataset.costUsd = prod.default_cost_usd ?? prod.cost_usd ?? "";
    tr.dataset.priceSyp = prod.default_price_syp ?? prod.price_syp ?? "";
    tr.dataset.priceUsd = prod.default_price_usd ?? prod.price_usd ?? "";

    const cur = defaultPurchaseCurForProduct(prod);
    const curOptions = [];
    if (prod.allow_syp_purchasing) curOptions.push(`<option value="SYP" ${cur==="SYP" ? "selected" : ""}>SYP</option>`);
    if (prod.allow_usd_purchasing) curOptions.push(`<option value="USD" ${cur==="USD" ? "selected" : ""}>USD</option>`);
    const allowAnyPurch = !!(prod.allow_syp_purchasing || prod.allow_usd_purchasing);
    if (!allowAnyPurch) curOptions.push(`<option value="SYP" selected>SYP</option>`);
    const lockCurrency = !allowAnyPurch || !(prod.allow_syp_purchasing && prod.allow_usd_purchasing);

    const costVal = defaultCostFor(prod, cur);
    const priceSypVal = prod.allow_syp_sales ? defaultPriceFor(prod, "SYP") : "";
    const priceUsdVal = prod.allow_usd_sales ? defaultPriceFor(prod, "USD") : "";

    tr.innerHTML = `
      <td class="pname"><span class="pname-text"></span></td>
      <td class="cost-cell"><input name="cost[]" class="input numeric-math" data-math-display-max-decimals="2" type="number" step="0.0001" value="${costVal}"></td>
      <td>
        <select class="input cur-ui" ${lockCurrency ? "disabled" : ""}>${curOptions.join("")}</select>
        <input type="hidden" name="currency[]" class="cur-hidden" value="${cur}">
      </td>
      <td class="qty-cell">
        <div style="display:flex; gap:6px; align-items:center;">
          <input name="qty[]" class="input numeric-math" type="number" step="0.001" min="0" placeholder="0">
          <select name="qty_unit[]" class="input" style="max-width:160px;" ${lockSelect ? "disabled" : ""}>
            ${showU1 ? `<option value="u1">${u1Label}</option>` : ``}
            ${showU2 ? `<option value="u2">${u2Label}</option>` : ``}
          </select>
        </div>
      </td>
      <td>
        <div class="price-wrap">
          <button type="button" class="btn btn-fx btn-fx-syp">FX</button>
          <input name="price_syp[]" class="input price-syp numeric-math" data-math-display-max-decimals="2" type="number" step="0.0001" value="${priceSypVal}" ${prod.allow_syp_sales ? "" : "disabled"}>
        </div>
      </td>
      <td class="usd-price-cell">
        <div class="price-wrap">
          <button type="button" class="btn btn-fx btn-fx-usd">FX</button>
          <input name="price_usd[]" class="input price-usd numeric-math" data-math-display-max-decimals="2" type="number" step="0.0001" value="${priceUsdVal}" ${prod.allow_usd_sales ? "" : "disabled"}>
        </div>
      </td>
      <td>
        <div class="total-cost-wrap">
          <span class="total-cost-cur" aria-hidden="true">${indicatorForPurchaseCurrency(cur)}</span>
          <input name="total_cost[]" class="input numeric-math" data-math-display-max-decimals="2" type="number" step="0.01" placeholder="0.00">
        </div>
      </td>
      <td style="text-align:center;"><button type="button" class="btn-danger btn-del">✕</button></td>
      <input type="hidden" name="product_id[]" value="${prod.id}">
    `;
    setProductNameCell(tr.querySelector(".pname"), prod.name);

    tr.querySelector(".btn-del")?.addEventListener("click", ()=>{ tr.remove(); recalcBillTotal(); });

    const costInput = tr.querySelector('input[name="cost[]"]');
    const qtyInput = tr.querySelector('input[name="qty[]"]');
    const priceSypInput = tr.querySelector('input[name="price_syp[]"]');
    const priceUsdInput = tr.querySelector('input[name="price_usd[]"]');
    const totalCostInput = tr.querySelector('input[name="total_cost[]"]');
    const curSelect = tr.querySelector('select.cur-ui');
    const curHidden = tr.querySelector('input.cur-hidden');
    if (costInput) costInput.dataset.auto = "1";
    if (priceSypInput) priceSypInput.dataset.auto = "1";
    if (priceUsdInput) priceUsdInput.dataset.auto = "1";
    if (totalCostInput) totalCostInput.dataset.auto = "1";

    costInput?.addEventListener("input", () => { costInput.dataset.auto = "0"; });
    priceSypInput?.addEventListener("input", () => { priceSypInput.dataset.auto = "0"; });
    priceUsdInput?.addEventListener("input", () => { priceUsdInput.dataset.auto = "0"; });
    qtyInput?.addEventListener("keydown", (e) => {
      if (e.key !== "Enter") return;
      e.preventDefault(); // keep Enter from submitting form while editing qty
      q?.focus();
    });
    curSelect?.addEventListener("change", () => {
      const sel = (curSelect.value || "SYP").toUpperCase();
      if (curHidden) curHidden.value = sel;
      updateRowTotalCostIndicator(tr, sel);
      if (costInput && costInput.dataset.auto === "1") {
        const v = (sel === "USD" ? (tr.dataset.costUsd || "") : (tr.dataset.costSyp || ""));
        costInput.value = v;
      }
      syncRowCostAndTotal(tr, "cost");
      recalcBillTotal();
    });

    const hasNonZeroValue = (v) => {
      const raw = String(v ?? "").trim();
      if (!raw.length) return false;
      const n = num(raw);
      return Number.isFinite(n) && n !== 0;
    };

    tr.querySelector(".btn-fx-syp")?.addEventListener("click", () => {
      const fxVal = readFxRate();
      if (!fxVal) return;
      if (!priceSypInput || priceSypInput.disabled) return;
      if (!hasNonZeroValue(priceUsdInput?.value)) return;
      priceSypInput.value = fmt4(num(priceUsdInput.value) * fxVal);
      priceSypInput.dataset.auto = "0";
    });

    tr.querySelector(".btn-fx-usd")?.addEventListener("click", () => {
      const fxVal = readFxRate();
      if (!fxVal) return;
      if (!priceUsdInput || priceUsdInput.disabled) return;
      if (!hasNonZeroValue(priceSypInput?.value)) return;
      priceUsdInput.value = fmt4(num(priceSypInput.value) / fxVal);
      priceUsdInput.dataset.auto = "0";
    });

    tr.addEventListener("input", handleRowChange);
    tr.addEventListener("change", handleRowChange);

    tbody?.appendChild(tr);
    updateRowQtyWarning(tr);
    updateRowCostWarning(tr);
    qtyInput?.focus();
    recalcBillTotal();
  }

  function handleRowChange(e){
    const nm = e.target.name || "";
    const tr = e.target.closest("tr");
    if (!tr) return;
    updateRowQtyWarning(tr);
    if (nm === "cost[]") syncRowCostAndTotal(tr, "cost");
    else if (nm === "total_cost[]") syncRowCostAndTotal(tr, "total");
    else if (nm === "qty[]" || nm === "qty_unit[]") syncRowCostAndTotal(tr, "qty");
    updateRowCostWarning(tr);
    if (nm === "qty[]" || nm === "cost[]" || nm === "total_cost[]" || nm === "qty_unit[]" || nm === "currency[]"){ recalcBillTotal(); }
  }

  function recalcBillTotal(){
    let totalSyp = 0;
    let totalUsd = 0;
    tbody?.querySelectorAll("tr").forEach(tr=>{
      const qty = num(tr.querySelector('input[name="qty[]"]')?.value);
      const cost = num(tr.querySelector('input[name="cost[]"]')?.value);
      const overrideRaw = tr.querySelector('input[name="total_cost[]"]')?.value ?? "";
      const isU2 = tr.querySelector('select[name="qty_unit[]"]')?.value === "u2";
      const cf = num(tr.dataset.cf || "0");
      const cur = (tr.querySelector('input.cur-hidden')?.value || tr.querySelector('select.cur-ui')?.value || "SYP").toUpperCase();
      let line;
      if (overrideRaw.trim().length) line = num(overrideRaw);
      else line = qty * cost * (isU2 ? (cf || 1) : 1);
      if (Number.isFinite(line)) {
        if (cur === "USD") totalUsd += line; else totalSyp += line;
      }
    });

    if (totalSypBox) totalSypBox.textContent = formatDisplay2(totalSyp);
    if (totalUsdBox) totalUsdBox.textContent = formatDisplay2(totalUsd);

    const settleCur = (payCurrency?.value || "SYP").toUpperCase();
    if (settleCurLabel) settleCurLabel.textContent = settleCur;
    const settlementTotal = settleCur === "USD" ? totalUsd : totalSyp;
    if (totalBox) totalBox.textContent = formatDisplay2(settlementTotal);

    const fxVal = readFxRate();
    if (grandTotals){
      if (Number.isFinite(fxVal) && fxVal > 0){
        const gSyp = totalSyp + (totalUsd * fxVal);
        const gUsd = totalUsd + (totalSyp / fxVal);
        grandTotals.textContent = `إجمالي بالتحويل: ${formatDisplay2(gSyp)} SYP | ${formatDisplay2(gUsd)} USD`;
      } else {
        grandTotals.textContent = "";
      }
    }
  }

  // ======================================================================
  // PAY controls
  // ======================================================================
  function syncPayUI(){
    const sel = document.querySelector('input[name="pay"]:checked')?.value || "unpaid";
    if (!paidInput) return;
    if (sel === "partial") paidInput.disabled = false;
    else { paidInput.value=""; paidInput.disabled = true; }
  }
  payRadios.forEach(r=> r.addEventListener("change", syncPayUI));
  syncPayUI();
  payCurrency?.addEventListener("change", recalcBillTotal);
  refreshFxBadgeDisplay();
  tbody?.querySelectorAll("tr").forEach((tr) => {
    updateRowQtyWarning(tr);
    updateRowCostWarning(tr);
  });

  // ======================================================================
  // SAVE handler
  // ======================================================================

  
  document.getElementById("btnSave")?.addEventListener("click", async ()=>{
  saveErr.hidden = true; provErr.hidden = true;

  const pid = (provIdEl.value || "").trim();
  if (!pid){
    provErr.textContent = "الرجاء اختيار مورد من القائمة.";
    provErr.hidden = false;
    provInput?.focus();
    return;
  }

  // Build items
  const rows = [...(tbody?.querySelectorAll("tr") || [])];
  if (!rows.length){
    saveErr.textContent = "أضف منتجاً واحداً على الأقل.";
    saveErr.hidden = false;
    return;
  }

  rows.forEach(updateRowQtyWarning);
  const firstInvalidQtyRow = rows.find(tr => !isRowQtyValid(tr));
  if (firstInvalidQtyRow){
    saveErr.textContent = "تعذر حفظ الفاتورة , بعض المنتجات لا تملك كميات";
    saveErr.hidden = false;
    firstInvalidQtyRow.querySelector('input[name="qty[]"]')?.focus();
    return;
  }

  rows.forEach(updateRowCostWarning);
  const invalidCostRows = invalidCostRowNumbers(rows);
  if (invalidCostRows.length){
    const confirmed = await confirmSaveWithInvalidCosts(invalidCostRows);
    if (!confirmed){
      rows[invalidCostRows[0] - 1]?.querySelector('input[name="cost[]"]')?.focus();
      return;
    }
  }

  const items = rows.map(tr=>{
    const product_id = parseInt(tr.dataset.pid, 10);
    const qty_raw = String(tr.querySelector('input[name="qty[]"]').value || "0");
    const cost = String(tr.querySelector('input[name="cost[]"]').value || "0");
    const price_syp = String(tr.querySelector('input[name="price_syp[]"]')?.value || "");
    const price_usd = String(tr.querySelector('input[name="price_usd[]"]')?.value || "");
    const totalCostInput = tr.querySelector('input[name="total_cost[]"]');
    const total_cost_el = String(totalCostInput?.value || "");
    const totalCostIsAuto = totalCostInput?.dataset?.auto === "1";
    const unit_index = tr.querySelector('select[name="qty_unit[]"]').value === "u2" ? 2 : 1;
    const currency = (tr.querySelector('input.cur-hidden')?.value || tr.querySelector('select.cur-ui')?.value || "SYP").toUpperCase();
    const price = currency === "USD" ? (price_usd || "0") : (price_syp || "0");
    const row = { product_id, unit_index, qty_raw, cost, price, currency, price_syp, price_usd };
    if (total_cost_el.trim().length && !totalCostIsAuto) row.total_cost = total_cost_el;
    return row;
  });

  // Pay
  const status = document.querySelector('input[name="pay"]:checked')?.value || "unpaid";
  const paid_amount = (paidInput?.value || "0");

  const container_code = document.getElementById("containerSelect")?.value || "store";

  const moneyContainerSelect = document.getElementById("moneyContainerSelect");

  const money_container_id = parseInt(moneyContainerSelect?.value || "0", 10) || null;

  if (!money_container_id){
    saveErr.textContent = "اختر صندوق الدفع أولاً.";
    saveErr.hidden = false;
    return;
  }

  const payload = {
    provider: { id: parseInt(pid, 10) },   // ONLY existing providers
    container: container_code,
    money_container_id,
    currency_code: (document.getElementById("payCurrency")?.value || "SYP").trim().toUpperCase(),
    items,
    pay: { status, paid_amount }
  };

  try{
    const res = await fetch(SAVE_URL, {
      method: "POST",
      headers: { "Content-Type":"application/json", "X-CSRFToken": getCsrf() },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (!data.ok){
      let msg = data.error || "فشل الحفظ";
      if (/qty\s*must\s*be\s*>\s*0/i.test(String(msg))){
        msg = "تعذر حفظ الفاتورة , بعض المنتجات لا تملك كميات";
      }
      saveErr.hidden = false;
      saveErr.textContent = msg;
      return;
    }
    window.location.href = LIST_URL; // success
  }catch{
    saveErr.hidden = false;
    saveErr.textContent = "فشل الاتصال بالخادم.";
  }
});

  function getCsrf(){
    const m = document.cookie.match(/(?:^|;)\s*csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : '';
  }
})();
