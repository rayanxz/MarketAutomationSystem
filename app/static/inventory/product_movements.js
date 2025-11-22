(() => {
  "use strict";

  const qs  = (s, r=document) => r.querySelector(s);
  const qsa = (s, r=document) => Array.from(r.querySelectorAll(s));

  const scopeSel   = qs("#mvScope");
  const collSelect = qs("#mvCollection");

  const setId      = qs("#mvSetId");
  const setInput   = qs("#mvSetSearch");
  const setAC      = qs("#mvSetAC");

  const prodId     = qs("#mvProductId");
  const prodInput  = qs("#mvProductSearch");
  const prodAC     = qs("#mvProductAC");

  const resetBtn   = qs("#mvResetBtn");

  const SET_DATA    = window.MV_SET_DATA || [];
  const PRODUCT_API = window.MV_PRODUCT_SEARCH_API || "/api/product/search/name/";

  /* ================= SCOPE HANDLING (unchanged) ================= */

  function inferInitialScope() {
    if (prodId && prodId.value) return "product";
    if (setId && setId.value) return "set";
    if (collSelect && collSelect.value) return "collection";
    return "all";
  }

  function setDisabled(el, flag) {
    if (!el) return;
    if (flag) el.setAttribute("disabled", "disabled");
    else el.removeAttribute("disabled");
  }

  function applyScope() {
    const v = scopeSel ? scopeSel.value : "all";

    // base: everything disabled
    setDisabled(collSelect, true);
    setDisabled(setInput, true);
    setDisabled(prodInput, true);

    if (v === "collection") {
      setDisabled(collSelect, false);
      if (setInput) setInput.value = "";
      if (setId) setId.value = "";
      if (prodInput) prodInput.value = "";
      if (prodId) prodId.value = "";
    } else if (v === "set") {
      setDisabled(setInput, false);
      if (collSelect) collSelect.value = "";
      if (prodInput) prodInput.value = "";
      if (prodId) prodId.value = "";
    } else if (v === "product") {
      setDisabled(prodInput, false);
      if (collSelect) collSelect.value = "";
      if (setInput) setInput.value = "";
      if (setId) setId.value = "";
    } else {
      // all
      if (collSelect) collSelect.value = "";
      if (setInput) setInput.value = "";
      if (setId) setId.value = "";
      if (prodInput) prodInput.value = "";
      if (prodId) prodId.value = "";
    }
  }

  if (scopeSel) {
    scopeSel.value = inferInitialScope();
    applyScope();
    scopeSel.addEventListener("change", applyScope);
  }

  /* ================= GENERIC AC WITH KEYBOARD ================= */

  function initAC({ input, hidden, list, minChars, fetchItems, formatItem, isDisabled }) {
    if (!input || !list) return;

    let timer = null;
    let items = [];
    let activeIndex = -1;

    function clearList() {
      items = [];
      list.innerHTML = "";
      list.hidden = true;
      activeIndex = -1;
    }

    function render() {
      if (!items.length) {
        clearList();
        return;
      }
      list.innerHTML = items
        .map((it, idx) => `<li data-idx="${idx}">${formatItem(it)}</li>`)
        .join("");
      list.hidden = false;
      activeIndex = -1;
    }

    function choose(idx) {
      const it = items[idx];
      if (!it) return;
      if (hidden && typeof it.id !== "undefined") {
        hidden.value = it.id;
      }
      input.value = it.label || it.name || "";
      clearList();
    }

    input.addEventListener("input", () => {
      if (isDisabled && isDisabled()) return;

      const q = input.value.trim();
      if (hidden) hidden.value = "";

      if (!q || (minChars && q.length < minChars)) {
        clearList();
        return;
      }

      if (timer) clearTimeout(timer);
      timer = setTimeout(async () => {
        try {
          items = await fetchItems(q);
          render();
        } catch (_e) {
          clearList();
        }
      }, 180);
    });

    input.addEventListener("keydown", (e) => {
      if (list.hidden || !items.length) return;

      if (e.key === "ArrowDown") {
        e.preventDefault();
        activeIndex = (activeIndex + 1) % items.length;
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        activeIndex = (activeIndex - 1 + items.length) % items.length;
      } else if (e.key === "Enter") {
        if (activeIndex >= 0) {
          e.preventDefault();
          choose(activeIndex);
        }
        return;
      } else if (e.key === "Escape") {
        clearList();
        return;
      } else {
        return;
      }

      const lis = qsa("li", list);
      lis.forEach((li, idx) => {
        if (idx === activeIndex) {
          li.classList.add("active");
          li.scrollIntoView({ block: "nearest" });
        } else {
          li.classList.remove("active");
        }
      });
    });

    list.addEventListener("mousedown", (e) => {
      const li = e.target.closest("li");
      if (!li) return;
      e.preventDefault(); // keep focus on input
      const idx = Number(li.dataset.idx);
      choose(idx);
    });

    document.addEventListener("click", (e) => {
      if (e.target === input) return;
      if (!list.contains(e.target)) {
        list.hidden = true;
      }
    });
  }

  /* ================= SET AUTOCOMPLETE (local) ================= */

  initAC({
    input: setInput,
    hidden: setId,
    list: setAC,
    minChars: 1,
    isDisabled: () => !!setInput && setInput.disabled,
    async fetchItems(q) {
      const ql = q.toLowerCase();
      return SET_DATA
        .filter((s) => {
          const full = (s.collection + " " + s.name).toLowerCase();
          return full.includes(ql);
        })
        .slice(0, 30)
        .map((s) => ({
          id: s.id,
          label: `${s.collection} / ${s.name}`,
        }));
    },
    formatItem(it) {
      return it.label;
    },
  });

  /* ================= PRODUCT AUTOCOMPLETE (API) ================= */

  initAC({
    input: prodInput,
    hidden: prodId,
    list: prodAC,
    minChars: 2,
    isDisabled: () => !!prodInput && prodInput.disabled,
    async fetchItems(q) {
      const url = PRODUCT_API + "?mode=name&q=" + encodeURIComponent(q);
      const r = await fetch(url, { headers: { Accept: "application/json" } });
      const data = await r.json();
      if (!data.ok || !Array.isArray(data.items)) return [];
      return data.items
        .filter((it) => it.type === "product")
        .map((p) => ({
          id: p.id,
          label: `${p.code} - ${p.name}`,
        }));
    },
    formatItem(it) {
      return it.label;
    },
  });

  /* ================= RESET BUTTON (unchanged) ================= */

  if (resetBtn) {
    resetBtn.addEventListener("click", () => {
      // Just reload page without query string
      const base = window.location.pathname;
      window.location.href = base;
    });
  }

  /* ================= EXTRA GLOBAL CLICK (still fine) ================= */

  document.addEventListener("click", (e) => {
    if (setAC && !setAC.contains(e.target) && e.target !== setInput) {
      setAC.hidden = true;
    }
    if (prodAC && !prodAC.contains(e.target) && e.target !== prodInput) {
      prodAC.hidden = true;
    }
  });
})();
