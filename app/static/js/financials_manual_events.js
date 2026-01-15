(() => {
  const form = document.querySelector(".js-manual-form");
  if (!form) return;

  const actionSel = form.querySelector(".js-action");
  const msg = form.querySelector(".js-msg");

  const fields = {
    from: form.querySelector(".js-from"),
    to: form.querySelector(".js-to"),
    currency: form.querySelector(".js-currency"),
    currencyFrom: form.querySelector(".js-currency-from"),
    currencyTo: form.querySelector(".js-currency-to"),
    fx: form.querySelector(".js-fx"),
  };

  const setVisible = (el, on) => {
    if (!el) return;
    el.style.display = on ? "" : "none";
  };

  const updateFields = () => {
    const v = actionSel.value;
    setVisible(fields.from, v === "withdraw" || v === "transfer" || v === "exchange");
    setVisible(fields.to, v === "add" || v === "transfer" || v === "exchange");
    setVisible(fields.currency, v !== "exchange");
    setVisible(fields.currencyFrom, v === "exchange");
    setVisible(fields.currencyTo, v === "exchange");
    setVisible(fields.fx, v === "exchange");
  };

  const csrf = form.querySelector("input[name='csrfmiddlewaretoken']");

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    msg.textContent = "";
    const data = new FormData(form);
    try {
      const res = await fetch("/financials/api/manual-event/", {
        method: "POST",
        headers: { "X-CSRFToken": csrf ? csrf.value : "" },
        body: data,
      });
      const json = await res.json();
      if (!json.ok) {
        msg.textContent = json.error || "خطأ غير معروف";
        return;
      }
      window.location.reload();
    } catch (err) {
      msg.textContent = "فشل الاتصال";
    }
  });

  actionSel.addEventListener("change", updateFields);
  updateFields();
})();
