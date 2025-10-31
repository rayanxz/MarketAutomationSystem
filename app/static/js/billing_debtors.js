// app/static/js/billing_debtors.js
(function () {
  "use strict";

  const API = {
    LIST: "/manager/billing/api/debts/",
    PAY_FULL: (item) => item.manual
    ? `/manager/billing/manual-debts/${item.id}/pay-full/`
    : `/manager/billing/bills/${item.id}/pay-full/`,

  PAY_BATCH: (item) => item.manual
    ? `/manager/billing/manual-debts/${item.id}/pay-batch/`
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

  let cursor = null, busy = false, done = false;
  let target = { bill_id: null, provider: "", remaining: 0 };

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

  // Accepts either DebtorEntry-shaped rows or old Bill-shaped rows
  function normalize(row){
    const providerName = row?.provider?.name ?? row?.provider_name ?? "";
    const billId = row?.bill_id ?? row?.source_id ?? row?.id ?? null; // prefer bill id
    return {
      bill_id: billId,
      serial:  row?.serial ?? "",
      provider_name: providerName,
      total:   row?.total ?? row?.grand_total ?? 0,
      paid:    row?.paid_amount ?? row?.paid ?? 0,
      remaining: row?.remaining ?? ( (row?.total ?? 0) - (row?.paid_amount ?? 0) ),
      status:  row?.status ?? "",
    };
  }

 function arType(t){
  if ((t||"").toLowerCase() === "customer") return "زبون";
  if ((t||"").toLowerCase() === "worker")   return "عامل";
  return "مورد";
}

function rowHtml(src){
  // Normalize
  const partyType  = (src.party_type || "provider");
  const partyName  = src.party_name || (src?.provider?.name || "");
  const serial     = (src.serial ?? src.doc_serial ?? ""); // backend may send any
  const total      = src.total ?? 0;
  const paid       = src.paid_amount ?? 0;
  const remaining  = src.remaining ?? Math.max(0, Number(total)-Number(paid));
  const status     = (src.status || "").toLowerCase();

  const canAct = (status !== "paid" && Number(remaining) > 0);
  const actions = canAct
    ? `<button class="btn js-full">تسديد كامل</button>
       <button class="btn js-batch">تسديد دفعة</button>`
    : `<button class="btn" disabled>لا يوجد إجراء</button>`;

  return `
  <tr class="${statusClass(status)}"
      data-bill="${eh(src.id ?? src.bill_id ?? "")}"
      data-provider="${eh(partyName)}"
      data-remaining="${eh(remaining)}"
      data-manual="${src.manual ? 1 : 0}">

      <td>${eh(serial)}</td>
      <td>${eh(arType(partyType))}</td>
      <td>${eh(partyName)}</td>
      <td>${nf(total)}</td>
      <td>${nf(paid)}</td>
      <td>${nf(remaining)}</td>
      <td>${status === "unpaid" ? "غير مدفوعة" : (status === "partial" ? "مدفوعة جزئياً" : "مدفوعة")}</td>
      <td class="left">${actions}</td>
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

    target.bill_id   = tr.getAttribute("data-bill");
    target.provider  = tr.getAttribute("data-provider") || "";
    target.remaining = parseFloat(tr.getAttribute("data-remaining") || "0");
    target.manual = tr.getAttribute("data-manual") === "1";


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
  });

  function openModal(m){ m.classList.add("open"); m.setAttribute("aria-hidden", "false"); }
  function closeModal(m){ m.classList.remove("open"); m.setAttribute("aria-hidden", "true"); }

  mFullClose?.addEventListener("click", () => closeModal(mFull));
  mFull?.addEventListener("click",  (e) => { if (e.target === mFull) closeModal(mFull); });

  mBatchClose?.addEventListener("click", () => closeModal(mBatch));
  mBatch?.addEventListener("click", (e) => { if (e.target === mBatch) closeModal(mBatch); });

  mFullConfirm?.addEventListener("click", async () => {
    if (!target.bill_id) return;
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
