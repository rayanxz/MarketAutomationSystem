// static/audit_log/audit_ui.js
(function () {
  // Toggle details
  document.addEventListener("click", (e) => {
    const btn = e.target.closest(".js-toggle");
    if (!btn) return;

    const card = btn.closest(".audit-ev");
    if (!card) return;

    const details = card.querySelector(".js-details");
    if (!details) return;

    const isHidden = details.hasAttribute("hidden");
    if (isHidden) details.removeAttribute("hidden");
    else details.setAttribute("hidden", "");
  });

  // Copy JSON
  document.addEventListener("click", async (e) => {
    const btn = e.target.closest(".js-copy");
    if (!btn) return;

    const id = btn.getAttribute("data-copy");
    const el = document.getElementById(id);
    if (!el) return;

    const txt = el.textContent || "";
    try {
      await navigator.clipboard.writeText(txt);
      btn.textContent = "تم";
      setTimeout(() => (btn.textContent = "نسخ"), 900);
    } catch (err) {
      btn.textContent = "فشل";
      setTimeout(() => (btn.textContent = "نسخ"), 900);
    }
  });
})();
