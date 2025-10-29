// app/static/js/billing_debts.js
(function () {
  "use strict";

  // ========================= API endpoints =========================
  const API = {
    // role: 'debtor' => store owes providers (old bills debts)
    // role: 'creditor' => providers owe store (provider returns receivables)
    LIST: (role) =>
      role === "creditor"
        ? "/manager/billing/api/returns/list/"
        : "/manager/billing/api/debts/",

    // Debtor (store owes provider) = existing bill pay endpoints:
    PAY_FULL:  (id) => `/manager/billing/bills/${id}/pay-full/`,
    PAY_BATCH: (id) => `/manager/billing/bills/${id}/pay-batch/`,

    // Creditor (provider owes store) = new collect endpoints:
    COLLECT_FULL:  (id) => `/manager/billing/returns/${id}/collect-full/`,
    COLLECT_BATCH: (id) => `/manager/billing/returns/${id}/collect-batch/`,
  };

  // ========================= DOM refs =========================
  const rows      = el("#rows");
  const loadMore  = el("#loadMore");
  const endMsg    = el("#endMsg");

  const fQ        = el("#fQ");
  const fSerial   = el("#fSerial");
  const fId       = el("#fId");
  const fFrom     = el("#fFrom");
  const fTo       = el("#fTo");
  const fStatus   = el("#fStatus");
  const fRole     = el("#fRole");       // NEW (debtor|creditor)
  const btnSearch = el("#btnSearch");

  // Modals
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

  // ========================= State =========================
  let cursor = null, busy = false, done = false;
  let target = { id: null, provider: "", remaining: 0 };

  // ========================= Tiny utils =========================
  function el(s)         { return document.querySelector(s); }
  function qs(s, root)   { return (root || document).querySelector(s); }
  function nf(x)         { const n = Number(x); return Number.isFinite(n) ? new Intl.NumberFormat().format(n) : x; }
  function escapeHtml(s) { return String(s||"").replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
  function getCsrf()     { const m = document.cookie.match(/(?:^|;)\s*csrftoken=([^;]+)/); return m ? decodeURIComponent(m[1]) : ""; }

  function qsBuild(obj){
    const u = new URLSearchParams();
    Object.entries(obj).forEach(([k,v]) => { if (v !== "" && v != null) u.append(k, v); });
    return u.toString();
  }

  // ========================= Filters/params =========================
  function params(reset=false){
  const p = {
    page_size: 30,
    q:        (fQ?.value || "").trim(),
    serial:   (fSerial?.value || "").trim(),
    id:       (fId?.value || "").trim(),          // <<< fixed (was fId.value)
    date_from: fFrom?.value || "",
    date_to:   fTo?.value || "",
    status:   fStatus?.value || "",
  };
  if (!reset && cursor) p.cursor = cursor;
  return p;
}

  // ========================= Rendering =========================
  function statusClass(st){
    if (st === "unpaid")  return "is-unpaid";
    if (st === "partial") return "is-partial";
    return "is-paid";
  }

 function rowHtml(b){
  const role   = (fRole?.value || "debtor");
  const canAct = (String(b.status).toLowerCase() !== "paid" && Number(b.remaining ?? 0) > 0);

  const actions = role === "debtor"
    ? (canAct
        ? `<button class="btn js-full">تسديد كامل</button>
           <button class="btn js-batch">تسديد دفعة</button>`
        : `<button class="btn" disabled>لا يوجد إجراء</button>`)
    : (canAct
        ? `<button class="btn js-collect-full">تحصيل كامل</button>
           <button class="btn js-collect-batch">تحصيل دفعة</button>`
        : `<button class="btn" disabled>لا يوجد إجراء</button>`);

  return `
    <tr class="${statusClass(b.status)}"
        data-id="${b.id}"
        data-provider="${escapeHtml(b?.provider?.name || "")}"
        data-remaining="${b.remaining}">
      <td>${b.serial ?? ""}</td>
      <td>${escapeHtml(b?.provider?.name || "")}</td>
      <td>${nf(b.total)}</td>
      <td>${nf(b.paid_amount)}</td>
      <td>${nf(b.remaining)}</td>
      <td>${(String(b.status).toLowerCase()==="unpaid")?"غير مدفوعة":(String(b.status).toLowerCase()==="partial"?"مدفوعة جزئياً":"مدفوعة")}</td>
      <td class="left">${actions}</td>
    </tr>
  `;
}


  // ========================= Loading =========================
  async function load(reset=false){
    if (busy || (done && !reset)) return;
    busy = true; loadMore.disabled = true; endMsg.hidden = true;
    if (reset){ rows.innerHTML = ""; cursor = null; done = false; }

    try{
      const role = (fRole?.value || "debtor");
      const url  = `${API.LIST(role)}?${qsBuild(params(reset))}`;
      const res  = await fetch(url, { headers: { "Accept": "application/json" } });

      if (!res.ok){
        const txt = await res.text().catch(()=>"(no body)");
        alert(`فشل التحميل\nHTTP ${res.status}\n${txt.slice(0,300)}`);
        return;
      }

      const data = await res.json().catch(()=>({ok:false,error:"bad json"}));
      if (!data.ok){
        alert(`فشل التحميل\n${data.error || "unknown error"}`);
        return;
      }

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
      alert(`فشل التحميل\n${e?.message || e}`);
    }finally{
      busy = false; loadMore.disabled = false;
    }
  }

  // ========================= Events: filters & paging =========================
  btnSearch.addEventListener("click", () => load(true));
  [fQ, fSerial, fId, fFrom, fTo, fStatus, fRole].forEach(i => {
    i?.addEventListener("change", () => load(true));
  });

  loadMore.addEventListener("click", () => load(false));

  // ========================= Row actions (delegate) =========================
  rows.addEventListener("click", (e) => {
    const tr = e.target.closest("tr[data-id]");
    if (!tr) return;

    const role = (fRole?.value || "debtor");
    target.id        = tr.getAttribute("data-id");
    target.provider  = tr.getAttribute("data-provider") || "";
    target.remaining = parseFloat(tr.getAttribute("data-remaining") || "0");

    // Debtor (store owes provider) -> تسديد
    if (role === "debtor"){
      if (e.target.classList.contains("js-full")){
        mFullText.textContent = `هل أنت متأكد من التسديد الكامل إلى (${target.provider}) بمبلغ ${nf(target.remaining)}؟`;
        openModal(mFull);
        return;
      }
      if (e.target.classList.contains("js-batch")){
        mBatchText.textContent = `أدخل الدفعة للمورد (${target.provider})`;
        mBatchHint.textContent = `المتبقي: ${nf(target.remaining)}`;
        mBatchAmount.value = "";
        openModal(mBatch);
        return;
      }
    }

    // Creditor (provider owes store) -> تحصيل
    if (role === "creditor"){
      if (e.target.classList.contains("js-collect-full")){
        mFullText.textContent = `هل أنت متأكد من تحصيل ${nf(target.remaining)} بالكامل من (${target.provider})؟`;
        openModal(mFull);
        return;
      }
      if (e.target.classList.contains("js-collect-batch")){
        mBatchText.textContent = `أدخل التحصيل من المورد (${target.provider})`;
        mBatchHint.textContent = `المتبقي: ${nf(target.remaining)}`;
        mBatchAmount.value = "";
        openModal(mBatch);
        return;
      }
    }
  });

  // ========================= Modals open/close =========================
  function openModal(m){
    m.classList.add("open");
    m.setAttribute("aria-hidden", "false");
  }
  function closeModal(m){
    m.classList.remove("open");
    m.setAttribute("aria-hidden", "true");
  }

  mFullClose.addEventListener("click", () => closeModal(mFull));
  mFull.addEventListener("click",  (e) => { if (e.target === mFull)   closeModal(mFull); });

  mBatchClose.addEventListener("click", () => closeModal(mBatch));
  mBatch.addEventListener("click", (e) => { if (e.target === mBatch)  closeModal(mBatch); });

  // ========================= Confirm handlers =========================
  mFullConfirm.addEventListener("click", async () => {
    if (!target.id) return;
    const role = (fRole?.value || "debtor");
    const url  = role === "creditor" ? API.COLLECT_FULL(target.id) : API.PAY_FULL(target.id);
    try{
      const resp = await fetch(url, { method: "POST", headers: { "X-CSRFToken": getCsrf(), "Accept":"application/json" } });
      const data = await resp.json();
      if (data.ok){ closeModal(mFull); load(true); }
      else{ alert(data.error || "خطأ غير متوقع"); }
    }catch{
      alert("فشل الاتصال بالخادم");
    }
  });

  mBatchConfirm.addEventListener("click", async () => {
    const v = parseFloat(mBatchAmount.value || "0");
    if (!(v > 0)){ alert("أدخل قيمة موجبة."); return; }
    if (v > target.remaining){ alert("القيمة تتجاوز المبلغ المتبقي."); return; }

    const role = (fRole?.value || "debtor");
    const url  = role === "creditor" ? API.COLLECT_BATCH(target.id) : API.PAY_BATCH(target.id);

    const form = new FormData();
    form.append("amount", String(v));

    try{
      const resp = await fetch(url, { method: "POST", body: form, headers: { "X-CSRFToken": getCsrf(), "Accept":"application/json" } });
      const data = await resp.json();
      if (data.ok){ closeModal(mBatch); load(true); }
      else{ alert(data.error || "خطأ غير متوقع"); }
    }catch{
      alert("فشل الاتصال بالخادم");
    }
  });
  (function prefillFromUrl(){
  const p = new URLSearchParams(location.search);
  if (p.has("serial") && fSerial) fSerial.value = p.get("serial");
  if (p.has("id") && fId) fId.value = p.get("id");
  if (p.has("q") && fQ) fQ.value = p.get("q");
  })();
  // ========================= Kickoff =========================
  load(true);
})();
