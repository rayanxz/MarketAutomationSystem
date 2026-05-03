// static/js/billing_add_bill.js
(() => {
  "use strict";

  // ====== DOM ======
  // Provider AC
  const provInput = document.getElementById("provInput");
  const provList  = document.getElementById("provList");
  const provIdEl  = document.getElementById("provId");
  const provErr   = document.getElementById("provErr");

  // Bill public ID preview / errors
 
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
  const settleCurLabel = document.getElementById("settleCurLabel");

  // Pay widgets
  const payRadios = document.querySelectorAll('input[name="pay"]');
  const payMethodRadios = document.querySelectorAll('input[name="payMethod"]');
  const payUnpaidRadio = document.getElementById("payUnpaid");
  const payPartialRadio = document.getElementById("payPartial");
  const payPaidRadio = document.getElementById("payPaid");
  const payMethodsFieldset = document.getElementById("payMethodsFieldset");
  const payMethodSeparate = document.getElementById("payMethodSeparate");
  const payMethodHint = document.getElementById("payMethodHint");
  const paySypOnlyInput = document.getElementById("paySypOnly");
  const payUsdOnlyInput = document.getElementById("payUsdOnly");
  const paySeparateSypInput = document.getElementById("paySeparateSyp");
  const paySeparateUsdInput = document.getElementById("paySeparateUsd");
  const payMixedSypInput = document.getElementById("payMixedSyp");
  const payMixedUsdInput = document.getElementById("payMixedUsd");
  const moneyContainerSelect = document.getElementById("moneyContainerSelect");
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
  const round2 = (v) => {
    const n = Number(v ?? 0);
    if (!Number.isFinite(n)) return 0;
    return Math.round((n + Number.EPSILON) * 100) / 100;
  };
  const moneyEq = (a, b) => round2(a) === round2(b);
  const moneyGt = (a, b) => round2(a) > round2(b);
  const fmt2 = v => round2(v).toFixed(2);
  const formatDisplay2 = (v) => {
    const n = round2(v);
    if (!Number.isFinite(n)) return "0";
    return formatMoney(n);
  };
  const readFxRate = () => {
    const fxVal = num(BILLING.fxSypPerUsdRaw || "");
    return (Number.isFinite(fxVal) && fxVal > 0) ? round2(fxVal) : null;
  };
  const payState = {
    mixedLastEdited: "syp",
    syncingMixed: false,
    totals: {
      totalSyp: 0,
      totalUsd: 0,
      settlementSyp: 0,
      settlementUsd: 0,
      settlementSelected: 0,
      settlementCurrency: "SYP",
      fx: null,
    },
  };
  const toAmount = (v) => {
    const n = num(v);
    if (!Number.isFinite(n) || n <= 0) return 0;
    return round2(n);
  };
  const isZeroFinancialTotal = (totals) => (
    moneyEq(totals?.totalSyp || 0, 0) &&
    moneyEq(totals?.totalUsd || 0, 0)
  );
  const setNumericInputValue = (input, value) => {
    if (!input) return;
    const n = Number(value);
    input.value = Number.isFinite(n) ? fmt2(n) : "0.00";
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

  function rowDefaultCostForCurrency(tr, cur){
    const raw = (String(cur || "SYP").toUpperCase() === "USD")
      ? (tr?.dataset?.costUsd ?? "")
      : (tr?.dataset?.costSyp ?? "");
    const clean = String(raw ?? "").trim();
    if (!clean.length) return "0";
    const parsed = num(clean);
    if (!Number.isFinite(parsed) || parsed === 0) return "0";
    return clean;
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
    if (d?.ok){
      const nextPublic = String(d.next_public_id || "").trim();
      if (nextPublic) {
        autoSerialBadge.textContent = nextPublic;
        autoSerialBadge.setAttribute("data-public-id", nextPublic);
      } else if (d.next_serial != null) {
        const n = String(d.next_serial).padStart(3, "0");
        autoSerialBadge.textContent = `PB-${n}`;
      }
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
      costInput.value = fmt2(lineTotal / denom);
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
      <td class="pname"><span class="pname-text truncate-cell"></span></td>
      <td class="cost-cell"><input name="cost[]" class="input numeric-math" data-math-display-max-decimals="2" data-math-max-decimals="2" type="number" step="0.01" value="${costVal}"></td>
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
          <input name="price_syp[]" class="input price-syp numeric-math" data-math-display-max-decimals="2" data-math-max-decimals="2" type="number" step="0.01" value="${priceSypVal}" ${prod.allow_syp_sales ? "" : "disabled"}>
        </div>
      </td>
      <td class="usd-price-cell">
        <div class="price-wrap">
          <button type="button" class="btn btn-fx btn-fx-usd">FX</button>
          <input name="price_usd[]" class="input price-usd numeric-math" data-math-display-max-decimals="2" data-math-max-decimals="2" type="number" step="0.01" value="${priceUsdVal}" ${prod.allow_usd_sales ? "" : "disabled"}>
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
      if (costInput) {
        costInput.value = rowDefaultCostForCurrency(tr, sel);
        costInput.dataset.auto = "1";
      }
      syncRowCostAndTotal(tr, "cost");
      updateRowCostWarning(tr);
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
      priceSypInput.value = fmt2(num(priceUsdInput.value) * fxVal);
      priceSypInput.dataset.auto = "0";
    });

    tr.querySelector(".btn-fx-usd")?.addEventListener("click", () => {
      const fxVal = readFxRate();
      if (!fxVal) return;
      if (!priceUsdInput || priceUsdInput.disabled) return;
      if (!hasNonZeroValue(priceSypInput?.value)) return;
      priceUsdInput.value = fmt2(num(priceSypInput.value) / fxVal);
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
      if (overrideRaw.trim().length) line = round2(num(overrideRaw));
      else line = round2(qty * cost * (isU2 ? (cf || 1) : 1));
      if (Number.isFinite(line)) {
        if (cur === "USD") totalUsd = round2(totalUsd + line);
        else totalSyp = round2(totalSyp + line);
      }
    });

    if (totalSypBox) totalSypBox.textContent = formatDisplay2(totalSyp);
    if (totalUsdBox) totalUsdBox.textContent = formatDisplay2(totalUsd);

    const fxVal = readFxRate();
    const hasFx = Number.isFinite(fxVal) && fxVal > 0;
    const canConvert = hasFx || moneyEq(totalSyp, 0) || moneyEq(totalUsd, 0);
    const settlementSyp = canConvert ? round2(totalSyp + (hasFx ? (totalUsd * fxVal) : 0)) : Number.NaN;
    const settlementUsd = canConvert ? round2(totalUsd + (hasFx ? (totalSyp / fxVal) : 0)) : Number.NaN;

    const settleCur = (payCurrency?.value || "SYP").toUpperCase();
    if (settleCurLabel) settleCurLabel.textContent = settleCur;
    const settlementSelected = settleCur === "USD" ? settlementUsd : settlementSyp;
    if (totalBox) totalBox.textContent = Number.isFinite(settlementSelected) ? formatDisplay2(settlementSelected) : "—";

    const isZeroTotal = isZeroFinancialTotal({ totalSyp, totalUsd });
    payState.totals = {
      totalSyp,
      totalUsd,
      settlementSyp,
      settlementUsd,
      settlementSelected: Number.isFinite(settlementSelected) ? round2(settlementSelected) : 0,
      settlementCurrency: settleCur,
      fx: hasFx ? fxVal : null,
      isZeroTotal,
    };
    syncPayUI();

    if (grandTotals){
      if (Number.isFinite(settlementSyp) && Number.isFinite(settlementUsd)){
        grandTotals.textContent = `إجمالي بالتحويل: ${formatDisplay2(settlementSyp)} SYP | ${formatDisplay2(settlementUsd)} USD`;
      } else {
        grandTotals.textContent = "إجمالي بالتحويل: يتطلب سعر صرف صحيح";
      }
    }
  }

  // ======================================================================
  // PAY controls
  // ======================================================================
  const selectedPayStatus = () => document.querySelector('input[name="pay"]:checked')?.value || "unpaid";
  const selectedPayMethod = () => document.querySelector('input[name="payMethod"]:checked')?.value || "syp_only";

  function showActivePayMethodPanel(method){
    document.querySelectorAll("[data-method-panel]").forEach((panel) => {
      panel.classList.toggle("active", panel.dataset.methodPanel === method);
    });
  }

  function toSettlementAmount(amountSyp, amountUsd){
    const settleCur = (payState.totals.settlementCurrency || "SYP").toUpperCase();
    const fxVal = payState.totals.fx;
    if (settleCur === "USD") {
      if (moneyGt(amountSyp, 0)) {
        if (!(fxVal > 0)) return null;
        return round2(amountUsd + (amountSyp / fxVal));
      }
      return round2(amountUsd);
    }
    if (moneyGt(amountUsd, 0)) {
      if (!(fxVal > 0)) return null;
      return round2(amountSyp + (amountUsd * fxVal));
    }
    return round2(amountSyp);
  }

  function readCurrentPaymentAmounts(status, method){
    const totals = payState.totals;
    let amountSyp = 0;
    let amountUsd = 0;
    if (method === "syp_only") {
      amountSyp = status === "paid" ? totals.settlementSyp : toAmount(paySypOnlyInput?.value);
    } else if (method === "usd_only") {
      amountUsd = status === "paid" ? totals.settlementUsd : toAmount(payUsdOnlyInput?.value);
    } else if (method === "separate") {
      amountSyp = totals.totalSyp;
      amountUsd = totals.totalUsd;
    } else {
      amountSyp = toAmount(payMixedSypInput?.value);
      amountUsd = toAmount(payMixedUsdInput?.value);
    }
    return { amountSyp, amountUsd };
  }

  function partialRealtimeValidationHint(method, amountSyp, amountUsd){
    const totals = payState.totals;
    const settlementTotal = totals.settlementSelected;

    if (method === "mixed") {
      if (!moneyGt(totals.totalSyp, 0) || !moneyGt(totals.totalUsd, 0)) {
        return "الدفع المختلط الجزئي يتطلب وجود إجمالي بعملتي SYP و USD.";
      }
      if (!moneyGt(amountSyp, 0) || !moneyGt(amountUsd, 0)) {
        return "في الدفع المختلط الجزئي يجب إدخال مبلغين أكبر من الصفر.";
      }
    }

    const paidSettlement = toSettlementAmount(amountSyp, amountUsd);
    if (paidSettlement == null || !Number.isFinite(paidSettlement)) return null;
    if (moneyGt(paidSettlement, settlementTotal)) {
      return "مبلغ الدفع الجزئي لا يمكن أن يتجاوز إجمالي التسوية.";
    }
    if (moneyEq(paidSettlement, settlementTotal)) {
      return "يمكنك اختيار خيار (دفع كامل)";
    }
    return null;
  }

  function syncMixedFullFrom(source){
    if (payState.syncingMixed) return;
    const fxVal = payState.totals.fx;
    const targetSyp = payState.totals.settlementSyp;
    const targetUsd = payState.totals.settlementUsd;
    if (!(fxVal > 0) || !Number.isFinite(targetSyp) || !Number.isFinite(targetUsd)) return;
    payState.syncingMixed = true;
    if (source === "usd") {
      let usd = toAmount(payMixedUsdInput?.value);
      usd = Math.min(usd, targetUsd);
      const syp = Math.max(0, targetSyp - (usd * fxVal));
      setNumericInputValue(payMixedUsdInput, usd);
      setNumericInputValue(payMixedSypInput, syp);
    } else {
      let syp = toAmount(payMixedSypInput?.value);
      syp = Math.min(syp, targetSyp);
      const usd = Math.max(0, (targetSyp - syp) / fxVal);
      setNumericInputValue(payMixedSypInput, syp);
      setNumericInputValue(payMixedUsdInput, usd);
    }
    payState.syncingMixed = false;
  }

  function syncPayUI(){
    let status = selectedPayStatus();
    let method = selectedPayMethod();
    const isZeroTotal = !!payState.totals.isZeroTotal;
    const isUnpaid = status === "unpaid";
    const isPartial = status === "partial";
    const isPaid = status === "paid";
    const needsFx = moneyGt(payState.totals.totalSyp, 0) && moneyGt(payState.totals.totalUsd, 0);
    const hasFx = payState.totals.fx > 0;

    if (isZeroTotal) {
      if (payUnpaidRadio) payUnpaidRadio.checked = true;
      status = "unpaid";
      payRadios.forEach((r) => { r.disabled = true; });
      payMethodRadios.forEach((r) => { r.disabled = true; });
      setNumericInputValue(paySypOnlyInput, 0);
      setNumericInputValue(payUsdOnlyInput, 0);
      setNumericInputValue(paySeparateSypInput, 0);
      setNumericInputValue(paySeparateUsdInput, 0);
      setNumericInputValue(payMixedSypInput, 0);
      setNumericInputValue(payMixedUsdInput, 0);
      if (payMethodsFieldset) {
        payMethodsFieldset.disabled = true;
        payMethodsFieldset.classList.add("is-disabled");
      }
      if (moneyContainerSelect) {
        // Keep container options visible in zero-total mode.
        // Financial enforcement remains backend-side (status/payment-driven).
        moneyContainerSelect.disabled = false;
      }
      if (payMethodHint) {
        payMethodHint.textContent = "الإجمالي صفري: الفاتورة غير مالية ولا يوجد دفع عند الإنشاء.";
      }
      showActivePayMethodPanel(method);
      return;
    }

    payRadios.forEach((r) => { r.disabled = false; });
    payMethodRadios.forEach((r) => { r.disabled = false; });
    if (moneyContainerSelect) moneyContainerSelect.disabled = false;

    if (payMethodsFieldset) {
      payMethodsFieldset.disabled = isUnpaid;
      payMethodsFieldset.classList.toggle("is-disabled", isUnpaid);
    }

    if (payMethodSeparate) {
      payMethodSeparate.disabled = isPartial;
      payMethodSeparate.closest("label")?.classList.toggle("muted", isPartial);
      if (isPartial && method === "separate") {
        const fallback = document.getElementById("payMethodMixed") || document.getElementById("payMethodSyp");
        if (fallback) fallback.checked = true;
        method = selectedPayMethod();
      }
    }

    showActivePayMethodPanel(method);

    setNumericInputValue(paySeparateSypInput, payState.totals.totalSyp);
    setNumericInputValue(paySeparateUsdInput, payState.totals.totalUsd);

    if (isPaid) {
      setNumericInputValue(paySypOnlyInput, payState.totals.settlementSyp);
      setNumericInputValue(payUsdOnlyInput, payState.totals.settlementUsd);
      if (!toAmount(payMixedSypInput?.value) && !toAmount(payMixedUsdInput?.value)) {
        setNumericInputValue(payMixedSypInput, payState.totals.settlementSyp);
        setNumericInputValue(payMixedUsdInput, 0);
        payState.mixedLastEdited = "syp";
      }
      if (method === "mixed") syncMixedFullFrom(payState.mixedLastEdited === "usd" ? "usd" : "syp");
      if (payMethodHint) payMethodHint.textContent = "في وضع الدفع الكامل: يجب أن تغطي المدفوعات كامل إجمالي التسوية.";
    } else if (isPartial) {
      if (payMethodHint) {
        const { amountSyp, amountUsd } = readCurrentPaymentAmounts(status, method);
        payMethodHint.textContent =
          partialRealtimeValidationHint(method, amountSyp, amountUsd)
          || "في الدفع الجزئي يمكنك إدخال جزء من القيمة، والمتبقي يصبح ديناً على المورد.";
      }
    } else if (payMethodHint) {
      payMethodHint.textContent = "حالة غير مدفوع: خيارات التسديد معطلة حتى اختيار دفع كامل أو جزئي.";
    }

    const methodInputs = [paySypOnlyInput, payUsdOnlyInput, payMixedSypInput, payMixedUsdInput];
    methodInputs.forEach((el) => {
      if (!el) return;
      el.readOnly = isPaid && selectedPayMethod() !== "mixed";
      el.disabled = isUnpaid;
    });
    if (payMixedSypInput) payMixedSypInput.readOnly = isUnpaid;
    if (payMixedUsdInput) payMixedUsdInput.readOnly = isUnpaid;

    if (payMethodHint && needsFx && !hasFx && !isUnpaid) {
      payMethodHint.textContent = "لا يمكن حساب التسوية متعددة العملات بدون سعر صرف صحيح.";
    }
  }

  function buildPaymentPayload(){
    const status = selectedPayStatus();
    const method = selectedPayMethod();
    const totals = payState.totals;
    if (totals.isZeroTotal) {
      return {
        ok: true,
        pay: {
          status: "unpaid",
          method: "none",
          amount_syp: "0",
          amount_usd: "0",
          paid_amount: "0",
          settlement_total: "0",
          fx_rate: totals.fx > 0 ? String(totals.fx) : "",
          non_financial: true,
        },
      };
    }
    const needsFx = moneyGt(totals.totalSyp, 0) && moneyGt(totals.totalUsd, 0);
    if (status !== "unpaid" && needsFx && !(totals.fx > 0)) {
      return { ok: false, error: "لا يمكن إتمام الدفع قبل ضبط سعر الصرف بشكل صحيح." };
    }

    if (status === "unpaid") {
      return {
        ok: true,
        pay: {
          status,
          method: "none",
          amount_syp: "0",
          amount_usd: "0",
          paid_amount: "0",
          settlement_total: fmt2(totals.settlementSelected),
          fx_rate: totals.fx > 0 ? String(totals.fx) : "",
        },
      };
    }

    if (status === "partial" && method === "separate") {
      return { ok: false, error: "خيار الدفع المنفصل متاح للدفع الكامل فقط." };
    }

    let { amountSyp, amountUsd } = readCurrentPaymentAmounts(status, method);
    if (method === "separate" && status !== "paid") {
      return { ok: false, error: "خيار الدفع المنفصل مخصص للدفع الكامل." };
    }
    if (method === "mixed" && status === "paid") {
      syncMixedFullFrom(payState.mixedLastEdited === "usd" ? "usd" : "syp");
      amountSyp = toAmount(payMixedSypInput?.value);
      amountUsd = toAmount(payMixedUsdInput?.value);
    }

    if (amountSyp < 0 || amountUsd < 0) {
      return { ok: false, error: "قيمة الدفع لا يمكن أن تكون سالبة." };
    }

    const paidSettlement = toSettlementAmount(amountSyp, amountUsd);
    if (paidSettlement == null || !Number.isFinite(paidSettlement)) {
      return { ok: false, error: "تعذر احتساب قيمة التسديد. تحقق من سعر الصرف." };
    }

    const settlementTotal = totals.settlementSelected;
    if (status === "partial") {
      if (method === "mixed") {
        if (!moneyGt(totals.totalSyp, 0) || !moneyGt(totals.totalUsd, 0)) {
          return { ok: false, error: "الدفع المختلط الجزئي يتطلب وجود إجمالي بعملتي SYP و USD." };
        }
        if (!moneyGt(amountSyp, 0) || !moneyGt(amountUsd, 0)) {
          return { ok: false, error: "في الدفع المختلط الجزئي يجب إدخال مبلغين أكبر من الصفر." };
        }
      }
      if (!moneyGt(paidSettlement, 0)) {
        return { ok: false, error: "عند اختيار دفع جزئي يجب إدخال مبلغ أكبر من الصفر." };
      }
      if (moneyGt(paidSettlement, settlementTotal)) {
        return { ok: false, error: "مبلغ الدفع الجزئي لا يمكن أن يتجاوز إجمالي التسوية." };
      }
      if (moneyEq(paidSettlement, settlementTotal)) {
        return { ok: false, error: "يمكنك اختيار خيار (دفع كامل)" };
      }
    }
    if (status === "paid") {
      if (!moneyEq(paidSettlement, settlementTotal)) {
        return { ok: false, error: "الدفع الكامل يتطلب تغطية كامل إجمالي التسوية." };
      }
    }

    return {
      ok: true,
      pay: {
        status,
        method,
        amount_syp: fmt2(amountSyp),
        amount_usd: fmt2(amountUsd),
        paid_amount: fmt2(paidSettlement),
        settlement_total: fmt2(settlementTotal),
        fx_rate: totals.fx > 0 ? String(totals.fx) : "",
      },
    };
  }

  payRadios.forEach((r) => r.addEventListener("change", syncPayUI));
  payMethodRadios.forEach((r) => r.addEventListener("change", syncPayUI));
  payMixedSypInput?.addEventListener("input", () => {
    payState.mixedLastEdited = "syp";
    if (selectedPayStatus() === "paid" && selectedPayMethod() === "mixed") syncMixedFullFrom("syp");
    syncPayUI();
  });
  payMixedUsdInput?.addEventListener("input", () => {
    payState.mixedLastEdited = "usd";
    if (selectedPayStatus() === "paid" && selectedPayMethod() === "mixed") syncMixedFullFrom("usd");
    syncPayUI();
  });
  paySypOnlyInput?.addEventListener("input", syncPayUI);
  payUsdOnlyInput?.addEventListener("input", syncPayUI);
  payCurrency?.addEventListener("change", recalcBillTotal);
  tbody?.querySelectorAll("tr").forEach((tr) => {
    updateRowQtyWarning(tr);
    updateRowCostWarning(tr);
  });
  syncPayUI();
  recalcBillTotal();

  // ======================================================================
  // SAVE handler
  // ======================================================================

  
  const saveBtn = document.getElementById("btnSave");
  let saveInFlight = false;

  saveBtn?.addEventListener("click", async ()=>{
  if (saveInFlight) return;
  saveInFlight = true; // lock immediately before any async work
  let keepLockedAfterReturn = false;
  saveErr.hidden = true; provErr.hidden = true;

  const pid = (provIdEl.value || "").trim();
  if (!pid){
    provErr.textContent = "الرجاء اختيار مورد من القائمة.";
    provErr.hidden = false;
    provInput?.focus();
    saveInFlight = false;
    if (saveBtn) saveBtn.disabled = false;
    return;
  }

  // Build items
  const rows = [...(tbody?.querySelectorAll("tr") || [])];
  if (!rows.length){
    saveErr.textContent = "أضف منتجاً واحداً على الأقل.";
    saveErr.hidden = false;
    saveInFlight = false;
    if (saveBtn) saveBtn.disabled = false;
    return;
  }

  rows.forEach(updateRowQtyWarning);
  const firstInvalidQtyRow = rows.find(tr => !isRowQtyValid(tr));
  if (firstInvalidQtyRow){
    saveErr.textContent = "تعذر حفظ الفاتورة , بعض المنتجات لا تملك كميات";
    saveErr.hidden = false;
    firstInvalidQtyRow.querySelector('input[name="qty[]"]')?.focus();
    saveInFlight = false;
    if (saveBtn) saveBtn.disabled = false;
    return;
  }

  rows.forEach(updateRowCostWarning);
  const invalidCostRows = invalidCostRowNumbers(rows);
  if (invalidCostRows.length){
    const confirmed = await confirmSaveWithInvalidCosts(invalidCostRows);
    if (!confirmed){
      rows[invalidCostRows[0] - 1]?.querySelector('input[name="cost[]"]')?.focus();
      saveInFlight = false;
      if (saveBtn) saveBtn.disabled = false;
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
  const payResult = buildPaymentPayload();
  if (!payResult.ok){
    saveErr.textContent = payResult.error || "بيانات الدفع غير صحيحة.";
    saveErr.hidden = false;
    saveInFlight = false;
    if (saveBtn) saveBtn.disabled = false;
    return;
  }
  const payPayload = payResult.pay;

  const container_code = document.getElementById("containerSelect")?.value || "store";
  const money_container_id = parseInt(moneyContainerSelect?.value || "0", 10) || null;
  const requiresMoneyContainer = payPayload?.status !== "unpaid";
  if (requiresMoneyContainer && !money_container_id){
    saveErr.textContent = "اختر صندوق الدفع أولاً.";
    saveErr.hidden = false;
    saveInFlight = false;
    if (saveBtn) saveBtn.disabled = false;
    return;
  }

  const payload = {
    provider: { id: parseInt(pid, 10) },   // ONLY existing providers
    container: container_code,
    money_container_id,
    currency_code: (document.getElementById("payCurrency")?.value || "SYP").trim().toUpperCase(),
    items,
    pay: payPayload
  };

  try{
    if (saveBtn) saveBtn.disabled = true; // disable only while request is running
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
    keepLockedAfterReturn = true; // avoid a second send window during navigation
    window.location.href = LIST_URL; // success
  }catch{
    saveErr.hidden = false;
    saveErr.textContent = "فشل الاتصال بالخادم.";
  }finally{
    if (!keepLockedAfterReturn){
      saveInFlight = false;
      if (saveBtn) saveBtn.disabled = false;
    }
  }
});

  function getCsrf(){
    const m = document.cookie.match(/(?:^|;)\s*csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : '';
  }
})();
