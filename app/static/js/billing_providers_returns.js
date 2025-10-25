// static/js/billing_providers_returns.js
(() => {
  "use strict";

  window.addEventListener("error", (e) => {
  const box = document.getElementById("saveErr");
  if (box) { box.hidden = false; box.textContent = `JavaScript error: ${e.message}`; }
  // still log to console
  console.error("JS error:", e.error || e.message);
  });

  // ====== DOM ======
  const provInput = document.getElementById("provInput");
  const provList  = document.getElementById("provList");
  const provIdEl  = document.getElementById("provId");
  const provErr   = document.getElementById("provErr");

  const saveErr   = document.getElementById("saveErr");
  const autoSerialBadge = document.getElementById("retAutoSerial");

  const q        = document.getElementById("prodQ");
  const sug      = document.getElementById("prodSug");
  const btnAdd   = document.getElementById("btnAddProd");
  const tbody    = document.getElementById("retBody");
  const totalBox = document.getElementById("retTotalBox");

  const paidInput = document.getElementById("paidAmount");
  const payRadios = document.querySelectorAll('input[name="pay"]');

  // ====== URLs / Config ======
  const CFG       = window.__RETURNS__ || {};
  const API_PROV  = (CFG.providersAcUrl || "/manager/billing/api/providers/ac/").replace(/\/+$/,"/");
  const API_SEARCH= (document.body?.dataset?.urlApiSearch || "/manager/billing/api/products/search/").replace(/\/+$/,"/");
  const SAVE_URL  = (CFG.saveReturnUrl || "/manager/billing/api/returns/save/").replace(/\/+$/,"/");
  const NEXT_URL  = (CFG.nextSerialUrl || "/manager/billing/api/returns/next-serial/").replace(/\/+$/,"/");
  const LIST_URL  = "/manager/billing/"; // back link (to bills home); change if you prefer

  const apiModeFor = (m) => (m || "name");

  // ====== Utils ======
  const debounce = (fn, ms=180)=>{ let t; return (...a)=>{ clearTimeout(t); t=setTimeout(()=>fn(...a),ms); }; };
  const num  = v => { const n = parseFloat(String(v ?? "").trim().replace(",", ".")); return Number.isFinite(n) ? n : 0; };
  const fmt2 = v => (Number(v || 0)).toFixed(2);

  const looksLikeProduct = (x) => x && typeof x === "object" && ("id" in x) && ("name" in x);

  function enhanceListAsListbox(ul){
    if (!ul) return;
    ul.setAttribute("role","listbox");
    ul.querySelectorAll("li").forEach(li=>{
      li.setAttribute("role","option");
      li.setAttribute("tabindex","-1");
    });
  }

  async function refreshAutoSerial(){
    if (!autoSerialBadge) return;
    try{
      const r = await fetch(NEXT_URL, { headers:{ "Accept":"application/json" }});
      const d = await r.json();
      if (d?.ok && d.next_serial != null){
        const n = String(d.next_serial).padStart(3, "0");
        autoSerialBadge.textContent = n;
        autoSerialBadge.setAttribute("data-serial", String(d.next_serial));
      }
    }catch{/* silent */}
  }
  refreshAutoSerial();

  // ======================================================================
  // PROVIDER AUTOCOMPLETE (copied & kept consistent with add_bill.js)
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

  function pickProv(it){
    provInput.value = it.name;
    provIdEl.value = String(it.id);
    clearProvList();
    provErr.hidden = true;
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

  async function validateProviderExact(){
    const val = (provInput?.value || "").trim();
    if (!val){ provErr.hidden = true; return; }
    if ((provIdEl.value || "").trim()) { provErr.hidden = true; return; }
    try{
      const r = await fetch(`${API_PROV}?q=${encodeURIComponent(val)}`, {headers:{"Accept":"application/json"}});
      const d = await r.json();
      const items = (d && d.ok) ? (d.items || []) : [];
      const match = items.find(it => (it.name || "").trim().toLowerCase() === val.toLowerCase());
      if (match){ pickProv(match); return; }
      provErr.textContent = "لا يوجد مورد بهذا الاسم.";
      provErr.hidden = false;
    }catch{/* don’t block save on failure here */}
  }

  provInput?.addEventListener("input", debounce(provSearch, 180));
  provInput?.addEventListener("focus", provSearch);
  provInput?.addEventListener("blur", () => { setTimeout(() => { clearProvList(); validateProviderExact(); }, 120); });
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
  // PRODUCT SEARCH + SUGGESTIONS (STRICT mode, like add_bill.js)
  // ======================================================================
  let mode = "name";
  document.querySelectorAll('input[name="prodMode"]').forEach(r => {
    if (r.checked) mode = r.value;
    r.addEventListener("change", () => { mode = r.value; clearSug(); q?.focus(); });
  });

  let lastItems = [];
  let activeIndex = -1;

  function normalize(items){
    return (items || []).filter(looksLikeProduct).map(p => ({
      id: p.id,
      name: p.name,
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
      cost: p.cost ?? ""
    }));
  }

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
    }catch{
      return { ok:false, items:[] };
    }
  }

  // STRICT: query only the selected mode (no fallbacks)
  async function searchCascade(query, mode){
    const triedIds = new Set();
    const out = [];
    const pushUnique = (arr)=> (arr || []).forEach(it => {
      if (it && it.id != null && !triedIds.has(it.id)) { triedIds.add(it.id); out.push(it); }
    });

    const start = apiModeFor(mode);
    const order = [start];

    for (const m of order){
      const res1 = await fetchSearch({ q: query, mode: m });
      if (res1.ok) pushUnique(res1.items);
      if (out.length > 0) break;
    }
    return out;
  }

  // Position the suggestion list right under the input
  const placeSug = ()=>{
    if (!q || !sug) return;
    const r = q.getBoundingClientRect();
    const s = sug.style;
    s.position = "fixed";
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
    if (!sug) return;
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

    placeSug();
  }

  const doSearch = async ()=>{
    const val = (q?.value || "").trim();
    if (!val) { clearSug(); return; }
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

  // ======================================================================
  // ROW BUILDER for RETURNS (no "price" column)
  // ======================================================================
  function pick(prod){
    clearSug(); if (q) q.value="";
    const exists = tbody?.querySelector(`tr[data-pid="${prod.id}"]`);
    if (exists){ exists.querySelector('input[name="qty[]"]')?.focus(); return; }

    const hasU2 = !!(prod.unit_secondary && String(prod.unit_secondary).trim().length);
    const cf    = prod.conversion_factor ? String(prod.conversion_factor) : "";
    const isSingleUnit = prod.matched_unit != null;
    const showU1 = !isSingleUnit || (prod.matched_unit===1);
    const showU2 = hasU2 && (!isSingleUnit || (prod.matched_unit===2));
    const lockSelect = !!isSingleUnit;

    const u1Label = prod.unit_primary_label || "الوحدة الأولى";
    const u2Label = prod.unit_secondary_label || "الوحدة الثانية";

    const tr = document.createElement("tr");
    tr.dataset.pid = String(prod.id);
    tr.dataset.cf = cf;

    tr.innerHTML = `
      <td class="pname">${prod.name}</td>
      <td><input name="cost[]" class="input" type="number" step="0.01" value="${prod.cost ?? ""}"></td>
      <td>
        <div style="display:flex; gap:6px; align-items:center;">
          <input name="qty[]" class="input" type="number" step="0.001" min="0" placeholder="0">
          <select name="qty_unit[]" class="input" style="max-width:160px;" ${lockSelect ? "disabled" : ""}>
            ${showU1 ? `<option value="u1">${u1Label}</option>` : ``}
            ${showU2 ? `<option value="u2">${u2Label}</option>` : ``}
          </select>
        </div>
      </td>
      <td><input name="total_cost[]" class="input" type="number" step="0.01" placeholder="0.00"></td>
      <td style="text-align:center;"><button type="button" class="btn-danger btn-del">✕</button></td>
      <input type="hidden" name="product_id[]" value="${prod.id}">
    `;

    tr.querySelector(".btn-del")?.addEventListener("click", ()=>{ tr.remove(); recalcTotal(); });
    tr.addEventListener("input", handleRowChange);
    tr.addEventListener("change", handleRowChange);

    tbody?.appendChild(tr);
    tr.querySelector('input[name="qty[]"]')?.focus();
    recalcTotal();
  }

  function handleRowChange(e){
    const nm = e.target.name || "";
    if (nm === "qty[]" || nm === "cost[]" || nm === "total_cost[]" || nm === "qty_unit[]"){ recalcTotal(); }
  }

  function recalcTotal(){
    let total = 0;
    tbody?.querySelectorAll("tr").forEach(tr=>{
      const qty = num(tr.querySelector('input[name="qty[]"]')?.value);
      const cost = num(tr.querySelector('input[name="cost[]"]')?.value);
      const overrideRaw = tr.querySelector('input[name="total_cost[]"]')?.value ?? "";
      const isU2 = tr.querySelector('select[name="qty_unit[]"]')?.value === "u2";
      const cf = num(tr.dataset.cf || "0");
      let line;
      if (overrideRaw.trim().length) line = num(overrideRaw);
      else line = qty * cost * (isU2 ? (cf || 1) : 1);
      if (Number.isFinite(line)) total += line;
    });
    if (totalBox) totalBox.textContent = fmt2(total);
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

  // ======================================================================
  // SAVE handler
  // ======================================================================
  document.getElementById("btnSave")?.addEventListener("click", async ()=>{
    saveErr.hidden = true; provErr.hidden = true;


    console.debug("Save clicked");

    const pid = (provIdEl.value || "").trim();
    if (!pid){ provErr.textContent = "الرجاء اختيار مورد من القائمة."; provErr.hidden = false; provInput?.focus(); return; }

    const rows = [...(tbody?.querySelectorAll("tr") || [])];
    if (!rows.length){ saveErr.textContent = "أضف منتجاً واحداً على الأقل."; saveErr.hidden = false; return; }

    const items = rows.map(tr=>{
      const product_id = parseInt(tr.dataset.pid, 10);
      const qty_raw = String(tr.querySelector('input[name="qty[]"]').value || "0");
      const cost = String(tr.querySelector('input[name="cost[]"]').value || "0");
      const total_cost_el = tr.querySelector('input[name="total_cost[]"]').value;
      const unit_index = tr.querySelector('select[name="qty_unit[]"]').value === "u2" ? 2 : 1;
      const row = { product_id, unit_index, qty_raw, cost };
      if (total_cost_el && total_cost_el.trim().length) row.total_cost = String(total_cost_el);
      return row;
    });

    const status = document.querySelector('input[name="pay"]:checked')?.value || "unpaid";
    const paid_amount = (paidInput?.value || "0");

    const payload = {
      provider: { id: parseInt(pid, 10) },
      items,
      pay: { status, paid_amount },
    };

    try{
    const res = await fetch(SAVE_URL, {
      method: "POST",
      headers: { "Content-Type":"application/json", "X-CSRFToken": getCsrf() },
      body: JSON.stringify(payload)
    });

    let data;
    const ct = res.headers.get("content-type") || "";
    if (ct.includes("application/json")){
      data = await res.json();
    } else {
      const text = await res.text();
      console.error("Non-JSON response", res.status, text);
      throw new Error(`Non-JSON ${res.status}`);
    }

    if (!data.ok){
      const msg = data.error || "فشل الحفظ";
      console.error("Server said not ok:", msg, data);
      saveErr.hidden = false; saveErr.textContent = msg; 
      return;
    }
    window.location.href = LIST_URL;
  }catch(err){
    console.error("Fetch failed:", err);
    saveErr.hidden = false; 
    saveErr.textContent = "فشل الاتصال بالخادم.";
  }
});

  function getCsrf(){
    const m = document.cookie.match(/(?:^|;)\s*csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : '';
  }
})();
