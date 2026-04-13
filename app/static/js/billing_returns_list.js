// static/js/billing_returns_list.js
(() => {
  const API_LIST = window.__RETURNS__?.listUrl;
  const VIEW_TPL = window.__RETURNS__?.viewUrlTemplate;
  if (!API_LIST || !VIEW_TPL) { console.error("Missing __RETURNS__ URLs"); return; }

  const form = document.getElementById('filters');
  const rowsEl = document.getElementById('rows');
  const loadMoreBtn = document.getElementById('loadMore');
  const endMsg = document.getElementById('endMsg');
  const sentinelEl = document.getElementById('sentinel');

  let cursor = null, loading = false, done = false;
  let debounceTimer = null;

  // helpers
  const qs = (obj)=> new URLSearchParams(obj).toString();
  const nfmt = (x)=> {
    const n = Number(x);
    return Number.isFinite(n) ? new Intl.NumberFormat().format(n) : (x ?? "");
  };
  const pill = (status) => {
    const cls = status === "paid" ? "paid" : (status === "partial" ? "partial" : "unpaid");
    const label = status === "paid" ? "مدفوعة من المورد"
                 : status === "partial" ? "مدفوعة جزئياً من المورد"
                 : "غير مدفوعة من المورد";
    return `<span class="status ${cls}">${label}</span>`;
  };
  const row = (r) => {
  const dt = r.created_at ? new Date(r.created_at).toLocaleString() : "";
  const retId = String(r.id || "").trim();
  const viewUrl = VIEW_TPL.replace("PR-REF", encodeURIComponent(retId));
  return `
      <tr data-id="${r.id}">
        <td>${retId}</td>
        <td>${r.source_bill_id ?? ""}</td>
        <td>${r.provider?.name ?? ""}</td>
        <td>${nfmt(r.total_syp)}</td>
        <td>${nfmt(r.total_usd)}</td>
        <td>${nfmt(r.total)}</td>
        <td>${pill(r.status)}</td>
        <td>${dt}</td>
        <td class="left">
          <a class="btn" href="${viewUrl}">View</a>
          <button class="btn" disabled title="Not supported">Delete</button>
        </td>
      </tr>
    `;
};


  function readFilters(includeCursor=true){
    const fd = new FormData(form);
    const obj = {};
    for (const [k,v] of fd.entries()) if (v) obj[k]=v;
    obj.page_size = 30;
    if (includeCursor && cursor) obj.cursor = cursor;
    return obj;
  }

  function updateUrlFromFilters(){
    const params = readFilters(false);
    const url = new URL(location.href);
    url.search = new URLSearchParams(params).toString();
    history.replaceState(null, "", url.toString());
  }

  function prefillFromUrl(){
    const params = new URLSearchParams(location.search);
    let changed = false;
    for (const [k,v] of params.entries()){
      if (form.elements[k]) { form.elements[k].value = v; changed = true; }
    }
    return changed;
  }

  const debounce = (fn, ms)=> (...args)=>{ clearTimeout(debounceTimer); debounceTimer=setTimeout(()=>fn(...args), ms); };

  async function load(reset=false){
    if (loading || (done && !reset)) return;
    loading = true;
    loadMoreBtn.disabled = true;
    endMsg.hidden = true;

    if (reset){
      rowsEl.innerHTML = ""; cursor = null; done = false;
    }

    const params = readFilters(!reset);

    try{
      const res = await fetch(`${API_LIST}?${qs(params)}`, { headers: { Accept: 'application/json' } });
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || "error");

      const frag = document.createDocumentFragment();
      for (const r of data.items){
        const tb = document.createElement('tbody');
        tb.innerHTML = row(r);
        frag.appendChild(tb.firstElementChild);
      }
      rowsEl.appendChild(frag);

      cursor = data.next_cursor || null;
      done = !cursor;
      loadMoreBtn.style.display = done ? 'none' : 'inline-block';
      if (done && rowsEl.children.length > 0) endMsg.hidden = false;
    } catch (e){
      console.error(e);
      alert('فشل التحميل');
    } finally {
      loading = false;
      loadMoreBtn.disabled = false;
    }
  }

  // events
  form.addEventListener('submit', (e)=>{ e.preventDefault(); updateUrlFromFilters(); load(true); });
  for (const el of form.querySelectorAll('input,select')){
    el.addEventListener('input', debounce(()=>{ updateUrlFromFilters(); load(true); }, 300));
    el.addEventListener('change', ()=>{ updateUrlFromFilters(); load(true); });
  }
  loadMoreBtn.addEventListener('click', ()=> load(false));

  if ('IntersectionObserver' in window && sentinelEl){
    const io = new IntersectionObserver(entries => {
      for (const en of entries) if (en.isIntersecting) load(false);
    });
    io.observe(sentinelEl);
  }

  prefillFromUrl();
  load(true);
})();
