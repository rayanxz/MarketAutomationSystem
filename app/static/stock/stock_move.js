// static/stock/stock_move.js
(function () {
  const root = document.getElementById("stockMoveRoot");
  if (!root) return;

  const searchUrl = root.dataset.urlSearch;
  const stockUrl = root.dataset.urlStock;

  const fromSel = document.getElementById("from_container");
  const toSel = document.getElementById("to_container");
  const searchInput = document.getElementById("productSearch");
  const acUl = document.getElementById("productAC");
  const rowsBody = document.getElementById("mvRowsBody");
  const emptyRow = document.getElementById("mvEmptyRow");
  const globalWarn = document.getElementById("mvGlobalWarn");

  let searchTimer = null;

  function escapeHtml(str) {
    return (str || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function currentMode() {
    const r = document.querySelector("input[name='searchMode']:checked");
    return r ? r.value : "name";
  }

  function clearAC() {
    acUl.innerHTML = "";
    acUl.hidden = true;
  }

  function clearRowsOnContainerChange() {
    // if user changes containers, we drop rows to avoid wrong preview
    while (rowsBody.firstChild) {
      rowsBody.removeChild(rowsBody.firstChild);
    }
    if (emptyRow) {
      rowsBody.appendChild(emptyRow);
    }
    updateGlobalWarning();
  }

  if (fromSel) {
    fromSel.addEventListener("change", clearRowsOnContainerChange);
  }
  if (toSel) {
    toSel.addEventListener("change", clearRowsOnContainerChange);
  }

  function renderAC(results) {
    acUl.innerHTML = "";
    if (!results || !results.length) {
      acUl.hidden = true;
      return;
    }
    results.forEach((item) => {
      const li = document.createElement("li");
      li.innerHTML =
        '<span class="ac-name"></span>' +
        '<span class="ac-code"></span>' +
        '<span class="ac-path"></span>';

      li.querySelector(".ac-name").textContent = item.name || "";
      li.querySelector(".ac-code").textContent = item.code
        ? "(" + item.code + ")"
        : "";
      li.querySelector(".ac-path").textContent = item.path || "";

      li.addEventListener("click", () => {
        onPickProduct(item);
      });

      acUl.appendChild(li);
    });
    acUl.hidden = false;
  }

  function doSearch(q) {
    if (!searchUrl) return;
    const mode = currentMode();
    fetch(
      searchUrl +
        "?q=" +
        encodeURIComponent(q) +
        "&mode=" +
        encodeURIComponent(mode),
      {
        headers: { Accept: "application/json" },
      }
    )
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((data) => {
        renderAC(data.results || []);
      })
      .catch(() => {
        clearAC();
      });
  }

  if (searchInput) {
    searchInput.addEventListener("input", () => {
      const q = searchInput.value.trim();
      if (searchTimer) clearTimeout(searchTimer);

      if (!q) {
        clearAC();
        return;
      }

      searchTimer = setTimeout(() => doSearch(q), 250);
    });
  }

  function onPickProduct(item) {
    clearAC();
    if (searchInput) searchInput.value = "";

    const fromCode = fromSel ? fromSel.value : "";
    const toCode = toSel ? toSel.value : "";

    if (!fromCode || !toCode) {
      alert("اختر الحاوية المصدر والحاوية الهدف أولاً.");
      return;
    }
    if (!stockUrl) return;

    fetch(stockUrl + "?product_id=" + encodeURIComponent(item.id), {
      headers: { Accept: "application/json" },
    })
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((data) => {
        if (!data.ok) return;
        let fromQty = "0";
        let toQty = "0";
        (data.containers || []).forEach((c) => {
          if (c.code === fromCode) fromQty = c.qty;
          if (c.code === toCode) toQty = c.qty;
        });
        addRow(item, fromQty, toQty);
      })
      .catch(() => {});
  }

  function addRow(product, fromQty, toQty) {
    if (emptyRow && emptyRow.parentNode === rowsBody) {
      emptyRow.remove();
    }

    const tr = document.createElement("tr");
    tr.className = "mv-row";
    tr.dataset.productId = String(product.id);
    tr.dataset.fromQty = String(fromQty || "0");
    tr.dataset.toQty = String(toQty || "0");

    const idx = rowsBody.querySelectorAll("tr.mv-row").length + 1;

    tr.innerHTML =
      '<td class="col-idx"></td>' +
      '<td class="col-name"></td>' +
      '<td class="num col-from"></td>' +
      '<td class="num col-to"></td>' +
      '<td class="col-move"></td>' +
      '<td class="num col-after-from">—</td>' +
      '<td class="num col-after-to">—</td>' +
      '<td class="col-warning"><span class="mv-warn-text"></span></td>' +
      '<td class="col-actions"></td>';

    tr.querySelector(".col-idx").textContent = idx;

    const nameCell = tr.querySelector(".col-name");
    nameCell.innerHTML =
      "<strong>" + escapeHtml(product.name || "") + "</strong>" +
      (product.code
        ? ' <span class="muted">(' + escapeHtml(product.code) + ")</span>"
        : "");

    tr.querySelector(".col-from").textContent = fromQty || "0";
    tr.querySelector(".col-to").textContent = toQty || "0";

    const moveCell = tr.querySelector(".col-move");
    moveCell.innerHTML =
      '<input type="number" name="qty" class="input mv-qty" min="0" step="0.001" value="0">' +
      '<input type="hidden" name="product_id" value="' +
      String(product.id) +
      '">';

    const actionsCell = tr.querySelector(".col-actions");
    const delBtn = document.createElement("button");
    delBtn.type = "button";
    delBtn.className = "btn-mini mv-del";
    delBtn.textContent = "حذف";
    actionsCell.appendChild(delBtn);

    rowsBody.appendChild(tr);

    const qtyInput = tr.querySelector(".mv-qty");
    qtyInput.addEventListener("input", () => recalcRow(tr));
    delBtn.addEventListener("click", () => {
      tr.remove();
      renumberRows();
      if (!rowsBody.querySelector("tr.mv-row") && emptyRow) {
        rowsBody.appendChild(emptyRow);
      }
      updateGlobalWarning();
    });
  }

  function parseDecimal(str) {
    const num = parseFloat(str);
    return isNaN(num) ? 0 : num;
  }

  function recalcRow(tr) {
    const fromQty = parseDecimal(tr.dataset.fromQty || "0");
    const toQty = parseDecimal(tr.dataset.toQty || "0");

    const qtyInput = tr.querySelector(".mv-qty");
    const qtyVal = parseDecimal(qtyInput.value || "0");

    const afterFrom = fromQty - qtyVal;
    const afterTo = toQty + qtyVal;

    const cellAfterFrom = tr.querySelector(".col-after-from");
    const cellAfterTo = tr.querySelector(".col-after-to");
    const warnText = tr.querySelector(".mv-warn-text");

    cellAfterFrom.textContent = afterFrom.toFixed(3).replace(/\.000$/, "");
    cellAfterTo.textContent = afterTo.toFixed(3).replace(/\.000$/, "");

    if (afterFrom < 0) {
      tr.classList.add("mv-row-neg");
      warnText.textContent = "رصيد سالب في المصدر بعد النقل";
    } else {
      tr.classList.remove("mv-row-neg");
      warnText.textContent = "";
    }

    updateGlobalWarning();
  }

  function renumberRows() {
    const rows = rowsBody.querySelectorAll("tr.mv-row");
    rows.forEach((tr, idx) => {
      const cell = tr.querySelector(".col-idx");
      if (cell) cell.textContent = idx + 1;
    });
  }

  function updateGlobalWarning() {
    const hasNeg = !!rowsBody.querySelector("tr.mv-row-neg");
    if (hasNeg) {
      globalWarn && globalWarn.classList.add("show");
    } else {
      globalWarn && globalWarn.classList.remove("show");
    }
  }
})();
