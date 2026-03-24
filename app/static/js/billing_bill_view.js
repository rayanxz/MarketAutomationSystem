// static/js/billing_bill_view.js
(function () {
  "use strict";

  const root = document.getElementById("billView");
  if (!root || !window.__BILLING__) return;

  const wizardUrl = window.__BILLING__.wizardUrl;
  const selectedItemsRaw = (window.__BILLING__.selectedItems || "").trim();

  const btnStart = document.getElementById("btnStartReturn");
  const btnProceed = document.getElementById("btnProceedReturn");
  const btnCancel = document.getElementById("btnCancelReturnSelection");
  const controls = document.getElementById("returnSelectControls");

  const showReturnButtons = Array.prototype.slice.call(
    document.querySelectorAll(".btn-show-return")
  );
  const stockToggleBtns = Array.prototype.slice.call(
    document.querySelectorAll("[data-stock-toggle]")
  );
  const STOCK_EXPANDED_CLASS = "stock-breakdown-expanded";

  function setStockBreakdownExpanded(expanded) {
    const isExpanded = !!expanded;
    root.classList.toggle(STOCK_EXPANDED_CLASS, isExpanded);
    if (!stockToggleBtns.length) return;
    const label = isExpanded ? "إخفاء تفاصيل المتبقي" : "إظهار تفاصيل المتبقي";
    stockToggleBtns.forEach(function (btn) {
      btn.textContent = isExpanded ? "-" : "+";
      btn.setAttribute("aria-expanded", isExpanded ? "true" : "false");
      btn.setAttribute("aria-label", label);
      btn.setAttribute("title", label);
    });
  }

  function getCheckboxes() {
    return Array.prototype.slice.call(
      document.querySelectorAll(".return-select")
    );
  }

  if (stockToggleBtns.length) {
    setStockBreakdownExpanded(false); // default compact mode
    stockToggleBtns.forEach(function (btn) {
      btn.addEventListener("click", function () {
        const next = !root.classList.contains(STOCK_EXPANDED_CLASS);
        setStockBreakdownExpanded(next);
      });
    });
  }

  /* ================== SELECT MODE (FOR WIZARD) ================== */

    function enterSelectMode() {
    root.classList.add("bill-select-active");
    if (controls) controls.style.display = "flex";

    // 🔒 disable "show returns" buttons while selecting items
    showReturnButtons.forEach(function (btn) {
      btn.classList.add("disabled");
      btn.disabled = true;                 // for the click handler + semantics
      btn.setAttribute("aria-disabled", "true");
    });
  }

  function exitSelectMode(clearChecks) {
    root.classList.remove("bill-select-active");
    if (controls) controls.style.display = "none";

    if (clearChecks) {
      getCheckboxes().forEach(function (cb) {
        cb.checked = false;
      });
    }

    // 🔓 re-enable "show returns" buttons when leaving selection mode
    showReturnButtons.forEach(function (btn) {
      btn.classList.remove("disabled");
      btn.disabled = false;
      btn.removeAttribute("aria-disabled");
    });
  }


  function proceed() {
    const ids = getCheckboxes()
      .filter(function (cb) { return cb.checked; })
      .map(function (cb) { return cb.value; });

    if (!ids.length) {
      alert("اختر منتجاً واحداً على الأقل للمرتجع.");
      return;
    }

    const params = new URLSearchParams();
    params.set("items", ids.join(","));
    window.location.href = wizardUrl + "?" + params.toString();
  }

  // wire buttons
  if (btnStart) {
    btnStart.addEventListener("click", function () {
      enterSelectMode();
    });
  }
  if (btnCancel) {
    btnCancel.addEventListener("click", function () {
      exitSelectMode(true);
    });
  }
  if (btnProceed) {
    btnProceed.addEventListener("click", proceed);
  }

  // auto-enter selection mode if server gave preselected items
  if (selectedItemsRaw) {
    const pre = selectedItemsRaw.split(",").map(function (s) { return s.trim(); });
    getCheckboxes().forEach(function (cb) {
      if (pre.indexOf(cb.value) !== -1) cb.checked = true;
    });
    if (pre.length) {
      enterSelectMode();
    }
  } else {
    exitSelectMode(false);
  }

  // "عرض المرتجعات" buttons → navigate same window
  if (showReturnButtons.length > 0) {
    showReturnButtons.forEach(function (btn) {
      btn.addEventListener("click", function () {
        if (btn.disabled) return;
        const url = btn.getAttribute("data-returns-url");
        if (url) {
          window.location.href = url;
        }
      });
    });
  }

  // ====== GUARD: don't allow selecting rows with zero left ======
  document.addEventListener("change", function (e) {
    const t = e.target;
    if (!t.classList || !t.classList.contains("return-select")) return;

    const tr = t.closest("tr");
    if (!tr) return;

    const leftStr = tr.getAttribute("data-left") || "0";
    const left = parseFloat(String(leftStr).replace(",", ".")) || 0;

    if (left <= 0) {
      t.checked = false;
      alert("لا يمكن تحديد منتج لا توجد منه أي كمية متبقية.");
    }
  });

  /* ================== SEARCH INSIDE BILL ================== */

  const searchInput = document.getElementById("billSearchInput");
  const searchBtn = document.getElementById("btnBillSearch");
  const suggestBox = document.getElementById("billSearchSuggest");
  const modeRadios = Array.prototype.slice.call(
    document.querySelectorAll("input[name='billSearchMode']")
  );

  const debounce = function (fn, ms) {
    let t;
    return function () {
      const args = arguments;
      clearTimeout(t);
      t = setTimeout(function () { fn.apply(null, args); }, ms);
    };
  };

  function clearHighlight() {
    Array.prototype.slice.call(
      document.querySelectorAll(".cell-highlight")
    ).forEach(function (el) {
      el.classList.remove("cell-highlight");
    });
  }

  function allRows() {
    return Array.prototype.slice.call(
      document.querySelectorAll("table.bill tbody tr")
    );
  }

  function rowName(row) {
    const cell = row.querySelector("td.pname");
    const fallback = row.cells && row.cells[0] ? row.cells[0].textContent : "";
    return (cell ? cell.textContent : fallback || "").trim();
  }

  function splitTokens(raw) {
    return String(raw || "")
      .split("||")
      .map(function (x) { return String(x || "").trim(); })
      .filter(function (x) { return !!x; });
  }

  function rowProductId(row) {
    return String(row.dataset.productId || "").trim();
  }

  function rowUnitCodes(row) {
    return splitTokens(row.dataset.unitcodes);
  }

  function rowBarcodes(row) {
    return splitTokens(row.dataset.barcodes);
  }

  function rowMatchesMode(row, mode, lowerTerm) {
    if (mode === "name") {
      return rowName(row).toLowerCase().indexOf(lowerTerm) !== -1;
    }
    if (mode === "id") {
      const pid = rowProductId(row).toLowerCase();
      return !!pid && pid === lowerTerm;
    }
    if (mode === "code") {
      return rowUnitCodes(row).some(function (v) {
        return v.toLowerCase() === lowerTerm;
      });
    }
    if (mode === "barcode") {
      return rowBarcodes(row).some(function (v) {
        return v.toLowerCase() === lowerTerm;
      });
    }
    return false;
  }

  function rowDisplayValue(row, mode, lowerTerm) {
    if (mode === "id") return rowProductId(row);
    if (mode === "code") {
      const codes = rowUnitCodes(row);
      const matched = codes.find(function (v) {
        return v.toLowerCase() === lowerTerm;
      });
      return matched || codes[0] || "";
    }
    if (mode === "barcode") {
      const codes = rowBarcodes(row);
      const matched = codes.find(function (v) {
        return v.toLowerCase() === lowerTerm;
      });
      return matched || codes[0] || "";
    }
    return rowName(row);
  }

  function focusRow(row) {
    if (!row) return false;
    clearHighlight();
    const cell = row.querySelector("td.pname") || row.cells[0];
    if (cell) {
      cell.classList.add("cell-highlight");
    }
    row.scrollIntoView({ behavior: "smooth", block: "center" });
    return true;
  }

  let mode = "name";
  modeRadios.forEach(function (r) {
    if (r.checked) mode = r.value;
    r.addEventListener("change", function () {
      mode = r.value;
      clearSug();
      if (searchInput) searchInput.focus();
    });
  });

  let lastItems = [];
  let activeIndex = -1;

  function clearSug() {
    if (!suggestBox) return;
    suggestBox.style.display = "none";
    suggestBox.innerHTML = "";
    lastItems = [];
    activeIndex = -1;
  }

  function setActive(i) {
    const nodes = Array.prototype.slice.call(
      suggestBox ? suggestBox.querySelectorAll(".search-suggest-item") : []
    );
    if (!nodes.length) {
      activeIndex = -1;
      return;
    }
    activeIndex = ((i % nodes.length) + nodes.length) % nodes.length;
    nodes.forEach(function (node, idx) {
      node.classList.toggle("is-active", idx === activeIndex);
    });
  }

  function pick(item) {
    if (!item) return;
    if (searchInput) {
      if (mode === "name") {
        searchInput.value = item.name;
      } else if (item.display) {
        searchInput.value = item.display;
      }
    }
    clearSug();
    focusRow(item.row);
  }

  async function searchInBill(q, modeValue) {
    const term = String(q || "").trim();
    if (!term) return [];
    const lower = term.toLowerCase();
    const seen = new Set();
    const out = [];

    allRows().forEach(function (row) {
      if (!rowMatchesMode(row, modeValue, lower)) return;
      const pid = rowProductId(row);
      const dedupeKey = pid || ("item:" + (row.dataset.itemId || ""));
      if (seen.has(dedupeKey)) return;
      seen.add(dedupeKey);

      const name = rowName(row);
      if (!name) return;
      out.push({
        row: row,
        name: name,
        display: rowDisplayValue(row, modeValue, lower),
      });
    });

    return out;
  }

  function renderSug(items) {
    if (!suggestBox) return;
    if (!items.length) {
      clearSug();
      return;
    }

    suggestBox.innerHTML = "";
    items.slice(0, 8).forEach(function (it, i) {
      const node = document.createElement("div");
      node.className = "search-suggest-item";
      node.textContent = it.name;
      node.addEventListener("mouseenter", function () { setActive(i); });
      node.addEventListener("mousedown", function (e) {
        e.preventDefault();
        const picked = lastItems[i];
        if (picked) pick(picked);
      });
      suggestBox.appendChild(node);
    });

    suggestBox.style.display = "block";
    setActive(0);
  }

  const doSearch = async function () {
    const val = (searchInput ? searchInput.value : "").trim();
    if (!val) {
      clearSug();
      return;
    }
    try {
      const items = await searchInBill(val, mode);
      lastItems = items;
      renderSug(lastItems);
    } catch (_e) {
      clearSug();
    }
  };
  const onType = debounce(doSearch, 180);

  if (searchInput) {
    searchInput.addEventListener("input", onType);
    searchInput.addEventListener("focus", function () {
      const val = (searchInput.value || "").trim();
      if (val) onType();
      else clearSug();
    });
    searchInput.addEventListener("blur", function () {
      setTimeout(clearSug, 120);
    });
    searchInput.addEventListener("keydown", async function (e) {
      if (e.key === "Escape") {
        clearSug();
        return;
      }

      const hasList = !!(
        suggestBox &&
        suggestBox.style.display !== "none" &&
        suggestBox.querySelectorAll(".search-suggest-item").length
      );

      if (hasList && (e.key === "Tab" || e.key === "ArrowDown" || e.key === "ArrowUp")) {
        e.preventDefault();
        const delta = (e.key === "ArrowDown" || (!e.shiftKey && e.key === "Tab")) ? 1 : -1;
        setActive(activeIndex + delta);
        return;
      }

      if (e.key === "Enter") {
        e.preventDefault();
        const val = (searchInput.value || "").trim();
        if (!val) {
          clearSug();
          return;
        }
        if (hasList && activeIndex >= 0) {
          const it = lastItems[activeIndex];
          if (it) {
            pick(it);
            return;
          }
        }
        const items = await searchInBill(val, mode);
        if (items.length) pick(items[0]);
        else clearSug();
      }
    });
  }

  if (searchBtn) {
    searchBtn.addEventListener("click", async function () {
      const hasList = !!(
        suggestBox &&
        suggestBox.style.display !== "none" &&
        suggestBox.querySelectorAll(".search-suggest-item").length
      );
      if (hasList) {
        const idx = activeIndex >= 0 ? activeIndex : 0;
        const it = lastItems[idx];
        if (it) {
          pick(it);
          if (searchInput) searchInput.focus();
          return;
        }
      }

      const val = (searchInput ? searchInput.value : "").trim();
      if (!val) {
        clearSug();
        if (searchInput) searchInput.focus();
        return;
      }

      const items = await searchInBill(val, mode);
      if (items.length) pick(items[0]);
      else clearSug();
      if (searchInput) searchInput.focus();
    });
  }

  if (modeRadios.length) {
    const checked = modeRadios.find(function (r) { return r.checked; });
    if (checked) {
      mode = checked.value;
    }
  }
})();
