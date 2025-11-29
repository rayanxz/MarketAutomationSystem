// static/js/billing_bill_view.js
(() => {
  const billId = window.__BILLING__?.billId;
  if (!billId) return;

  const btnStart = document.getElementById("btnStartReturn");
  const btnSave = document.getElementById("btnSaveReturn");
  const form = document.getElementById("billReturnForm");

  if (!btnStart || !btnSave || !form) return;

  let editMode = false;

  function enableReturnInputs() {
    const qtyInputs = form.querySelectorAll(".input-return-qty");
    const costInputs = form.querySelectorAll(".input-return-cost");

    qtyInputs.forEach((inp) => {
      const tr = inp.closest("tr");
      if (!tr) return;
      const left = parseFloat(tr.dataset.left || "0");
      if (left > 0) {
        inp.disabled = false;
        inp.max = String(left);
      }
    });

    costInputs.forEach((inp) => {
      const tr = inp.closest("tr");
      if (!tr) return;
      const left = parseFloat(tr.dataset.left || "0");
      if (left > 0) {
        inp.disabled = false;
      }
    });

    // Show save button
    btnSave.style.display = "inline-block";
  }

  function clampQty(e) {
    const inp = e.target;
    if (!inp.classList.contains("input-return-qty")) return;

    const tr = inp.closest("tr");
    if (!tr) return;
    const left = parseFloat(tr.dataset.left || "0");
    let val = parseFloat(inp.value || "0");

    if (!isFinite(val) || val < 0) {
      val = 0;
    }
    if (val > left) {
      val = left;
    }
    inp.value = val ? val.toString() : "";
  }

  btnStart.addEventListener("click", () => {
    if (editMode) return;
    editMode = true;
    enableReturnInputs();
  });

  form.addEventListener("input", clampQty);

  // NOTE:
  // Submitting the form with btnSave will hit the Django view for POST on bill_view.
  // There we still need to implement actual ProviderReturn creation
  // (reading return_qty_* / return_cost_* values).
})();
