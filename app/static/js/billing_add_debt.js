// app/static/js/billing_add_debt.js
(() => {
  "use strict";

  const API = {
    PROV_AC: "/manager/billing/api/providers/ac/",            // stays in billing
    BILL_NEXT: "/manager/billing/api/bill/next-serial/",      // ok
    RET_NEXT:  "/manager/billing/api/returns/next-serial/",   // ok
    SAVE: "/manager/debts/api/manual/save/",                  // <<< FIX
};

  // el helpers
  const $ = (s, r=document) => r.querySelector(s);
  const dir     = $("#dir");
  const ptype   = $("#ptype");
  const serial  = $("#serial");
  const amount  = $("#amount");
  const currency = $("#currency");
  const initialPayment = $("#initialPayment");
  const moneyContainer = $("#moneyContainer");
  const due     = $("#due");

  const partyName = $("#partyName");
  const partyId   = $("#partyId");
  const acList    = $("#acList");
  const partyErr  = $("#partyErr");

  const saveBtn = $("#save");
  const saveErr = $("#saveErr");

  function nf(x){
    const n = Number(x);
    return Number.isFinite(n) ? formatMoney(n) : x;
  }
  function getCsrf(){ const m = document.cookie.match(/(?:^|;)\s*csrftoken=([^;]+)/); return m ? decodeURIComponent(m[1]) : ""; }

  async function refreshSerial(){
    const url = (dir.value === "debtor") ? API.BILL_NEXT : API.RET_NEXT;
    try{
      const res = await fetch(url, { headers: { "Accept":"application/json" }});
      const data = await res.json();
      serial.value = (data.ok && data.next_serial) ? data.next_serial : "";
    }catch{
      serial.value = "";
    }
  }

  // === Provider AC ===
  let acItems = [], acActive = -1; let acTimer = 0;
  function clearAC(){ acList.classList.remove("show"); acList.innerHTML = ""; acItems = []; acActive = -1; }
  function renderAC(items){
    acList.innerHTML = items.map((it,i) => `<li data-id="${it.id}" data-name="${escapeHtml(it.name)}" class="${i===0?"active":""}">${escapeHtml(it.name)}</li>`).join("");
    acItems = Array.from(acList.querySelectorAll("li"));
    acActive = acItems.length ? 0 : -1;
    acList.classList.toggle("show", acItems.length>0);
  }
  function escapeHtml(s){ return String(s||"").replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }

  async function acFetch(q){
    if (!q.trim()){ clearAC(); return; }
    const url = `${API.PROV_AC}?q=${encodeURIComponent(q)}`;
    try{
      const res = await fetch(url, { headers: { "Accept":"application/json" }});
      const data = await res.json();
      if (data?.ok && Array.isArray(data.items)) renderAC(data.items);
      else clearAC();
    }catch{ clearAC(); }
  }

  partyName.addEventListener("input", () => {
    partyId.value = "";
    clearAC();
    clearTimeout(acTimer);
    acTimer = setTimeout(() => acFetch(partyName.value||""), 200);
  });

  partyName.addEventListener("keydown", (e) => {
    if (!acItems.length) return;
    if (e.key === "Tab" || e.key === "ArrowDown"){ e.preventDefault(); acActive = Math.min(acActive+1, acItems.length-1); }
    if (e.key === "ArrowUp"){ e.preventDefault(); acActive = Math.max(acActive-1, 0); }
    if (e.key === "Enter"){
      e.preventDefault();
      if (acActive >= 0) pick(acItems[acActive]);
    }
    acItems.forEach((li,i) => li.classList.toggle("active", i===acActive));
  });

  acList.addEventListener("click", (e) => {
    const li = e.target.closest("li[data-id]");
    if (li) pick(li);
  });

  function pick(li){
    const id = li.getAttribute("data-id");
    const nm = li.getAttribute("data-name") || "";
    partyId.value = id; partyName.value = nm;
    clearAC();
  }

  function validate(){
    saveErr.textContent = "";
    partyErr.textContent = "";
    if (!partyId.value){
      partyErr.textContent = "يجب اختيار اسم صحيح من القائمة.";
      return false;
    }
    const a = Number(amount.value || "0");
    if (!(a > 0)){
      saveErr.textContent = "أدخل مبلغاً موجباً.";
      return false;
    }
    const initPay = Number(initialPayment.value || "0");
    if (initPay < 0){
      saveErr.textContent = "Initial payment must be >= 0.";
      return false;
    }
    if (initPay > a){
      saveErr.textContent = "Initial payment cannot exceed total amount.";
      return false;
    }
    if (initPay > 0 && !moneyContainer.value){
      saveErr.textContent = "Select a money container for the initial payment.";
      return false;
    }
    return true;
  }

  saveBtn.addEventListener("click", async () => {
    if (!validate()) return;

    const payload = {
      direction: dir.value,           // debtor | creditor
      party_type: ptype.value,        // provider (only now)
      provider_id: Number(partyId.value),
      party_name: String(partyName.value||""),
      amount: String(amount.value||"0"),
      currency_code: String(currency.value || "SYP"),
      initial_payment: String(initialPayment.value || "0"),
      money_container_id: moneyContainer.value ? Number(moneyContainer.value) : null,
      due_date: due.value || null,
    };

    try{
      const res = await fetch(API.SAVE, {
        method: "POST",
        headers: {
          "Content-Type":"application/json",
          "X-CSRFToken": getCsrf(),
          "Accept":"application/json",
        },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
    if (data.ok){
        const debtsUrl = document.getElementById("debtsUrl")?.value || "/manager/debts/";
        location.href = debtsUrl;
      }else{
        saveErr.textContent = data.error || "فشل الحفظ.";
      }
    }catch(e){
      saveErr.textContent = "فشل الاتصال بالخادم.";
    }
  });

  dir.addEventListener("change", refreshSerial);
  refreshSerial();
})();
