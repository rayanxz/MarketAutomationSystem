// static/js/billing_add_bill.js
(() => {
  // ====== Provider AC (must select existing) ======
  const provInput = document.getElementById("provInput");
  const provList  = document.getElementById("provList");
  const provIdEl  = document.getElementById("provId");
  const provErr   = document.getElementById("provErr");
  const serialEl  = document.getElementById("billSerial");
  const serialErr = document.getElementById("serialErr");
  const saveErr   = document.getElementById("saveErr");

  const API_PROV = (window.__BILLING__?.providersAcUrl || "/manager/billing/api/providers/ac/").replace(/\/+$/,"/");

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
    const q = (provInput.value || "").trim();
    provIdEl.value = ""; // typing clears selection
    if (!q) { clearProvList(); return; }
    try{
      const r = await fetch(`${API_PROV}?q=${encodeURIComponent(q)}`, {headers:{"Accept":"application/json"}});
      const d = await r.json();
      const items = (d && d.ok) ? (d.items || []) : [];
      provItems = items;
      if (!items.length){ clearProvList(); return; }
      provList.innerHTML = items.map((it,i)=>`<li data-i="${i}">${it.name}</li>`).join("");
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

  provInput?.addEventListener("input", debounce(provSearch, 180));
  provInput?.addEventListener("focus", provSearch);
  provInput?.addEventListener("blur", () => setTimeout(clearProvList, 120));
  provInput?.addEventListener("keydown", (e)=>{
    const hasList = !provList.hidden && provList.querySelectorAll("li").length > 0;
    if (e.key === "Escape"){ clearProvList(); return; }
    if (!hasList) return;
    if (["ArrowDown","Tab"].includes(e.key) && !e.shiftKey){ e.preventDefault(); setProvActive(provActive+1); }
    else if (["ArrowUp"].includes(e.key) || (e.key==="Tab" && e.shiftKey)){ e.preventDefault(); setProvActive(provActive-1); }
    else if (e.key==="Enter"){ e.preventDefault(); const it = provItems[provActive>=0?provActive:0]; if (it) pickProv(it); }
  });

  // ====== Product search / table (your original, trimmed where not needed) ======
  const q        = document.getElementById("prodQ");
  const sug      = document.getElementById("prodSug");
  const btnAdd   = document.getElementById("btnAddProd");
  const tbody    = document.getElementById("billBody");
  const totalBox = document.getElementById("billTotalBox");
  const API_SEARCH = (document.body.dataset.urlApiSearch || "/manager/products/api/search/").replace(/\/+$/, "/");

  let mode = "name";
  document.querySelectorAll('input[name="prodMode"]').forEach(r => {
    if (r.checked) mode = r.value;
    r.addEventListener("change", () => { mode = r.value; clearSug(); q.focus(); });
  });

  const debounceTimer = (fn, ms=180)=>{ let t; return (...a)=>{ clearTimeout(t); t=setTimeout(()=>fn(...a),ms);} };
  const debounce = debounceTimer;
  const num = v => { const n = parseFloat(String(v ?? "").trim().replace(",", ".")); return Number.isFinite(n) ? n : 0; };
  const fmt2 = v => (Number(v || 0)).toFixed(2);
  const onlyProducts = items => (items || []).filter(x => (x.type ?? "product") === "product");

  let lastItems = []; let activeIndex = -1;
  function clearSug(){ sug.hidden=true; sug.innerHTML=""; lastItems=[]; activeIndex=-1; }
  function setActive(i){ const lis=[...sug.querySelectorAll("li")]; if(!lis.length){ activeIndex=-1; return;}
    activeIndex=((i%lis.length)+lis.length)%lis.length; lis.forEach((li,idx)=>li.classList.toggle("active", idx===activeIndex)); }

  function renderSug(items){
    const prods = onlyProducts(items);
    if (!prods.length){ clearSug(); return; }
    let html = "";
    prods.slice(0,8).forEach((p,i)=>{
      const path = `${p.col_name || p.col_code || ""}${(p.set_name || p.set_code) ? " · " + (p.set_name || p.set_code) : ""}`;
      html += `<li data-i="${i}"><span>📦</span><span>${p.name}</span><span style="margin-inline-start:auto;color:#6b7280;font-size:12px;">${path} — ${p.code || ""}</span></li>`;
    });
    sug.innerHTML = html; sug.hidden = false; setActive(0);
    [...sug.querySelectorAll("li")].forEach((li,i)=>{
      li.addEventListener("mouseenter", ()=> setActive(i));
      li.addEventListener("mousedown", e=>{ e.preventDefault(); const it=onlyProducts(lastItems)[i]; if(it) pick(it); });
    });
  }

  const doSearch = async ()=>{
    const val = (q.value || "").trim(); if(!val){ clearSug(); return; }
    const url = `${API_SEARCH}?mode=${encodeURIComponent(mode)}&q=${encodeURIComponent(val)}`;
    const r = await fetch(url, { headers: { "Accept": "application/json" } });
    if (!r.ok) { clearSug(); return; }
    const d = await r.json();
    if (!d.ok) { clearSug(); return; }
    lastItems = d.items || [];
    if (mode==="barcode"){ clearSug(); return; }
    renderSug(lastItems);
  };
  const onType = debounce(doSearch,180);

  q?.addEventListener("input", onType);
  q?.addEventListener("focus", onType);
  q?.addEventListener("blur", ()=> setTimeout(clearSug,120));
  q?.addEventListener("keydown", async (e)=>{
    if (e.key==="Escape"){ clearSug(); return; }
    const hasList = !sug.hidden && sug.querySelectorAll("li").length>0;

    if (mode==="barcode"){
      if (e.key==="Enter"){
        e.preventDefault();
        const val=(q.value||"").trim(); if(!val) return;
        const r=await fetch(`${API_SEARCH}?mode=barcode&q=${encodeURIComponent(val)}`, {headers: {"Accept":"application/json"}});
        const d=r.ok?await r.json():{ok:false};
        const it=d.ok?onlyProducts(d.items)[0]:null;
        if (it) pick(it);
      }
      return;
    }

    if (hasList && (e.key==="Tab" || e.key==="ArrowDown" || e.key==="ArrowUp")){
      e.preventDefault();
      const delta = (e.key==="ArrowDown" || (!e.shiftKey && e.key==="Tab")) ? 1 : -1 ;
      setActive(activeIndex + delta);
      return;
    }
    if (e.key==="Enter"){
      e.preventDefault();
      const val=(q.value||"").trim(); if(!val) return;
      if (hasList && activeIndex>=0){
        const it=onlyProducts(lastItems)[activeIndex]; if (it) pick(it); return;
      }
      const r=await fetch(`${API_SEARCH}?mode=${encodeURIComponent(mode)}&q=${encodeURIComponent(val)}`, {headers: {"Accept":"application/json"}});
      const d=r.ok?await r.json():{ok:false};
      const it=d.ok?onlyProducts(d.items)[0]:null;
      if (it) pick(it);
    }
  });

  document.getElementById("btnAddProd")?.addEventListener("click", async ()=>{
    const li = sug.querySelector("li");
    if (li && !sug.hidden){
      const idx = activeIndex>=0?activeIndex:0;
      const it = onlyProducts(lastItems)[idx];
      if (it){ pick(it); return; }
    }
    const val=(q.value||"").trim(); if(!val) return;
    const r=await fetch(`${API_SEARCH}?mode=${encodeURIComponent(mode)}&q=${encodeURIComponent(val)}`, {headers: {"Accept":"application/json"}});
    const d=r.ok?await r.json():{ok:false};
    const it=d.ok?onlyProducts(d.items)[0]:null;
    if (it) pick(it);
  });

  function pick(prod){
    clearSug(); q.value="";
    const exists = tbody.querySelector(`tr[data-pid="${prod.id}"]`);
    if (exists){ exists.querySelector('input[name="qty[]"]')?.focus(); return; }
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
    tr.innerHTML = `
      <td class="pname">${prod.name}</td>
      <td><input name="cost[]" class="input" type="number" step="0.01" value="${prod.cost ?? ""}"></td>
      <td><input name="price[]" class="input" type="number" step="0.01" value="${prod.price ?? ""}"></td>
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
    tr.querySelector(".btn-del").addEventListener("click", ()=>{ tr.remove(); recalcBillTotal(); });
    tr.addEventListener("input", handleRowChange);
    tr.addEventListener("change", handleRowChange);
    tbody.appendChild(tr);
    tr.querySelector('input[name="qty[]"]')?.focus();
    recalcBillTotal();
  }

  function handleRowChange(e){
    const nm = e.target.name || "";
    if (nm === "qty[]" || nm === "cost[]" || nm === "total_cost[]" || nm === "qty_unit[]"){ recalcBillTotal(); }
  }

  function recalcBillTotal(){
    let total = 0;
    tbody.querySelectorAll("tr").forEach(tr=>{
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
    totalBox.textContent = fmt2(total);
  }

  // Pay controls
  (() => {
    const paidInput = document.getElementById("paidAmount");
    const radios = document.querySelectorAll('input[name="pay"]');
    function syncPayUI(){
      const sel = document.querySelector('input[name="pay"]:checked')?.value || "unpaid";
      if (sel === "partial") paidInput.disabled = false;
      else { paidInput.value=""; paidInput.disabled = true; }
    }
    radios.forEach(r=> r.addEventListener("change", syncPayUI));
    syncPayUI();
  })();

  // ====== Save handler ======
  const SAVE_URL = (window.__BILLING__?.saveBillUrl || "/manager/billing/api/bill/save/").replace(/\/+$/,"/");

  document.getElementById("btnSave")?.addEventListener("click", async ()=>{
    saveErr.hidden = true; serialErr.hidden = true; provErr.hidden = true;

    const pid = (provIdEl.value || "").trim();
    if (!pid){ provErr.textContent = "الرجاء اختيار مورد من القائمة."; provErr.hidden = false; provInput.focus(); return; }

    // Optional manual serial (digits only)
    const serialRaw = (serialEl?.value || "").trim();
    let serial = null;
    if (serialRaw){
      if (!/^\d+$/.test(serialRaw)){ serialErr.textContent = "أرقام فقط."; serialErr.hidden = false; serialEl.focus(); return; }
      serial = parseInt(serialRaw, 10);
      if (!Number.isFinite(serial) || serial <= 0){ serialErr.textContent = "رقم غير صالح."; serialErr.hidden = false; serialEl.focus(); return; }
    }

    // Build items
    const rows = [...tbody.querySelectorAll("tr")];
    if (!rows.length){ saveErr.textContent = "أضف منتجاً واحداً على الأقل."; saveErr.hidden = false; return; }

    const items = rows.map(tr=>{
      const product_id = parseInt(tr.dataset.pid, 10);
      const qty_raw = String(tr.querySelector('input[name="qty[]"]').value || "0");
      const cost = String(tr.querySelector('input[name="cost[]"]').value || "0");
      const price = String(tr.querySelector('input[name="price[]"]').value || "0");
      const total_cost_el = tr.querySelector('input[name="total_cost[]"]').value;
      const unit_index = tr.querySelector('select[name="qty_unit[]"]').value === "u2" ? 2 : 1;
      const row = { product_id, unit_index, qty_raw, cost, price };
      if (total_cost_el && total_cost_el.trim().length) row.total_cost = String(total_cost_el);
      return row;
    });

    // Pay
    const status = document.querySelector('input[name="pay"]:checked')?.value || "unpaid";
    const paid_amount = document.getElementById("paidAmount").value || "0";

    const payload = {
      provider: { id: parseInt(pid, 10) },           // ONLY existing providers
      items,
      pay: { status, paid_amount },
    };
    if (serial !== null) payload.serial = serial;     // optional manual serial

    try{
      const res = await fetch(SAVE_URL, {
        method: "POST",
        headers: { "Content-Type":"application/json", "X-CSRFToken": getCsrf() },
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (!data.ok){
        const msg = data.error || "فشل الحفظ";
        if (msg.includes("serial")) serialErr.hidden = false, serialErr.textContent = msg;
        else saveErr.hidden = false, saveErr.textContent = msg;
        return;
      }
      // success → go back to list
      window.location.href = "{% url 'billing_list' %}";
    }catch(e){
      saveErr.hidden = false; saveErr.textContent = "فشل الاتصال بالخادم.";
    }
  });

  function getCsrf(){
    const m = document.cookie.match(/(?:^|;)\s*csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : '';
  }

  // small util
  function debounce(fn, ms=180){ let t; return (...a)=>{ clearTimeout(t); t=setTimeout(()=>fn(...a), ms);} }
})();