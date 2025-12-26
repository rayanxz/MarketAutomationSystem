// static/js/billing_creditors.js
(function () {
  "use strict";

  const API = {
       LIST: "/manager/debts/api/creditors/",
   COLLECT_FULL:  (item) => item.manual
     ? `/manager/debts/manual-creditors/${item.id}/collect-full/`
     : `/manager/billing/returns/${item.id}/collect-full/`,
   COLLECT_BATCH: (item) => item.manual
     ? `/manager/debts/manual-creditors/${item.id}/collect-batch/`
     : `/manager/billing/returns/${item.id}/collect-batch/`,
  };

  const rows     = el("#rows");
  const loadMore = el("#loadMore");
  const endMsg   = el("#endMsg");

  const fQ      = el("#fQ");
  const fSerial = el("#fSerial");
  const fFrom   = el("#fFrom");
  const fTo     = el("#fTo");
  const fStatus = el("#fStatus");
  const btnSearch = el("#btnSearch");

  const mFull        = el("#modal-full");
  const mFullText    = el("#full-text");
  const mFullConfirm = qs('[data-confirm]', mFull);
  const mFullClose   = qs('[data-close]',   mFull);

  const mBatch        = el("#modal-batch");
  const mBatchText    = el("#batch-text");
  const mBatchHint    = el("#batch-hint");
  const mBatchAmount  = el("#batch-amount");
  const mBatchConfirm = qs('[data-confirm]', mBatch);
  const mBatchClose   = qs('[data-close]',   mBatch);

  const VIEW_URL = (entryId) => `/manager/debts/view/creditor/${entryId}/`;


  let cursor = null, busy = false, done = false;
  let target = { id: null, provider: "", remaining: 0 };

  function el(s){ return document.querySelector(s); }
  function qs(s, root){ return (root || document).querySelector(s); }
  function nf(x){ const n = Number(x); return Number.isFinite(n) ? new Intl.NumberFormat().format(n) : x; }
  function escapeHtml(s){ return String(s||"").replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
  function getCsrf(){ const m = document.cookie.match(/(?:^|;)\s*csrftoken=([^;]+)/); return m ? decodeURIComponent(m[1]) : ""; }

  function qsBuild(obj){
    const u = new URLSearchParams();
    Object.entries(obj).forEach(([k,v]) => { if (v !== "" && v != null) u.append(k, v); });
    return u.toString();
  }

  function params(reset=false){
    const p = {
      page_size: 30,
      q:        (fQ.value||"").trim(),
      serial:   (fSerial.value||"").trim(),
      date_from: fFrom.value || "",
      date_to:   fTo.value || "",
      status: (fStatus.value || ""),
    };
    if (!reset && cursor) p.cursor = cursor;
    return p;
  }

  function statusClass(st){
    if (st === "unpaid")  return "is-unpaid";
    if (st === "partial") return "is-partial";
    return "is-paid";
  }

 function arType(t){
  if ((t||"").toLowerCase() === "customer") return "زبون";
  if ((t||"").toLowerCase() === "worker")   return "عامل";
  return "مورد";
}

function rowHtml(b){
  const partyType = (b.party_type || "provider");
  const partyName = b.party_name || (b?.provider?.name || "");
  const serial    = (b.serial ?? b.doc_serial ?? "");
  const totalNum  = Number(b.total ?? 0);
  const paidNum   = Number(b.paid_amount ?? 0);
  const remainingNum = (b.remaining != null) ? Number(b.remaining) : Math.max(0, totalNum - paidNum);
  const canAct    = (b.status !== "paid" && remainingNum > 0);

  const actions = canAct
    ? `<button class="btn js-collect-full">تحصيل كامل</button>
       <button class="btn js-collect-batch">تحصيل دفعة</button>`
    : `<button class="btn" disabled>لا يوجد إجراء</button>`;

  return `
    <tr class="${statusClass(b.status)}"
      data-id="${b.id}"
      data-provider="${escapeHtml(partyName)}"
      data-remaining="${String(remainingNum)}"
      data-manual="${b.manual ? "1" : "0"}">

      <td>${serial}</td>
      <td>${escapeHtml(arType(partyType))}</td>
      <td>${escapeHtml(partyName)}</td>
      <td>${nf(totalNum)}</td>
      <td>${nf(paidNum)}</td>
      <td>${nf(remainingNum)}</td>
      <td>${b.status === "unpaid" ? "غير مدفوعة" : (b.status === "partial" ? "مدفوعة جزئياً" : "مدفوعة")}</td>
      <td class="left">${actions} <a class="btn" href="/manager/debts/view/creditor/${b.entry_id ?? ""}/">عرض</a></td>
    </tr>
  `;
}


  async function load(reset=false){
    if (busy || (done && !reset)) return;
    busy = true; loadMore.disabled = true; endMsg.hidden = true;
    if (reset){ rows.innerHTML = ""; cursor = null; done = false; }

    try{
      const url  = `${API.LIST}?${qsBuild(params(reset))}`;
      const res  = await fetch(url, { headers: { "Accept": "application/json" } });
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || "error");

      const frag = document.createDocumentFragment();
      for (const item of (data.items || [])){
        const tmp = document.createElement("tbody");
        tmp.innerHTML = rowHtml(item);
        frag.appendChild(tmp.firstElementChild);
      }
      rows.appendChild(frag);

      cursor = data.next_cursor || null;
      done   = !cursor;
      loadMore.style.display = done ? "none" : "inline-block";
      if (done && rows.children.length) endMsg.hidden = false;
    }catch(e){
      console.error(e);
      alert("فشل التحميل");
    }finally{
      busy = false; loadMore.disabled = false;
    }
  }

  btnSearch.addEventListener("click", () => load(true));
  [fQ, fSerial, fFrom, fTo, fStatus].forEach(i => i?.addEventListener("change", () => load(true)));
  loadMore.addEventListener("click", () => load(false));

  rows.addEventListener("click", (e) => {
  const tr = e.target.closest("tr[data-id]");
  if (!tr) return;

  const ds = tr.dataset;
  target.id        = ds.id || "";
  target.provider  = ds.provider || "";
  target.remaining = Number(ds.remaining || "0");
  target.manual    = (ds.manual === "1" || ds.manual === "true");

  if (e.target.classList.contains("js-collect-full")){
    mFullText.textContent = `هل أنت متأكد من تحصيل ${nf(target.remaining)} بالكامل من (${target.provider})؟`;
    openModal(mFull); return;
  }
  if (e.target.classList.contains("js-collect-batch")){
    mBatchText.textContent = `أدخل التحصيل من المورد (${target.provider})`;
    mBatchHint.textContent = `المتبقي: ${nf(target.remaining)}`;
    mBatchAmount.value = "";
    openModal(mBatch); return;
  }
});


  function openModal(m){ m.classList.add("open"); m.setAttribute("aria-hidden", "false"); }
  function closeModal(m){ m.classList.remove("open"); m.setAttribute("aria-hidden", "true"); }

  mFullClose.addEventListener("click", () => closeModal(mFull));
  mFull.addEventListener("click",  (e) => { if (e.target === mFull) closeModal(mFull); });

  mBatchClose.addEventListener("click", () => closeModal(mBatch));
  mBatch.addEventListener("click", (e) => { if (e.target === mBatch) closeModal(mBatch); });

  mFullConfirm.addEventListener("click", async () => {
    if (!target.id) return;
    try{
      const resp = await fetch(API.COLLECT_FULL(target), { method: "POST", headers: { "X-CSRFToken": getCsrf(), "Accept":"application/json" } });
      const data = await resp.json();
      if (data.ok){ closeModal(mFull); load(true); } else { alert(data.error || "خطأ غير متوقع"); }
    }catch{ alert("فشل الاتصال بالخادم"); }
  });

  mBatchConfirm.addEventListener("click", async () => {
    const v = parseFloat(mBatchAmount.value || "0");
    if (!(v > 0)){ alert("أدخل قيمة موجبة."); return; }
    if (v > target.remaining){ alert("القيمة تتجاوز المبلغ المتبقي."); return; }

    const form = new FormData();
    form.append("amount", String(v));
    try{
      const resp = await fetch(API.COLLECT_BATCH(target), { method: "POST", body: form, headers: { "X-CSRFToken": getCsrf(), "Accept":"application/json" } });
      const data = await resp.json();
      if (data.ok){ closeModal(mBatch); load(true); } else { alert(data.error || "خطأ غير متوقع"); }
    }catch{ alert("فشل الاتصال بالخادم"); }
  });

  (function prefillFromUrl(){
    const p = new URLSearchParams(location.search);
    if (p.has("serial") && fSerial) fSerial.value = p.get("serial");
    if (p.has("q") && fQ) fQ.value = p.get("q");
  })();

  load(true);
})();
