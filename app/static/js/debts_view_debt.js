(() => {
  "use strict";

  const S = window.__DEBT_VIEW__;
  const $ = (s, r=document) => r.querySelector(s);
  const summary = $("#summary");
  const movHead = $("#movHead");
  const movRows = $("#movRows");
  const remRows = $("#remRows");

  const btnFull  = $("#btnFull");
  const btnBatch = $("#btnBatch");
  const mcSelect = $("#mcSelect");
  const mBatch   = $("#mBatch");
  const mAmt     = $("#mBatchAmount");
  const mHint    = $("#mBatchHint");

  let currentCurrency = "SYP";

  const remDate  = $("#remDate");
  const btnRem   = $("#btnRem");
  const remHint  = $("#remHint");

  function fmtNum(x){
    if (x === null || x === undefined) return "";
    const n = Number(x);
    if (Number.isFinite(n)) return n.toFixed(2);
    return String(x);
  }

  function getCsrf(){
  const m = document.cookie.match(/(?:^|;)\s*csrftoken=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : "";
}


  function openModal(el){ el.classList.add("open"); }
  function closeModal(el){ el.classList.remove("open"); }
  mBatch.addEventListener("click", e => { if (e.target.matches(".modal")) closeModal(mBatch); });
  mBatch.querySelector("[data-close]").onclick = () => closeModal(mBatch);

  function renderSummary(item){
  const dirLabel = item.direction === "debtor" ? "مدين" : "دائن";
  const partyType = arType(item.party_type || "provider");
  const partyName = item.party_name || (item.provider?.name || "");
  const serial = item.doc_serial ?? "—";
  const statusAr = arStatus(item.status);
  const currency = (item.currency_code || "SYP").toUpperCase();
  currentCurrency = currency;

  // labels depend on direction
  const paidLabel = item.direction === "debtor" ? "المدفوع" : "المحصّل";

  // numbers
  const total = Number(item.total || 0);
  const paid  = Number(item.paid_amount || 0);
  const rem   = Number(item.remaining || Math.max(0, total - paid));

  const topSection = [
    ["نوع الدين", dirLabel],
    ["نوع الطرف الآخر", partyType],
    ["اسم الطرف الآخر", partyName],
    ["رقم السيريال", serial],
    ["مصدر السجل", arSource(item)],
    ["??????", currency],
  ];

  const metaSection = [
    ["تاريخ الإنشاء", item.created_at ? new Date(item.created_at).toLocaleString() : "—"],
    ["الحالة", statusAr],
  ];

  const amountsSection = [
    ["الإجمالي", fmtNum(total)],
    [paidLabel, fmtNum(paid)],
    ["المتبقي", fmtNum(rem)],
  ];

  const currentRem = item.current_reminder?.due_date || null;

  // build cards in order: top → meta → amounts → current reminder
  const parts = [];

  function cards(rows){
    return rows.map(([k,v]) => (
      `<div class="card"><div class="label">${k}</div><div class="val">${v ?? ""}</div></div>`
    )).join("");
  }

  // Top group
  parts.push(cards(topSection));
  // Meta (below top)
  parts.push(cards(metaSection));
  // Amounts (below meta)
  parts.push(cards(amountsSection));
  // Current reminder
  parts.push(
    `<div class="card"><div class="label">التذكير الحالي</div><div class="val">${currentRem || "لا يوجد"}</div></div>`
  );

  summary.innerHTML = parts.join("");

  // ---- Disable actions if CLOSED ----
  const isClosed = (item.status || "").toLowerCase() === "closed";
  btnFull.disabled  = isClosed;
  btnBatch.disabled = isClosed;
  remDate.disabled  = isClosed;
  btnRem.disabled   = isClosed;

  // also update action button labels depending on direction
  btnFull.textContent  = item.direction === "debtor" ? "تسديد كامل" : "تحصيل كامل";
  btnBatch.textContent = item.direction === "debtor" ? "تسديد دفعة" : "تحصيل دفعة";

  // If closed, hint to user
  const hint = document.getElementById("actionLockHint") || document.createElement("div");
  hint.id = "actionLockHint";
  hint.className = "muted";
  hint.style.marginTop = "6px";
  hint.textContent = isClosed ? "الحالة مغلقة: لا يمكن إجراء عمليات أو ضبط تذكير." : "";
  // insert after actions bar (if not yet inserted)
  const actionsBar = btnFull.closest(".row");
  if (actionsBar && !document.getElementById("actionLockHint")) {
    actionsBar.appendChild(hint);
  }
}

  function arType(t){
  const x = (t || "").toLowerCase();
  if (x === "customer") return "زبون";
  if (x === "worker")   return "عامل";
  return "مورد";
}

function arStatus(s){
  const x = (s || "").toLowerCase();
  return x === "closed" ? "مغلقة" : "مفتوحة";
}

function arSource(item){
  const app = (item.source_app || "").toLowerCase();
  const model = (item.source_model || "").toLowerCase();
  const serial = item.doc_serial != null ? String(item.doc_serial) : "—";

  // billing.Bill
  if (app === "billing" && model === "bill") {
    return `فاتورة مشتريات رقمها (${serial})`;
  }
  // billing.ProviderReturn
  if (app === "billing" && model === "providerreturn") {
    return `فاتورة مرتجعات رقمها (${serial})`;
  }
  // debts.ManualDebt
  if (app === "debts" && model === "manualdebt") {
    return `دين غير مربوط بسلع`;
  }
  // fallback
  return `${item.source_app}.${item.source_model}#${item.source_id}`;
}


  function renderMovements(item){
  movHead.innerHTML = "";
  movRows.innerHTML = "";

  if (item.direction === "debtor"){
    // ====== مدين ======
    movHead.innerHTML = `<th>???????</th><th>??????</th><th>??????</th><th>???????</th><th>???????</th>`;
    (item.payments || []).forEach(p => {
      movRows.insertAdjacentHTML("beforeend",
        `<tr>
          <td>${new Date(p.created_at).toLocaleString()}</td>
          <td>${fmtNum(p.amount)}</td>
          <td>${p.currency_code || currentCurrency}</td>
          <td>${p.container_name || ""}</td>
          <td>${p.receipt_serial || ""}</td>
        </tr>`);
    });
    btnFull.textContent  = "تسديد كامل";
    btnBatch.textContent = "تسديد دفعة";
    $("#mBatchTitle").textContent = "إدخال دفعة";
  } else {
    // ====== دائن ======
    movHead.innerHTML = `<th>???????</th><th>??????</th><th>??????</th><th>???????</th><th>???????</th>`;
    (item.receipts || []).forEach(r => {
      movRows.insertAdjacentHTML("beforeend",
        `<tr>
          <td>${new Date(r.created_at).toLocaleString()}</td>
          <td>${fmtNum(r.amount)}</td>
          <td>${r.currency_code || currentCurrency}</td>
          <td>${r.container_name || ""}</td>
          <td>${r.receipt_serial || ""}</td>
        </tr>`);
    });
    btnFull.textContent  = "تحصيل كامل";
    btnBatch.textContent = "تحصيل دفعة";
    $("#mBatchTitle").textContent = "إدخال تحصيل";
  }
}


  function renderReminders(item){
    remRows.innerHTML = "";
    (item.reminders_history || []).forEach(r => {
      remRows.insertAdjacentHTML("beforeend",
        `<tr><td>${new Date(r.set_at).toLocaleString()}</td><td>${r.due_date}</td></tr>`);
    });
    if (item.current_reminder?.due_date) {
      remDate.value = item.current_reminder.due_date;
      remHint.textContent = "سيتم استبدال التذكير الحالي.";
    } else {
      remDate.value = "";
      remHint.textContent = "لا يوجد تذكير حالي.";
    }
  }

  async function getJSON(url){
    const r = await fetch(url, {headers: {"X-Requested-With":"XMLHttpRequest"}});
    return r.json();
  }

  async function postForm(url, formData){
    const r = await fetch(url, {
      method:"POST",
      headers: {
        "X-Requested-With":"XMLHttpRequest",
        "X-CSRFToken": getCsrf(),
    },
      body: formData
    });
    return r.json();
  }

  async function refresh(){
    const res = await getJSON(S.API.DETAILS);
    if (!res.ok){ alert(res.error || "error"); return; }
    const it = res.item;
    renderSummary(it);
    renderMovements(it);
    renderReminders(it);
  }

  // actions
  btnFull.onclick = async () => {
    const mcId = mcSelect?.value || "";
    if (!mcId){ alert("???? ??????? ???????."); return; }
    const form = new FormData();
    form.append("money_container_id", mcId);
    form.append("currency_code", currentCurrency || "SYP");
    const res = await postForm(S.API.PAY_FULL, form);
    if (!res.ok){ alert(res.error || "error"); return; }
    await refresh();
  };

  btnBatch.onclick = () => {
    mAmt.value = "";
    mHint.textContent = "";
    openModal(mBatch);
  };

  mBatch.querySelector("[data-confirm]").onclick = async () => {
    const mcId = mcSelect?.value || "";
    if (!mcId){ alert("???? ??????? ???????."); return; }
    const f = new FormData();
    f.append("amount", mAmt.value || "");
    f.append("money_container_id", mcId);
    f.append("currency_code", currentCurrency || "SYP");
    const res = await postForm(S.API.PAY_BATCH, f);
    if (!res.ok){ alert(res.error || "error"); return; }
    closeModal(mBatch);
    await refresh();
  };

  btnRem.onclick = async () => {
    const d = (remDate.value || "").trim();
    if (!d){ alert("اختر تاريخ التذكير"); return; }
    const f = new FormData();
    f.append("due_date", d);
    const res = await postForm(S.API.REM_SET, f);
    if (!res.ok){ alert(res.error || "error"); return; }
    remHint.textContent = "تم حفظ التذكير.";
    await refresh();
  };

  // boot
  refresh();
})();
