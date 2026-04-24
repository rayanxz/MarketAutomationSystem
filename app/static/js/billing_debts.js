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
  const mBatchSypField = el("#batch-syp-field");
  const mBatchUsdField = el("#batch-usd-field");
  const mBatchSypHint  = el("#batch-hint-syp");
  const mBatchUsdHint  = el("#batch-hint-usd");
  const mBatchSypInput = el("#batch-amount-syp");
  const mBatchUsdInput = el("#batch-amount-usd");
  const mBatchConfirm = qs('[data-confirm]', mBatch);
  const mBatchClose   = qs('[data-close]',   mBatch);

  // ========================= State =========================
  let cursor = null, busy = false, done = false;
  let target = { id: null, manual: false, provider: "", remaining: 0, entries: {} };

  // ========================= Tiny utils =========================
  function el(s){ return document.querySelector(s); }
  function qs(s, root){ return (root || document).querySelector(s); }
  const moneyFmt = new Intl.NumberFormat(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  function round2(v){
    const n = Number(v || 0);
    if (!Number.isFinite(n)) return 0;
    return Math.round((n + Number.EPSILON) * 100) / 100;
  }
  function nf(x){
    const n = Number(x);
    return Number.isFinite(n) ? moneyFmt.format(round2(n)) : (x ?? "");
  }
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

  function currencyFromDocId(docId){
    const s = String(docId || "").toUpperCase();
    return s.includes(":USD") ? "USD" : "SYP";
  }

  function billKeyFromDocId(docId){
    const s = String(docId || "");
    return s.includes(":") ? s.split(":")[0] : s;
  }

  // Normalize a row (works for debtor & creditor; manual or document)
  function normalizeRow(src){
    const serial    = src.serial ?? src.doc_serial ?? "";
    const partyType = src.party_type || "provider";
    const partyName = src.party_name || (src?.provider?.name || "");
    const total     = round2(src.total ?? src.grand_total ?? 0);
    const paid      = round2(src.paid_amount ?? src.paid ?? 0); // creditor uses "paid_amount" = collected in serializer
    const remaining = round2(
      src.remaining != null ? src.remaining : Math.max(0, total - paid)
    );
    const status    = (src.status || "").toLowerCase();

    // bill/return id vs manual entry id is already handled by backend serializer "id"
    const docId = src.id ?? src.bill_id ?? src.source_id ?? null;
    const entryId = src.entry_id ?? src.id ?? null;
    const currency = currencyFromDocId(docId);
    const billKey = billKeyFromDocId(docId);

    return {
      id: docId,
      entryId,
      billKey,
      serial,
      partyType,
      partyName,
      total,
      paid,
      remaining,
      status,
      manual: !!src.manual,
      currency,
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
          data-entry-id="${escapeHtml(String(b.entryId ?? ""))}"
          data-bill-key="${escapeHtml(String(b.billKey ?? ""))}"
          data-currency="${escapeHtml(String(b.currency ?? ""))}"
          data-provider="${escapeHtml(b.partyName)}"
          data-total="${escapeHtml(String(b.total))}"
          data-remaining="${escapeHtml(String(b.remaining))}"
          data-manual="${b.manual ? "true" : "false"}">
        <td>${escapeHtml(String(b.serial ?? ""))}</td>
        <td>${escapeHtml(arType(b.partyType))}</td>
        <td>${escapeHtml(b.partyName)}</td>
        <td>${escapeHtml(b.currency)}</td>
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
    target.entryId   = tr.getAttribute("data-entry-id");
    target.provider  = tr.getAttribute("data-provider") || "";
    target.remaining = parseFloat(tr.getAttribute("data-remaining") || "0");
    target.manual    = (tr.getAttribute("data-manual") === "true");
    target.entries   = {};

    if (role === "debtor" && !target.manual){
      const billKey = tr.getAttribute("data-bill-key");
      const all = [...rows.querySelectorAll(`tr[data-bill-key="${billKey}"]`)];
      all.forEach(r => {
        if ((r.getAttribute("data-manual") || "") !== "false") return;
        const cur = (r.getAttribute("data-currency") || "SYP").toUpperCase();
        target.entries[cur] = {
          entryId: r.getAttribute("data-entry-id"),
          total: parseFloat(r.getAttribute("data-total") || "0"),
          remaining: parseFloat(r.getAttribute("data-remaining") || "0"),
        };
      });
    }

    if (role === "debtor"){
      if (e.target.classList.contains("js-full")){
        const rs = target.entries["SYP"]?.remaining ?? target.remaining;
        const ru = target.entries["USD"]?.remaining ?? 0;
        mFullText.textContent = `U?U, O?U+O? U.O?O?U?O_ U.U+ OÒU,O?O3O_USO_ OÒU,U?OÒU.U, O?U,U% (${target.provider}) O"U.O"U,O? ${nf(rs)} SYP${ru ? ` + ${nf(ru)} USD` : ""}OY`;
        openModal(mFull); return;
      }
      if (e.target.classList.contains("js-batch")){
        mBatchText.textContent = `O?O_OrU, OÒU,O_U?O1Oc U,U,U.U^O?O_ (${target.provider})`;
        const rs = target.entries["SYP"]?.remaining ?? target.remaining;
        const ru = target.entries["USD"]?.remaining ?? 0;
        if (mBatchSypField) mBatchSypField.style.display = (rs > 0 ? "block" : "none");
        if (mBatchUsdField) mBatchUsdField.style.display = (ru > 0 ? "block" : "none");
        if (mBatchSypHint) mBatchSypHint.textContent = `OÒU,U.O?O"U,US: ${nf(rs)}`;
        if (mBatchUsdHint) mBatchUsdHint.textContent = `OÒU,U.O?O"U,US: ${nf(ru)}`;
        if (mBatchSypInput) mBatchSypInput.value = "";
        if (mBatchUsdInput) mBatchUsdInput.value = "";
        openModal(mBatch); return;
      }
    } else {
      if (e.target.classList.contains("js-collect-full")){
        mFullText.textContent = `U?U, O?U+O? U.O?O?U?O_ U.U+ O?O-O?USU, ${nf(target.remaining)} O"OÒU,U?OÒU.U, U.U+ (${target.provider})OY`;
        openModal(mFull); return;
      }
      if (e.target.classList.contains("js-collect-batch")){
        mBatchText.textContent = `O?O_OrU, OÒU,O?O-O?USU, U.U+ OÒU,U.U^O?O_ (${target.provider})`;
        if (mBatchSypField) mBatchSypField.style.display = "block";
        if (mBatchUsdField) mBatchUsdField.style.display = "none";
        if (mBatchSypHint) mBatchSypHint.textContent = `OÒU,U.O?O"U,US: ${nf(target.remaining)}`;
        if (mBatchSypInput) mBatchSypInput.value = "";
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
  function entryPayBatchUrl(entryId){
    return `/manager/debts/api/entry/debtor/${entryId}/pay-batch/`;
  }

  async function postPayPartial(entryId, amount){
    const form = new FormData();
    form.append("amount", String(amount));
    const resp = await fetch(entryPayBatchUrl(entryId), { method: "POST", body: form, headers: { "X-CSRFToken": getCsrf(), "Accept":"application/json" } });
    const data = await resp.json().catch(()=>({ok:false}));
    if (!data.ok) throw new Error(data.error || "server error");
  }

  mFullConfirm?.addEventListener("click", async () => {
    if (!target.id) return;
    const url = urlForConfirmFull();
    try{
      if ((fRole?.value || "debtor") === "debtor" && !target.manual){
        const rs = round2(target.entries["SYP"]?.remaining ?? 0);
        const ru = round2(target.entries["USD"]?.remaining ?? 0);
        const tasks = [];
        if (rs > 0 && target.entries["SYP"]?.entryId) tasks.push(postPayPartial(target.entries["SYP"].entryId, rs));
        if (ru > 0 && target.entries["USD"]?.entryId) tasks.push(postPayPartial(target.entries["USD"].entryId, ru));
        if (!tasks.length){ alert("OÒU,O_U?O1 O?USO? U.U+O?OÒO?O?."); return; }
        await Promise.all(tasks);
        closeModal(mFull); load(true);
      } else {
        const resp = await fetch(url, { method: "POST", headers: { "X-CSRFToken": getCsrf(), "Accept":"application/json" } });
        const data = await resp.json().catch(()=>({ok:false}));
        if (data.ok){ closeModal(mFull); load(true); }
        else{ alert(data.error || "OrO?O? O?USO? U.O?U^U,O1"); }
      }
    }catch{
      alert("U?O'U, OÒU,OÒO?O?OÒU, O"OÒU,OrOÒO_U.");
    }
  });

  mBatchConfirm?.addEventListener("click", async () => {
    try{
      if ((fRole?.value || "debtor") === "debtor" && !target.manual){
        const rs = round2(parseFloat(mBatchSypInput?.value || "0") || 0);
        const ru = round2(parseFloat(mBatchUsdInput?.value || "0") || 0);
        const maxS = round2(target.entries["SYP"]?.remaining ?? 0);
        const maxU = round2(target.entries["USD"]?.remaining ?? 0);

        if (rs < 0 || ru < 0){ alert("O?O_OrU, U,USU.Oc U.U^O?O"Oc."); return; }
        if (rs > maxS){ alert("OÒU,U,USU.Oc O?O?O?OÒU^O? OÒU,U.O"U,O? OÒU,U.O?O"U,US."); return; }
        if (ru > maxU){ alert("OÒU,U,USU.Oc O?O?O?OÒU^O? OÒU,U.O"U,O? OÒU,U.O?O"U,US."); return; }
        if (!(rs > 0 || ru > 0)){ alert("O?O_OrU, U,USU.Oc U.U^O?O"Oc."); return; }

        const tasks = [];
        if (rs > 0 && target.entries["SYP"]?.entryId) tasks.push(postPayPartial(target.entries["SYP"].entryId, rs));
        if (ru > 0 && target.entries["USD"]?.entryId) tasks.push(postPayPartial(target.entries["USD"].entryId, ru));
        await Promise.all(tasks);
        closeModal(mBatch); load(true);
      } else {
        const v = round2(parseFloat(mBatchSypInput?.value || "0") || 0);
        if (!(v > 0)){ alert("O?O_OrU, U,USU.Oc U.U^O?O"Oc."); return; }
        if (v > round2(target.remaining)){ alert("OÒU,U,USU.Oc O?O?O?OÒU^O? OÒU,U.O"U,O? OÒU,U.O?O"U,US."); return; }

        const url  = urlForConfirmBatch();
        const form = new FormData();
        form.append("amount", String(v));

        const resp = await fetch(url, { method: "POST", body: form, headers: { "X-CSRFToken": getCsrf(), "Accept":"application/json" } });
        const data = await resp.json().catch(()=>({ok:false}));
        if (data.ok){ closeModal(mBatch); load(true); }
        else{ alert(data.error || "OrO?O? O?USO? U.O?U^U,O1"); }
      }
    }catch{
      alert("U?O'U, OÒU,OÒO?O?OÒU, O"OÒU,OrOÒO_U.");
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
