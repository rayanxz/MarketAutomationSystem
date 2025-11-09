// static/js/manager_export.js
(function () {
  "use strict";

  // ---------- small helpers ----------
  const q  = (s,r=document)=>r.querySelector(s);
  const qa = (s,r=document)=>Array.from(r.querySelectorAll(s));
  const hide = el => el && el.classList.add("hidden");
  const show = el => el && el.classList.remove("hidden");
  const debounce = (fn,ms=200)=>{ let t; return (...a)=>{ clearTimeout(t); t=setTimeout(()=>fn(...a),ms); }; };
  const getCookie = n => (document.cookie.split("; ").find(r=>r.startsWith(n+"="))||"").split("=")[1]||"";
  const CSRF = decodeURIComponent(getCookie("csrftoken")||"");

  // ---------- server data ----------
  const ALL_COLUMNS_RAW = JSON.parse(document.getElementById("exp_cols").textContent);
  const LABELS          = JSON.parse(document.getElementById("exp_labels").textContent);
  const labelOf = k => LABELS[k] || k;

  // Exclude fields you don't want selectable
  const EXCLUDED = new Set(["product_number","stock_qty"]);   // ID + quantity
  const ALL_ALLOWED = ALL_COLUMNS_RAW.filter(k => !EXCLUDED.has(k));

  // ---------- steps ----------
  const step1=q("#step1"), step2=q("#step2"), step3=q("#step3");
  const s1=q("#s1"), s2=q("#s2"), s3=q("#s3");
  const toStep2=q("#toStep2"), toStep3=q("#toStep3");
  const backTo1=q("#backTo1"), backTo2=q("#backTo2");
  function setStep(n){
    [step1,step2,step3].forEach((el,i)=>el.classList.toggle("hidden",(i+1)!==n));
    [s1,s2,s3].forEach((el,i)=>el.classList.toggle("active",(i+1)===n));
    if(n===3) prepare(true);
  }

  // ---------- stage 1 (scope pickers) ----------
  const scopeRadios=qa('input[name="scope"]');
  const pickCollections=q("#pickCollections");
  const pickSets=q("#pickSets");
  const scopeSummary=q("#scopeSummary");
  const validationMsg1=q("#validationMsg1");
  const fileType=q("#fileType");
  const includeHeader=q("#includeHeader");

  // tokens: collections
  const colSearchBox=q("#colSearch1"), colSugs=q("#colSugs"), colTokens=q("#colTokens"), colCount=q("#colCount");
  const btnClrCols=q("#btnClrCols");

  // tokens: sets
  const setFilterCol=q("#setFilterCol"), setSearch=q("#setSearch"), setSugs=q("#setSugs"), setTokens=q("#setTokens"), setCount=q("#setCount");
  const btnClrSets=q("#btnClrSets");

  const selectedColsScope = new Map();
  const selectedSetsScope = new Map();

  function scopeUI(){
    const scope = qa('input[name="scope"]:checked')[0].value;
    if(scope==='all'){ hide(pickCollections); hide(pickSets); scopeSummary.textContent='سيتم تصدير جميع المنتجات النشطة.'; }
    else if(scope==='collections'){ show(pickCollections); hide(pickSets); scopeSummary.textContent='اختر زمرة/زُمر للتصدير.'; }
    else { hide(pickCollections); show(pickSets); scopeSummary.textContent='اختر مجموعة/مجموعات أب للتصدير.'; }
    validationMsg1.textContent='';
  }
  scopeRadios.forEach(r=>r.addEventListener("change", scopeUI));
  scopeUI();

  function renderTokens(map, mount, countEl){
    mount.innerHTML='';
    map.forEach(v=>{
      const el=document.createElement('span');
      el.className='token';
      el.innerHTML = `${v.code ? v.code+' — ' : ''}${v.name} <button aria-label="إزالة">×</button>`;
      el.querySelector('button').addEventListener('click', ()=>{ map.delete(v.id); renderTokens(map,mount,countEl); });
      mount.appendChild(el);
    });
    countEl.textContent = map.size ? `${map.size} محدَّد` : 'لا شيء محدَّد';
  }
  renderTokens(selectedColsScope,colTokens,colCount);
  renderTokens(selectedSetsScope,setTokens,setCount);

  async function fetchCollections(qs){
    if(!qs) return [];
    const r = await fetch(`/manager/products/api/ac/collections/?q=${encodeURIComponent(qs)}`, {headers:{'X-Requested-With':'XMLHttpRequest'}});
    const j = await r.json(); return j.items||[];
  }
  const updateColSugs = debounce(async ()=>{
    const term=(colSearchBox?.value||'').trim();
    if(!term){ colSugs.innerHTML=''; hide(colSugs); return; }
    const items = await fetchCollections(term);
    if(!items.length){ colSugs.innerHTML=''; hide(colSugs); return; }
    colSugs.innerHTML = items.map(i=>`<button type="button" data-id="${i.id}" data-code="${i.code}" data-name="${i.name}">${i.code} — ${i.name}</button>`).join('');
    show(colSugs);
  },220);
  colSearchBox && colSearchBox.addEventListener('input', updateColSugs);
  colSugs && colSugs.addEventListener('click', e=>{
    const b=e.target.closest('button'); if(!b) return;
    const id=+b.dataset.id;
    if(!selectedColsScope.has(id)){
      selectedColsScope.set(id,{id,code:b.dataset.code,name:b.dataset.name});
      renderTokens(selectedColsScope,colTokens,colCount);
    }
    colSearchBox.value=''; colSugs.innerHTML=''; hide(colSugs);
  });
  btnClrCols && btnClrCols.addEventListener('click', ()=>{ selectedColsScope.clear(); renderTokens(selectedColsScope,colTokens,colCount); });

  async function fetchSets(qs,cid){
    const url=new URL('/manager/products/api/ac/sets/', location.origin);
    if(qs) url.searchParams.set('q', qs);
    if(cid) url.searchParams.set('cid', cid);
    const r = await fetch(url, {headers:{'X-Requested-With':'XMLHttpRequest'}});
    const j = await r.json(); return j.items||[];
  }
  const updateSetSugs = debounce(async ()=>{
    const term=(setSearch?.value||'').trim();
    const cid=setFilterCol?.value || '';
    if(!term && !cid){ setSugs.innerHTML=''; hide(setSugs); return; }
    const items = await fetchSets(term,cid);
    if(!items.length){ setSugs.innerHTML=''; hide(setSugs); return; }
    setSugs.innerHTML = items.map(i=>`
      <button type="button" data-id="${i.id}" data-code="${i.code||''}" data-name="${i.name}">
        <span style="min-width:72px">${i.code||''}</span><span>${i.name}</span>
      </button>`).join('');
    show(setSugs);
  },220);
  setSearch && setSearch.addEventListener('input', updateSetSugs);
  setFilterCol && setFilterCol.addEventListener('change', updateSetSugs);
  setSugs && setSugs.addEventListener('click', e=>{
    const b=e.target.closest('button'); if(!b) return;
    const id=+b.dataset.id;
    if(!selectedSetsScope.has(id)){
      selectedSetsScope.set(id,{id,code:b.dataset.code,name:b.dataset.name});
      renderTokens(selectedSetsScope,setTokens,setCount);
    }
    setSearch.value=''; setSugs.innerHTML=''; hide(setSugs);
  });
  btnClrSets && btnClrSets.addEventListener('click', ()=>{ selectedSetsScope.clear(); renderTokens(selectedSetsScope,setTokens,setCount); });

  function validateStep1(){
    const scope = qa('input[name="scope"]:checked')[0].value;
    if(scope==='collections' && selectedColsScope.size===0){ validationMsg1.textContent='اختر زمرة واحدة على الأقل.'; return false; }
    if(scope==='sets' && selectedSetsScope.size===0){ validationMsg1.textContent='اختر مجموعة أب واحدة على الأقل.'; return false; }
    validationMsg1.textContent=''; return true;
  }

  // ---------- stage 2 (SLOTS — one horizontal row) ----------
  const slotRow     = q("#slotRow");
  const colsSummary = q("#colsSummary");
  const validationMsg2 = q("#validationMsg2");
  const btnAddSlot  = q("#addSlot");

  const MAX_SLOTS = 12, MIN_SLOTS = 1;

  // init 12 slots, fill with first allowed keys; rest as __ignore__
  let slots = Array.from({length:MAX_SLOTS}, (_,i)=> ALL_ALLOWED[i] || "__ignore__");

  function usedKeys(exceptIdx=-1){
    const s = new Set();
    slots.forEach((v,i)=>{ if(i!==exceptIdx && v!=="__ignore__") s.add(v); });
    return s;
  }

  function slotTemplate(idx, value){
    const used = usedKeys(idx); // keys used by other slots
    const disabledAttr = k => used.has(k) ? ' disabled' : '';
    return `
      <div class="slot" data-idx="${idx}">
        <div class="slot-head">
          <span class="slot-title">العمود ${idx+1}</span>
          <div class="slot-actions">
            <button type="button" class="btn ghost mini" data-left ${idx===0?'disabled':''}>←</button>
            <button type="button" class="btn ghost mini" data-right ${idx===slots.length-1?'disabled':''}>→</button>
            <button type="button" class="btn ghost mini" data-del ${slots.length<=MIN_SLOTS?'disabled':''}>حذف</button>
          </div>
        </div>
        <select class="select slot-select">
          <option value="__ignore__"${value==="__ignore__"?' selected':''}>تجاهل</option>
          ${ALL_ALLOWED.map(k=>`<option value="${k}"${k===value?' selected':''}${disabledAttr(k)}>${labelOf(k)}</option>`).join('')}
        </select>
      </div>
    `;
  }

  function renderSlots(){
    // enforce bounds
    if(slots.length < MIN_SLOTS) slots = ["__ignore__"];
    if(slots.length > MAX_SLOTS) slots = slots.slice(0, MAX_SLOTS);

    // if any excluded sneaked in, blank it
    slots = slots.map(v => EXCLUDED.has(v) ? "__ignore__" : v);

    slotRow.innerHTML = slots.map((v,i)=>slotTemplate(i,v)).join("");
    const exportingCount = slots.filter(x=>x!=="__ignore__").length;
    colsSummary.textContent = `عدد الأعمدة: ${slots.length} — قيد التصدير: ${exportingCount}`;
    // disable "add" at cap
    if (btnAddSlot) btnAddSlot.disabled = (slots.length >= MAX_SLOTS);
  }
  renderSlots();

  // selection change with uniqueness enforcement
  slotRow.addEventListener("change", (e)=>{
    const sel = e.target.closest(".slot-select");
    if(!sel) return;
    const holder = e.target.closest(".slot");
    const idx = +holder.dataset.idx;
    const nextVal = sel.value;

    // if conflict with other slots, keep selection but do nothing (option is disabled anyway)
    slots[idx] = nextVal;
    renderSlots(); // re-render to refresh disabled states across all selects
  });

  // slot buttons
  slotRow.addEventListener("click", (e)=>{
    const box = e.target.closest(".slot"); if(!box) return;
    const idx = +box.dataset.idx;

    if(e.target.closest("[data-left]") && idx>0){
      [slots[idx-1], slots[idx]] = [slots[idx], slots[idx-1]];
      renderSlots();
    } else if(e.target.closest("[data-right]") && idx<slots.length-1){
      [slots[idx+1], slots[idx]] = [slots[idx], slots[idx+1]];
      renderSlots();
    } else if(e.target.closest("[data-del]")){
      if(slots.length > MIN_SLOTS){
        slots.splice(idx,1);
        renderSlots();
      }
    }
  });

  // add slot (up to 12)
  btnAddSlot && btnAddSlot.addEventListener("click", ()=>{
    if(slots.length >= MAX_SLOTS) return;
    // pick first unused allowed key, else ignore
    const used = usedKeys();
    const candidate = ALL_ALLOWED.find(k => !used.has(k)) || "__ignore__";
    slots.push(candidate);
    renderSlots();
  });

  // presets (respect uniqueness and bounds)
  q("#presetAll").addEventListener("click", ()=>{
    slots = ALL_ALLOWED.slice(0, MAX_SLOTS);
    if(slots.length < MAX_SLOTS) slots = slots.concat(Array(MAX_SLOTS - slots.length).fill("__ignore__"));
    renderSlots();
  });
  q("#presetNamePrice").addEventListener("click", ()=>{
    slots = ["name","price"];
    renderSlots();
  });
  q("#presetNameCostPrice").addEventListener("click", ()=>{
    slots = ["name","cost","price"];
    renderSlots();
  });
  q("#presetReset").addEventListener("click", ()=>{
    slots = ALL_ALLOWED.slice(0, MAX_SLOTS);
    if(slots.length < MAX_SLOTS) slots = slots.concat(Array(MAX_SLOTS - slots.length).fill("__ignore__"));
    renderSlots();
  });

  function validateStep2(){
    const chosen = slots.filter(x=>x!=="__ignore__");
    if(!chosen.length){ validationMsg2.textContent='اختر عموداً واحداً على الأقل (غير مُتجاهل).'; return false; }
    if(slots.length < MIN_SLOTS || slots.length > MAX_SLOTS){
      validationMsg2.textContent='عدد الأعمدة يجب أن يكون بين 1 و 12.';
      return false;
    }
    validationMsg2.textContent=''; return true;
  }

  // ---------- stage 3 (preview/download) ----------
  const btnPrepare=q("#btnPrepare");
  const btnDownload=q("#btnDownload");
  const tbl=q("#previewTbl");
  const errBox=q("#err");
  const previewMeta=q("#previewMeta");
  const previewBox=q("#previewBox");

  function setErr(msg){ if(!msg){ errBox.classList.add('hidden'); errBox.textContent=''; } else { errBox.classList.remove('hidden'); errBox.textContent=msg; } }
  function startLoading(){
    previewBox.classList.add('loading');
    tbl.innerHTML = '<thead><tr><th class="mini muted">... جار التحضير ...</th></tr></thead>';
    btnDownload.classList.add('hidden'); setErr(''); previewMeta.textContent='—';
  }
  function endLoading(){ previewBox.classList.remove('loading'); }

  function collectFormData(){
    const scope = qa('input[name="scope"]:checked')[0].value;
    const fd = new FormData();
    fd.append('scope', scope);
    fd.append('file_type', fileType.value);
    fd.append('include_header', includeHeader.checked ? '1' : '0');

    if (scope==='collections'){
      if (selectedColsScope.size===0) return {error:'اختر زمرة واحدة على الأقل.'};
      [...selectedColsScope.values()].forEach(o=> fd.append('ids[]', String(o.id)));
    } else if (scope==='sets'){
      if (selectedSetsScope.size===0) return {error:'اختر مجموعة أب واحدة على الأقل.'};
      [...selectedSetsScope.values()].forEach(o=> fd.append('ids[]', String(o.id)));
    }

    // only non-ignored in the exact order
    const chosen = slots.filter(x=>x!=="__ignore__");
    if(!chosen.length) return {error:'اختر عموداً واحداً على الأقل.'};
    chosen.forEach(k=> fd.append('columns[]', k));

    return {fd};
  }

  async function prepare(auto=false){
    const pack = collectFormData();
    if (pack.error){ if(!auto) setErr(pack.error); return; }
    startLoading();
    try{
      const r = await fetch('/manager/products/export/prepare/', {
        method:'POST', body: pack.fd,
        headers:{ 'X-Requested-With':'XMLHttpRequest', 'X-CSRFToken': CSRF }
      });
      const ct = r.headers.get('Content-Type') || '';
      if (!ct.includes('application/json')){
        const text = await r.text();
        throw new Error(text.includes('csrf') ? 'فشل CSRF. حدّث الصفحة.' : 'استجابة غير متوقعة.');
      }
      const data = await r.json();
      if (!r.ok || !data.ok) throw new Error(data.error || 'فشل التحضير');

      const headers=data.headers||[];
      const sample=data.sample||[];
      tbl.innerHTML='';
      if(!headers.length){
        tbl.innerHTML='<thead><tr><th>لا توجد بيانات</th></tr></thead>';
      }else{
        const thead=document.createElement('thead');
        const trh=document.createElement('tr');
        headers.forEach(h=>{ const th=document.createElement('th'); th.textContent=h; trh.appendChild(th); });
        thead.appendChild(trh);
        const tbody=document.createElement('tbody');
        if(!sample.length){
          const tr=document.createElement('tr');
          const td=document.createElement('td'); td.colSpan=headers.length; td.textContent='لا توجد صفوف للمعاينة (تُعرض أول 30).';
          tr.appendChild(td); tbody.appendChild(tr);
        }else{
          sample.forEach(row=>{
            const tr=document.createElement('tr');
            row.forEach(cell=>{ const td=document.createElement('td'); td.textContent=(cell==null?'':cell); tr.appendChild(td); });
            tbody.appendChild(tr);
          });
        }
        tbl.append(thead,tbody);
      }
      previewMeta.textContent=`عدد الصفوف الكلي: ${data.rows || 0}`;
      btnDownload.href=data.download_url;
      btnDownload.classList.remove('hidden');
      setErr('');
    }catch(e){
      setErr(String(e.message||e));
    }finally{
      endLoading();
    }
  }

  // ---------- nav ----------
  toStep2.addEventListener("click", ()=>{ if (validateStep1()) setStep(2); });
  toStep3.addEventListener("click", ()=>{ if (validateStep2()) setStep(3); });
  backTo1.addEventListener("click", ()=> setStep(1));
  backTo2.addEventListener("click", ()=> setStep(2));
  btnPrepare && btnPrepare.addEventListener("click", ()=> prepare(false));
})();
