// app/static/js/billing_debts.js
(function () {
  "use strict";

  // ========================= API endpoints =========================
  const API = {
  LIST: (role) =>
    role === "creditor"
      ? "/manager/billing/api/returns/list/"  // was billing returns list
      : "/manager/debts/api/debts/",     // was /manager/billing/api/debts/

  // Debtor (store owes provider)
   DEBTOR: {
      PAY_FULL_BILL:    (id) => `/manager/billing/bills/${id}/pay-full/`,
      PAY_BATCH_BILL:   (id) => `/manager/billing/bills/${id}/pay-batch/`,
      PAY_FULL_MANUAL:  (id) => `/manager/debts/manual-debts/${id}/pay-full/`,
      PAY_BATCH_MANUAL: (id) => `/manager/debts/manual-debts/${id}/pay-batch/`,
    },

  // Creditor (provider owes store)
  CREDITOR: {
      COLLECT_FULL_RET:    (id) => `/manager/billing/returns/${id}/collect-full/`,
      COLLECT_BATCH_RET:   (id) => `/manager/billing/returns/${id}/collect-batch/`,
      COLLECT_FULL_MANUAL: (id) => `/manager/debts/manual-creditors/${id}/collect-full/`,
      COLLECT_BATCH_MANUAL:(id) => `/manager/debts/manual-creditors/${id}/collect-batch/`,
    },
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
  const fRole     = el("#fRole"); // debtor | creditor
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
  let target = { id: null, manual: false, provider: "", remaining: 0 };

  // ========================= Tiny utils =========================
  function el(s){ return document.querySelector(s); }
  function qs(s, root){ return (root || document).querySelector(s); }
  function nf(x){ const n = Number(x); return Number.isFinite(n) ? new Intl.NumberFormat().format(n) : (x ?? ""); }
  function escapeHtml(s){ return String(s||"").replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
  function getCsrf(){ const m = document.cookie.match(/(?:^|;)\s*csrftoken=([^;]+)/); return m ? decodeURIComponent(m[1]) : ""; }
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
      id:       (fId?.value || "").trim(),
      date_from: fFrom?.value || "",
      date_to:   fTo?.value || "",
      status:   fStatus?.value || "",
    };
    if (!reset && cursor) p.cursor = cursor;
    return p;
  }

  // ========================= Rendering helpers =========================
  function statusClass(st){
    const s = (st || "").toLowerCase();
    if (s === "unpaid")  return "is-unpaid";
    if (s === "partial") return "is-partial";
    return "is-paid";
  }

  function arType(t){
    const x = (t || "").toLowerCase();
    if (x === "customer") return "زبون";
    if (x === "worker")   return "عامل";
    return "مورد";
  }

  // Normalize a row (works for debtor & creditor; manual or document)
  function normalizeRow(src){
    const serial    = src.serial ?? src.doc_serial ?? "";
    const partyType = src.party_type || "provider";
    const partyName = src.party_name || (src?.provider?.name || "");
    const total     = Number(src.total ?? src.grand_total ?? 0);
    const paid      = Number(src.paid_amount ?? src.paid ?? 0); // creditor uses "paid_amount" = collected in serializer
    const remaining = Number(
      src.remaining != null ? src.remaining : Math.max(0, total - paid)
    );
    const status    = (src.status || "").toLowerCase();

    // bill/return id vs manual entry id is already handled by backend serializer "id"
    const idForAction = src.id ?? src.bill_id ?? src.source_id ?? null;

    return {
      id: idForAction,
      serial,
      partyType,
      partyName,
      total,
      paid,
      remaining,
      status,
      manual: !!src.manual,
    };
  }

  function actionButtons(role, canAct){
    if (!canAct) return `<button class="btn" disabled>لا يوجد إجراء</button>`;
    if (role === "creditor"){
      return `
        <button class="btn js-collect-full">تحصيل كامل</button>
        <button class="btn js-collect-batch">تحصيل دفعة</button>
      `;
    }
    return `
      <button class="btn js-full">تسديد كامل</button>
      <button class="btn js-batch">تسديد دفعة</button>
    `;
  }

  function rowHtml(src){
    const b = normalizeRow(src);
    const role = (fRole?.value || "debtor");
    const canAct = (b.status !== "paid" && b.remaining > 0);

    return `
      <tr class="${statusClass(b.status)}"
          data-id="${escapeHtml(String(b.id ?? ""))}"
          data-provider="${escapeHtml(b.partyName)}"
          data-remaining="${escapeHtml(String(b.remaining))}"
          data-manual="${b.manual ? "true" : "false"}">
        <td>${escapeHtml(String(b.serial ?? ""))}</td>
        <td>${escapeHtml(arType(b.partyType))}</td>
        <td>${escapeHtml(b.partyName)}</td>
        <td>${nf(b.total)}</td>
        <td>${nf(b.paid)}</td>
        <td>${nf(b.remaining)}</td>
        <td>${
          b.status === "unpaid" ? "غير مدفوعة"
          : (b.status === "partial" ? "مدفوعة جزئياً" : "مدفوعة")
        }</td>
        <td class="left">${actionButtons(role, canAct)}</td>
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
  btnSearch?.addEventListener("click", () => load(true));
  [fQ, fSerial, fId, fFrom, fTo, fStatus, fRole].forEach(i => {
    i?.addEventListener("change", () => load(true));
  });

  loadMore?.addEventListener("click", () => load(false));

  // ========================= Row actions (delegate) =========================
  rows.addEventListener("click", (e) => {
    const tr = e.target.closest("tr[data-id]");
    if (!tr) return;

    const role = (fRole?.value || "debtor");
    target.id        = tr.getAttribute("data-id");
    target.provider  = tr.getAttribute("data-provider") || "";
    target.remaining = parseFloat(tr.getAttribute("data-remaining") || "0");
    target.manual    = (tr.getAttribute("data-manual") === "true");

    if (role === "debtor"){
      if (e.target.classList.contains("js-full")){
        mFullText.textContent = `هل أنت متأكد من التسديد الكامل إلى (${target.provider}) بمبلغ ${nf(target.remaining)}؟`;
        openModal(mFull); return;
      }
      if (e.target.classList.contains("js-batch")){
        mBatchText.textContent = `أدخل الدفعة للمورد (${target.provider})`;
        mBatchHint.textContent = `المتبقي: ${nf(target.remaining)}`;
        mBatchAmount.value = "";
        openModal(mBatch); return;
      }
    } else {
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
    }
  });

  // ========================= Modals open/close =========================
  function openModal(m){ m.classList.add("open"); m.setAttribute("aria-hidden", "false"); }
  function closeModal(m){ m.classList.remove("open"); m.setAttribute("aria-hidden", "true"); }

  mFullClose?.addEventListener("click", () => closeModal(mFull));
  mFull?.addEventListener("click",  (e) => { if (e.target === mFull)   closeModal(mFull); });

  mBatchClose?.addEventListener("click", () => closeModal(mBatch));
  mBatch?.addEventListener("click", (e) => { if (e.target === mBatch)  closeModal(mBatch); });

  // ========================= URL helpers by role/manual =========================
  function urlForConfirmFull(){
    const role = (fRole?.value || "debtor");
    if (role === "creditor"){
      return target.manual ? API.CREDITOR.COLLECT_FULL_MANUAL(target.id)
                           : API.CREDITOR.COLLECT_FULL_RET(target.id);
    }
    return target.manual ? API.DEBTOR.PAY_FULL_MANUAL(target.id)
                         : API.DEBTOR.PAY_FULL_BILL(target.id);
  }

  function urlForConfirmBatch(){
    const role = (fRole?.value || "debtor");
    if (role === "creditor"){
      return target.manual ? API.CREDITOR.COLLECT_BATCH_MANUAL(target.id)
                           : API.CREDITOR.COLLECT_BATCH_RET(target.id);
    }
    return target.manual ? API.DEBTOR.PAY_BATCH_MANUAL(target.id)
                         : API.DEBTOR.PAY_BATCH_BILL(target.id);
  }

  // ========================= Confirm handlers =========================
  mFullConfirm?.addEventListener("click", async () => {
    if (!target.id) return;
    const url = urlForConfirmFull();
    try{
      const resp = await fetch(url, { method: "POST", headers: { "X-CSRFToken": getCsrf(), "Accept":"application/json" } });
      const data = await resp.json().catch(()=>({ok:false}));
      if (data.ok){ closeModal(mFull); load(true); }
      else{ alert(data.error || "خطأ غير متوقع"); }
    }catch{
      alert("فشل الاتصال بالخادم");
    }
  });

  mBatchConfirm?.addEventListener("click", async () => {
    const v = parseFloat(mBatchAmount.value || "0");
    if (!(v > 0)){ alert("أدخل قيمة موجبة."); return; }
    if (v > target.remaining + 1e-9){ alert("القيمة تتجاوز المبلغ المتبقي."); return; }

    const url  = urlForConfirmBatch();
    const form = new FormData();
    form.append("amount", String(v));

    try{
      const resp = await fetch(url, { method: "POST", body: form, headers: { "X-CSRFToken": getCsrf(), "Accept":"application/json" } });
      const data = await resp.json().catch(()=>({ok:false}));
      if (data.ok){ closeModal(mBatch); load(true); }
      else{ alert(data.error || "خطأ غير متوقع"); }
    }catch{
      alert("فشل الاتصال بالخادم");
    }
  });

  // ========================= Prefill & Kickoff =========================
  (function prefillFromUrl(){
    const p = new URLSearchParams(location.search);
    if (p.has("serial") && fSerial) fSerial.value = p.get("serial");
    if (p.has("id") && fId) fId.value = p.get("id");
    if (p.has("q") && fQ) fQ.value = p.get("q");
  })();

  load(true);
})();
