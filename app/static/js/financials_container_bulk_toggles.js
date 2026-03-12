(function () {
  function getTargets(actionsBox) {
    const selector = actionsBox.getAttribute("data-bulk-target");
    if (!selector) return [];
    return Array.from(document.querySelectorAll(selector)).filter((el) => !el.disabled);
  }

  function setAll(actionsBox, checked) {
    getTargets(actionsBox).forEach((cb) => {
      cb.checked = checked;
    });
  }

  function bindActions(actionsBox) {
    const allowBtn = actionsBox.querySelector(".js-bulk-allow");
    const forbidBtn = actionsBox.querySelector(".js-bulk-forbid");

    if (allowBtn) {
      allowBtn.addEventListener("click", function () {
        setAll(actionsBox, true);
      });
    }
    if (forbidBtn) {
      forbidBtn.addEventListener("click", function () {
        setAll(actionsBox, false);
      });
    }
  }

  function init() {
    document.querySelectorAll(".js-bulk-actions[data-bulk-target]").forEach(bindActions);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
