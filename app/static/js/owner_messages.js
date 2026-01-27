// app/static/js/owner_messages.js
(() => {
  "use strict";

  const OWNER_SELECTOR = ".message.owner";
  const DISMISS_MS = 4000;

  function autoDismissOwnerMessages() {
    const nodes = document.querySelectorAll(OWNER_SELECTOR);
    if (!nodes.length) return;
    nodes.forEach((el) => {
      window.setTimeout(() => {
        if (el && el.parentNode) {
          el.parentNode.removeChild(el);
        }
      }, DISMISS_MS);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", autoDismissOwnerMessages);
  } else {
    autoDismissOwnerMessages();
  }
})();
