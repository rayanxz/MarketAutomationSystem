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

  function getCheckboxes() {
    return Array.prototype.slice.call(
      document.querySelectorAll(".return-select")
    );
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
  const suggestBox = document.getElementById("billSearchSuggest");
  const modeRadios = Array.prototype.slice.call(
    document.querySelectorAll("input[name='billSearchMode']")
  );

  const ATTR_MAP = {
    name: null,
    id: "unitid",
    code: "code",
    barcode: "barcode",
  };

  let currentMode = "name";

  function updateMode() {
    modeRadios.forEach(function (r) {
      if (r.checked) currentMode = r.value;
    });
    if (currentMode !== "name" && suggestBox) {
      suggestBox.style.display = "none";
      suggestBox.innerHTML = "";
    }
  }

  modeRadios.forEach(function (r) {
    r.addEventListener("change", updateMode);
  });
  updateMode();

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

  function focusRowByPredicate(pred) {
    const rows = allRows();
    for (let i = 0; i < rows.length; i++) {
      if (pred(rows[i])) {
        return focusRow(rows[i]);
      }
    }
    return false;
  }

  // --- suggestions with keyboard navigation ---
  let suggestItems = [];   // [{el, row, name}]
  let suggestIndex = -1;

  function resetSuggestState() {
    suggestItems = [];
    suggestIndex = -1;
  }

  function applyActive(idx) {
    suggestItems.forEach(function (item, i) {
      if (i === idx) {
        item.el.classList.add("is-active");
      } else {
        item.el.classList.remove("is-active");
      }
    });
    suggestIndex = idx;
  }

  function hideSuggestions() {
    if (!suggestBox) return;
    suggestBox.style.display = "none";
    suggestBox.innerHTML = "";
    resetSuggestState();
  }

  function selectSuggestion(idx) {
    if (idx < 0 || idx >= suggestItems.length) return;
    const item = suggestItems[idx];
    if (!item) return;
    if (searchInput) searchInput.value = item.name;
    hideSuggestions();
    focusRow(item.row);
  }

  function buildSuggestions(q) {
    if (!suggestBox) return;
    const term = (q || "").trim();
    suggestBox.innerHTML = "";
    resetSuggestState();

    if (!term) {
      suggestBox.style.display = "none";
      return;
    }

    const lower = term.toLowerCase();
    const rows = allRows();

    rows.forEach(function (row) {
      const cell = row.querySelector("td.pname");
      if (!cell) return;
      const name = (cell.textContent || "").trim();
      if (!name) return;
      if (name.toLowerCase().indexOf(lower) !== -1) {
        const div = document.createElement("div");
        div.className = "search-suggest-item";
        div.textContent = name;
        const idx = suggestItems.length;
        div.addEventListener("click", function () {
          selectSuggestion(idx);
        });
        suggestBox.appendChild(div);
        suggestItems.push({ el: div, row: row, name: name });
      }
    });

    if (!suggestItems.length) {
      suggestBox.style.display = "none";
      return;
    }

    suggestBox.style.display = "block";
    applyActive(-1); // nothing focused yet
  }

  function runSearch() {
    if (!searchInput) return;
    const term = (searchInput.value || "").trim();
    if (!term) return;

    if (currentMode === "name") {
      const lower = term.toLowerCase();
      const rows = allRows();
      let exactRow = null;
      rows.forEach(function (row) {
        if (exactRow) return;
        const cell = row.querySelector("td.pname");
        if (!cell) return;
        const name = (cell.textContent || "").trim();
        if (name.toLowerCase() === lower) {
          exactRow = row;
        }
      });
      if (exactRow) {
        hideSuggestions();
        focusRow(exactRow);
      } else {
        buildSuggestions(term);
      }
    } else {
      const attr = ATTR_MAP[currentMode];
      if (!attr) return;
      const lower = term.toLowerCase();
      focusRowByPredicate(function (row) {
        const v = (row.dataset[attr] || "").toString().toLowerCase();
        return v && v.indexOf(lower) !== -1;
      });
    }
  }

  if (searchInput) {
    searchInput.addEventListener("input", function () {
      if (currentMode === "name") {
        buildSuggestions(searchInput.value || "");
      } else {
        hideSuggestions();
      }
    });

    searchInput.addEventListener("keydown", function (e) {
      if (!suggestBox || suggestBox.style.display === "none" || !suggestItems.length) {
        if (e.key === "Enter") {
          e.preventDefault();
          runSearch();
        }
        return;
      }

      if (e.key === "ArrowDown") {
        e.preventDefault();
        const next = (suggestIndex + 1) % suggestItems.length;
        applyActive(next);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        const next =
          suggestIndex <= 0 ? suggestItems.length - 1 : suggestIndex - 1;
        applyActive(next);
      } else if (e.key === "Enter") {
        e.preventDefault();
        if (suggestIndex >= 0) {
          selectSuggestion(suggestIndex);
        } else {
          runSearch();
        }
      }
    });
  }
})();
