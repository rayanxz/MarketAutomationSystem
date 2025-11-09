// app/static/js/billing_debtors.js
(function () {
  "use strict";

  const API = {
   LIST: "/manager/debts/api/debts/",
   PAY_FULL:  (item) => item.manual
     ? `/manager/debts/manual/${item.id}/pay-full/`
     : `/manager/billing/bills/${item.id}/pay-full/`,
   PAY_BATCH: (item) => item.manual
     ? `/manager/debts/manual/${item.id}/pay-batch/`
     : `/manager/billing/bills/${item.id}/pay-batch/`,
  };

  const $ = (s, r=document) => r.querySelector(s);
  const rows      = $("#rows");
  const loadMore  = $("#loadMore");
  const endMsg    = $("#endMsg");

  const fQ        = $("#fQ");
  const fSerial   = $("#fSerial");
  const fFrom     = $("#fFrom");
  const fTo       = $("#fTo");
  const fStatus   = $("#fStatus");
  const btnSearch = $("#btnSearch");

  const mFull        = $("#modal-full");
  const mFullText    = $("#full-text");
  const mFullConfirm = $('[data-confirm]', mFull);
  const mFullClose   = $('[data-close]',   mFull);

  const mBatch        = $("#modal-batch");
  const mBatchText    = $("#batch-text");
  const mBatchHint    = $("#batch-hint");
  const mBatchAmount  = $("#batch-amount");
  const mBatchConfirm = $('[data-confirm]', mBatch);
  const mBatchClose   = $('[data-close]',   mBatch);

  const VIEW_URL = (entryId) => `/manager/debts/view/debtor/${entryId}/`;


  let cursor = null, busy = false, done = false;
  let target = { id: null, provider: "", remaining: 0, manual: false };

  function nf(x){ const n = Number(x); return Number.isFinite(n) ? new Intl.NumberFormat().format(n) : (x ?? ""); }
  function eh(s){ return String(s ?? "").replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
  function getCsrf(){ const m = document.cookie.match(/(?:^|;)\s*csrftoken=([^;]+)/); return m ? decodeURIComponent(m[1]) : ""; }
  function qs(obj){
    const u = new URLSearchParams();
    Object.entries(obj).forEach(([k,v]) => { if (v !== "" && v != null) u.append(k, v); });
    return u.toString();
  }

  function params(reset=false){
    const p = {
      page_size: 30,
      q:        (fQ?.value||"").trim(),
      serial:   (fSerial?.value||"").trim(),   // backend may ignore; safe to send
      date_from: fFrom?.value || "",
      date_to:   fTo?.value || "",
      status:   fStatus?.value || "",
    };
    if (!reset && cursor) p.cursor = cursor;
    return p;
  }

  function statusClass(st){
    if ((st||"").toLowerCase() === "unpaid")  return "is-unpaid";
    if ((st||"").toLowerCase() === "partial") return "is-partial";
    return "is-paid";
  }

 
 function arType(t){
  if ((t||"").toLowerCase() === "customer") return "زبون";
  if ((t||"").toLowerCase() === "worker")   return "عامل";
  return "مورد";
}

function rowHtml(src){
  // Normalize (force numbers)
  const partyType  = (src.party_type || "provider");
  const partyName  = src.party_name || (src?.provider?.name || "");
  const serial     = (src.serial ?? src.doc_serial ?? "");
  const totalNum   = Number(src.total ?? 0);
  const paidNum    = Number(src.paid_amount ?? 0);
  const remainingNum = (src.remaining != null) ? Number(src.remaining) : Math.max(0, totalNum - paidNum);
  const status     = (src.status || "").toLowerCase();

  const canAct = (status !== "paid" && remainingNum > 0);
  const actions = canAct
    ? `<button class="btn js-full">تسديد كامل</button>
       <button class="btn js-batch">تسديد دفعة</button>`
    : `<button class="btn" disabled>لا يوجد إجراء</button>`;

  return `
  <tr class="${statusClass(status)}"
      data-bill="${eh(src.id ?? src.bill_id ?? "")}"
      data-provider="${eh(partyName)}"
      data-remaining="${String(remainingNum)}"
      data-manual="${src.manual ? "1" : "0"}">

      <td>${eh(serial)}</td>
      <td>${eh(arType(partyType))}</td>
      <td>${eh(partyName)}</td>
      <td>${nf(totalNum)}</td>
      <td>${nf(paidNum)}</td>
      <td>${nf(remainingNum)}</td>
      <td>${status === "unpaid" ? "غير مدفوعة" : (status === "partial" ? "مدفوعة جزئياً" : "مدفوعة")}</td>
      <td class="left">${actions} <a class="btn" href="/manager/debts/view/debtor/${src.entry_id ?? ""}/">عرض</a></td>
    </tr>
  `;
}


  async function load(reset=false){
    if (busy || (done && !reset)) return;
    busy = true; loadMore.disabled = true; endMsg.hidden = true;
    if (reset){ rows.innerHTML = ""; cursor = null; done = false; }

    const url = `${API.LIST}?${qs(params(reset))}`;
    try{
      const res  = await fetch(url, { headers: { "Accept": "application/json" } });
      if (!res.ok){
        const txt = await res.text().catch(()=>"(no body)");
        alert(`فشل التحميل\nHTTP ${res.status}\n${txt.slice(0,300)}`);
        return;
      }
      const data = await res.json();
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

  // events
  btnSearch?.addEventListener("click", () => load(true));
  [fQ, fSerial, fFrom, fTo, fStatus].forEach(i => i?.addEventListener("change", () => load(true)));
  loadMore?.addEventListener("click", () => load(false));

  rows.addEventListener("click", (e) => {
  const tr = e.target.closest("tr[data-bill]");
  if (!tr) return;

  const ds = tr.dataset;
  target.id        = ds.bill || "";
  target.provider  = ds.provider || "";
  target.remaining = Number(ds.remaining || "0");
  target.manual    = (ds.manual === "1" || ds.manual === "true");

  if (e.target.classList.contains("js-full")){
    mFullText.textContent = `هل أنت متأكد من التسديد الكامل إلى (${target.provider}) بمبلغ ${nf(target.remaining)}؟`;
    openModal(mFull); return;
  }
  if (e.target.classList.Contains?.("js-batch") || e.target.classList.contains("js-batch")){
    mBatchText.textContent = `أدخل الدفعة للمورد (${target.provider})`;
    mBatchHint.textContent = `المتبقي: ${nf(target.remaining)}`;
    mBatchAmount.value = "";
    openModal(mBatch); return;
  }
});


  function openModal(m){ m.classList.add("open"); m.setAttribute("aria-hidden", "false"); }
  function closeModal(m){ m.classList.remove("open"); m.setAttribute("aria-hidden", "true"); }

  mFullClose?.addEventListener("click", () => closeModal(mFull));
  mFull?.addEventListener("click",  (e) => { if (e.target === mFull) closeModal(mFull); });

  mBatchClose?.addEventListener("click", () => closeModal(mBatch));
  mBatch?.addEventListener("click", (e) => { if (e.target === mBatch) closeModal(mBatch); });

  mFullConfirm?.addEventListener("click", async () => {
    if (!target.id) return;
    try{
      const resp = await fetch(API.PAY_FULL(target), { method: "POST", headers: { "X-CSRFToken": getCsrf(), "Accept":"application/json" } });
      const data = await resp.json().catch(()=>({ok:false,error:"bad json"}));
      if (data.ok){ closeModal(mFull); load(true); } else { alert(data.error || "خطأ غير متوقع"); }
    }catch{ alert("فشل الاتصال بالخادم"); }
  });

  mBatchConfirm?.addEventListener("click", async () => {
    const v = parseFloat(mBatchAmount.value || "0");
    if (!(v > 0)){ alert("أدخل قيمة موجبة."); return; }
    if (v > target.remaining){ alert("القيمة تتجاوز المبلغ المتبقي."); return; }

    const form = new FormData();
    form.append("amount", String(v));
    try{
      const resp = await fetch(API.PAY_BATCH(target), { method: "POST", body: form, headers: { "X-CSRFToken": getCsrf(), "Accept":"application/json" } });
      const data = await resp.json().catch(()=>({ok:false,error:"bad json"}));
      if (data.ok){ closeModal(mBatch); load(true); } else { alert(data.error || "خطأ غير متوقع"); }
    }catch{ alert("فشل الاتصال بالخادم"); }
  });

  // prefill from URL
  (function(){
    const p = new URLSearchParams(location.search);
    if (p.has("serial") && fSerial) fSerial.value = p.get("serial");
    if (p.has("q") && fQ) fQ.value = p.get("q");
  })();

  load(true);
})();
