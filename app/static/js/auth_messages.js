// app/static/js/auth_messages.js
(() => {
  "use strict";

  const AUTH_SELECTOR = ".message.auth";
  const DISMISS_MS = 4000;

  function autoDismissAuthMessages() {
    const nodes = document.querySelectorAll(AUTH_SELECTOR);
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
    document.addEventListener("DOMContentLoaded", autoDismissAuthMessages);
  } else {
    autoDismissAuthMessages();
  }
})();
