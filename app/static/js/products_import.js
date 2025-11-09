(function(){
  "use strict";

  // ---------- DOM ----------
  const s1 = document.getElementById("step1");
  const s2 = document.getElementById("step2");
  const s3 = document.getElementById("step3");
  const msg1 = document.getElementById("s1msg");
  const msg2 = document.getElementById("s2msg");
  const msg3 = document.getElementById("s3msg");

  const btnAnalyze = document.getElementById("btnAnalyze");
  const btnMap     = document.getElementById("btnMap");
  const btnBack1   = document.getElementById("btnBack1");
  const btnBack2   = document.getElementById("btnBack2");
  const btnCommit  = document.getElementById("btnCommit");

  const mapHeader = document.getElementById("mapHeader");
  const mapSample = document.getElementById("mapSample");
  const rowsBody  = document.getElementById("rowsBody");
  const statsLine = document.getElementById("statsLine");

  const fileInput  = document.getElementById("file");
  const fileType   = document.getElementById("fileType");
  const collection = document.getElementById("collection");
  const optCreateSets = document.getElementById("optCreateSets");
  const hasHeader  = document.getElementById("hasHeader");

  // Progress overlay
  let overlay = document.getElementById("importProgress");
  if (!overlay) {
    overlay = document.createElement("div");
    overlay.id = "importProgress";
    overlay.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,.35);display:none;align-items:center;justify-content:center;z-index:9999";
    overlay.innerHTML = `<div style="background:#fff;padding:16px 20px;border-radius:12px;border:1px solid #e5e7eb;min-width:260px;text-align:center">
      <div style="margin-bottom:8px;font-weight:600">جاري الفحص وإعداد القائمة…</div>
      <div id="bar" style="height:8px;border-radius:6px;background:#f3f4f6;overflow:hidden">
        <div style="width:35%;height:100%;background:#9ca3af;animation: pulse 1.2s infinite"></div>
      </div>
    </div>
    <style>@keyframes pulse{0%{transform:translateX(-60%)}50%{transform:translateX(60%)}100%{transform:translateX(-60%)}}</style>`;
    document.body.appendChild(overlay);
  }

  function showOverlay(){ overlay.style.display = "flex"; }
  function hideOverlay(){ overlay.style.display = "none"; }

  // ---------- state ----------
  let stagingId = null;
  let mappingSelects = [];
  const UNIT_WHITELIST = new Set(["غرام", "قطعة", "ليتر", "طرد", "حزمة"]);
  const clientErrs = new Map(); // {rid -> {key:msg}}

  // ---------- utils ----------
  function show(el){ el.style.display="block"; }
  function hide(el){ el.style.display="none"; }
  function notice(el, text, isErr=false){
    el.textContent = text || "";
    el.style.color = isErr ? "#b91c1c" : "#6b7280";
  }
  function getCSRF(){
    const m = document.cookie.match(/csrftoken=([^;]+)/);
    return m ? m[1] : '';
  }
  function selectedMode(){
    const el = s2.querySelector('input[name="import_mode"]:checked');
    return el ? el.value : "add_update";
  }

  // ---------- Stage 2 mapping ----------
  function mappingRow(headers, hints){
    mapHeader.innerHTML = "";
    mapSample.innerHTML = "";
    mappingSelects = [];
    const opts = [
      ["","— تجاهل —"],
      ["name","اسم المنتج"],
      ["set","المجموعة الأب"],
      ["unit_primary","الوحدة الأولى"],
      ["unit_secondary","الوحدة الثانية"],
      ["conversion_factor","عامل التحويل"],
      ["cost","الكلفة"],
      ["price","السعر"],
      ["barcodes_u1","باركودات U1"],
      ["barcodes_u2","باركودات U2"],
      ["unit_ids_u1","معرّفات U1"],
      ["unit_ids_u2","معرّفات U2"],
      ["notes","ملاحظات"]
    ];
    headers.forEach((h, i) => {
      const th = document.createElement("th");
      const top = document.createElement("div");
      top.innerHTML = `<strong>${h || ("عمود "+(i+1))}</strong> <span class="badge">${hints[i]||""}</span>`;
      const sel = document.createElement("select");
      sel.className = "input";
      sel.dataset.col = i;
      for (const [v,l] of opts){
        const o = document.createElement("option"); o.value=v; o.textContent=l; sel.appendChild(o);
      }
      th.appendChild(top); th.appendChild(sel);
      mapHeader.appendChild(th);
      mappingSelects.push(sel);
    });
    mappingSelects.forEach(sel=>{
      sel.addEventListener("change", enforceUniqueMapping);
    });
  }

  function enforceUniqueMapping(){
    const chosen = new Set();
    mappingSelects.forEach(sel => { if (sel.value) chosen.add(sel.value); });
    mappingSelects.forEach(sel=>{
      Array.from(sel.options).forEach(opt=>{
        if (!opt.value) return;
        opt.disabled = !(opt.selected || !chosen.has(opt.value));
      });
    });
  }

  function sampleRows(sample){
    mapSample.innerHTML = "";
    (sample||[]).forEach(row=>{
      const tr = document.createElement("tr");
      row.forEach(cell=>{
        const td = document.createElement("td");
        td.textContent = (cell===null || cell===undefined) ? "" : String(cell);
        tr.appendChild(td);
      });
      mapSample.appendChild(tr);
    });
  }

  function collectMapping(){
    const m = {};
    for (const sel of mappingSelects){
      if (!sel.value) continue;
      const idx = parseInt(sel.dataset.col,10);
      if (!Number.isNaN(idx)) m[sel.value] = idx;
    }
    return m;
  }

  // ---------- Step 1 ----------
  btnAnalyze.addEventListener("click", async ()=>{
    notice(msg1,"");
    const f = fileInput.files[0];
    if (!f){ notice(msg1,"اختر ملفاً أولاً.", true); return; }

    const fd = new FormData();
    fd.append("file", f);
    fd.append("file_type", fileType.value);
    fd.append("collection", collection.value);
    fd.append("has_header", hasHeader.checked ? "1":"0");

    try{
      const r = await fetch("/manager/products/import/analyze/", {
        method:"POST", headers:{"X-CSRFToken": getCSRF()}, body: fd
      });
      const j = await r.json();
      if (!j.ok){ notice(msg1, j.error || "فشل التحليل.", true); return; }
      stagingId = j.staging_id;
      mappingRow(j.headers, j.hints);
      sampleRows(j.sample);
      enforceUniqueMapping();
      hide(s1); show(s2); notice(msg2, "اضبط خريطة الأعمدة ثم تابع.");
    }catch(e){
      notice(msg1,"خطأ في الرفع/التحليل.", true);
    }
  });

  // ---------- Step 2 -> heavy stage (with overlay) ----------
  btnMap.addEventListener("click", async ()=>{
    notice(msg2,"");
    const mapping = collectMapping();

    // required: name, set, unit_primary, unit_secondary, conversion_factor
    const req = ["name","set","unit_primary","unit_secondary","conversion_factor"];
    for (const k of req){
      if (!(k in mapping)){
        notice(msg2, "الحقول الإلزامية: الاسم + المجموعة + الوحدة الأولى + الوحدة الثانية + عامل التحويل.", true);
        return;
      }
    }

    const fd = new FormData();
    fd.append("staging_id", stagingId);
    fd.append("mapping_json", JSON.stringify(mapping));
    fd.append("create_missing_sets", optCreateSets.checked ? "1":"0");
    fd.append("import_mode", selectedMode());

    try{
      showOverlay();
      const r = await fetch("/manager/products/import/map/", {
        method:"POST", headers:{"X-CSRFToken": getCSRF()}, body: fd
      });
      const j = await r.json();
      hideOverlay();
      if (!j.ok){ notice(msg2, j.error || "فشل التجهيز.", true); return; }

      await loadPage(1);
      hide(s2); show(s3);
    }catch(e){
      hideOverlay();
      notice(msg2,"فشل الاتصال.", true);
    }
  });

  btnBack1.addEventListener("click", ()=>{ hide(s2); show(s1); });
  btnBack2.addEventListener("click", ()=>{ hide(s3); show(s2); });

  // ---------- Step 3 ----------
  let currentPage = 1, totalPages = 1;

  function mergeErrors(serverErrsObj, clientErrsObj){
    const out = {};
    if (serverErrsObj) Object.entries(serverErrsObj).forEach(([k,v])=> out[k]=v);
    if (clientErrsObj) Object.entries(clientErrsObj).forEach(([k,v])=> out[k]=v);
    return out;
  }

  function validateClientRow(rr){
    const local = {};
    const d = rr.data || {};
    const u1 = (d.unit_primary || "").trim();
    const u2 = (d.unit_secondary || "").trim();

    if (u1 && !UNIT_WHITELIST.has(u1)) local.unit_primary = "الوحدة الأولى غير مدعومة (غرام/قطعة/ليتر/طرد/حزمة).";
    if (u2 && !UNIT_WHITELIST.has(u2)) local.unit_secondary = "الوحدة الثانية غير مدعومة (غرام/قطعة/ليتر/طرد/حزمة).";

    if (u2 && !String(d.conversion_factor||"").trim()){
      local.conversion_factor = "عامل التحويل مطلوب عند وجود وحدة ثانية.";
    }
    if (u2 && u1 === u2){
      local.unit_secondary = "لا يجوز أن تكون الوحدة الثانية مطابقة للأولى.";
    }

    if (Object.keys(local).length) clientErrs.set(rr.rid, local);
    else clientErrs.delete(rr.rid);
  }

  function rowHasAnyError(rr){
    const s = rr.errors || {};
    const c = clientErrs.get(rr.rid) || {};
    return (Object.keys(s).length + Object.keys(c).length) > 0;
  }

  function joinList(v){
    if (!v) return "";
    if (Array.isArray(v)) return v.join(", ");
    return String(v);
  }
  function splitList(s){
    if (!s) return [];
    // split by comma OR spaces; keep tokens exact (no numeric coercion)
    const raw = String(s).replace(/\n/g," ").split(",").flatMap(x=>x.split(" "));
    const out = [];
    const seen = new Set();
    for (const t of raw.map(x=>x.trim())){
      if (!t) continue;
      if (!seen.has(t)){ seen.add(t); out.push(t); }
    }
    return out;
  }

  function renderRows(rows){
    // ensure client validations run
    rows.forEach(rr=> validateClientRow(rr));

    // error-first sort
    const sorted = [...rows].sort((a,b)=>{
      const ae = rowHasAnyError(a) ? 1 : 0;
      const be = rowHasAnyError(b) ? 1 : 0;
      if (ae !== be) return be - ae;
      return a.rid - b.rid;
    });

    rowsBody.innerHTML = "";
    for (const rr of sorted){
      const d = rr.data || {};
      const sErrs = rr.errors || {};
      const cErrs = clientErrs.get(rr.rid) || {};
      const merged = mergeErrors(sErrs, cErrs);
      const mergedMsg = Object.values(merged).join(" | ");

      const tr = document.createElement("tr");
      tr.dataset.rid = rr.rid;
      tr.innerHTML = `
        <td>${rr.rid}</td>
        <td contenteditable="true" data-f="name">${d.name||""}</td>
        <td contenteditable="true" data-f="set">${d.set||""}</td>
        <td contenteditable="true" data-f="unit_primary">${d.unit_primary||""}</td>
        <td contenteditable="true" data-f="unit_secondary">${d.unit_secondary||""}</td>
        <td contenteditable="true" data-f="conversion_factor">${d.conversion_factor||""}</td>
        <td contenteditable="true" data-f="cost">${d.cost||""}</td>
        <td contenteditable="true" data-f="price">${d.price||""}</td>
        <td contenteditable="true" data-f="barcodes_u1">${joinList(d.barcodes_u1)}</td>
        <td contenteditable="true" data-f="barcodes_u2">${joinList(d.barcodes_u2)}</td>
        <td contenteditable="true" data-f="unit_ids_u1">${joinList(d.unit_ids_u1)}</td>
        <td contenteditable="true" data-f="unit_ids_u2">${joinList(d.unit_ids_u2)}</td>
        <td contenteditable="true" data-f="notes">${d.notes||""}</td>
        <td>${mergedMsg ? `<span class="badge err">${mergedMsg}</span>` : "—"}</td>
      `;
      tr.querySelectorAll("[contenteditable]").forEach(cell=>{
        cell.addEventListener("blur", ()=>{
          let val = (cell.textContent||"").trim();
          const f = cell.dataset.f;
          // For list fields, send raw string; backend will store as raw string in JSON,
          // but our update endpoint re-saves, then the heavy rule re-run will use arrays
          // We can help by normalizing lists here to CSV again (no numeric coercion)
          if (["barcodes_u1","barcodes_u2","unit_ids_u1","unit_ids_u2"].includes(f)){
            val = splitList(val).join(", ");
          }
          saveCell(rr.rid, f, val, tr);
        });
        cell.addEventListener("keydown", e=>{
          if (e.key==="Enter"){ e.preventDefault(); cell.blur(); }
        });
      });
      rowsBody.appendChild(tr);
    }

    const pageErrs = Array.from(rowsBody.querySelectorAll("tr")).reduce((n,tr)=>{
      const rid = Number(tr.dataset.rid);
      return n + (clientErrs.get(rid) ? 1 : 0);
    },0);
    statsLine.dataset.pageErrors = String(pageErrs);
  }

  async function loadPage(p){
    const u = new URL("/manager/products/import/stage/", window.location.origin);
    u.searchParams.set("staging_id", stagingId);
    u.searchParams.set("page", String(p));
    u.searchParams.set("size", "200");
    const j = await fetch(u).then(r=>r.json());
    if (!j.ok){ notice(msg3, j.error || "فشل التحميل.", true); return; }
    currentPage = j.page; totalPages = j.pages;

    renderRows(j.rows || []);
    const pageErrs = Number(statsLine.dataset.pageErrors||"0");
    statsLine.textContent = `الصفحة ${j.page}/${j.pages} — عدد الصفوف: ${j.total} — إجمالي أخطاء (السيرفر): ${j.errors_total} — أخطاء الصفحة (عميل): ${pageErrs}`;
    btnCommit.disabled = j.errors_total > 0;
  }

  async function saveCell(rid, field, value, trEl){
    const fd = new FormData();
    fd.append("staging_id", stagingId);
    fd.append("rid", String(rid));
    fd.append("field", field);
    fd.append("value", value);
    try{
      const j = await fetch("/manager/products/import/update-row/", {
        method:"POST", headers:{"X-CSRFToken": getCSRF()}, body: fd
      }).then(r=>r.json());
      if (!j.ok){ notice(msg3, j.error || "تعذر الحفظ.", true); return; }

      // Use returned row, re-validate, and re-render table with error-first sort
      const rows = [];
      rowsBody.querySelectorAll("tr").forEach(tr=>{
        const id = Number(tr.dataset.rid);
        if (id === j.row.rid){
          rows.push(j.row);
        }else{
          const tds = tr.querySelectorAll("td");
          rows.push({
            rid: id,
            data: {
              name: tds[1]?.textContent || "",
              set: tds[2]?.textContent || "",
              unit_primary: tds[3]?.textContent || "",
              unit_secondary: tds[4]?.textContent || "",
              conversion_factor: tds[5]?.textContent || "",
              cost: tds[6]?.textContent || "",
              price: tds[7]?.textContent || "",
              barcodes_u1: splitList(tds[8]?.textContent || ""),
              barcodes_u2: splitList(tds[9]?.textContent || ""),
              unit_ids_u1: splitList(tds[10]?.textContent || ""),
              unit_ids_u2: splitList(tds[11]?.textContent || ""),
              notes: tds[12]?.textContent || "",
            },
            errors: {} // server errs unknown for others, but client checks still apply
          });
        }
      });
      renderRows(rows);

      // lock commit if server still reports errors
      btnCommit.disabled = (j.errors_total > 0);
    }catch(e){
      notice(msg3,"تعذر الاتصال.", true);
    }
  }

  // ---------- Commit ----------
  btnCommit.addEventListener("click", async ()=>{
    const fd = new FormData();
    fd.append("staging_id", stagingId);
    try{
      const j = await fetch("/manager/products/import/commit/", {
        method:"POST", headers:{"X-CSRFToken": getCSRF()}, body: fd
      }).then(r=>r.json());
      if (!j.ok){ notice(msg3, j.error || "فشل الإدخال.", true); return; }
      notice(msg3, `تم: إنشاء ${j.created} / تحديث ${j.updated} / تجاوز ${j.skipped}`);
      btnCommit.disabled = true;
    }catch(e){
      notice(msg3,"تعذر الاتصال.", true);
    }
  });

})();
