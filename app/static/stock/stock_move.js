// static/stock/stock_move.js
(function () {
  const root = document.getElementById("stockMoveRoot");
  if (!root) return;

  const urlSearch = root.dataset.urlSearch;
  const urlStock = root.dataset.urlStock;
  const urlBatches = root.dataset.urlBatches;

  const fromSel = document.getElementById("from_container");
  const toSel = document.getElementById("to_container");
  const searchInput = document.getElementById("productSearch");
  const searchModeRadios = root.querySelectorAll("input[name='searchMode']");
  const acList = document.getElementById("productAC");

  const productEmpty = document.getElementById("mvProductEmpty");
  const productContent = document.getElementById("mvProductContent");
  const productSummary = document.getElementById("mvProductSummary");
  const batchBody = document.getElementById("mvBatchBody");
  const qtyInput = document.getElementById("mvQtyInput");
  const rowNoteInput = document.getElementById("mvRowNote");
  const addRowBtn = document.getElementById("mvAddRowBtn");

  const tbody = document.getElementById("mvRowsBody");
  const emptyRow = document.getElementById("mvEmptyRow");
  const globalWarn = document.getElementById("mvGlobalWarn");

  let acTimer = null;
  let acResults = [];
  let rowCounter = 0;

  // current selected product + batches in the source container
  let currentProduct = null;
  let currentBatches = [];

  function currentSearchMode() {
    for (const r of searchModeRadios) {
      if (r.checked) return r.value;
    }
    return "name";
  }

  function clearAC() {
    acResults = [];
    acList.innerHTML = "";
    acList.hidden = true;
  }

  function renderAC(results) {
    acResults = results || [];
    if (!acResults.length) {
      clearAC();
      return;
    }
    acList.innerHTML = "";
    acList.hidden = false;
    acResults.forEach((item, idx) => {
      const li = document.createElement("li");
      li.dataset.index = String(idx);

      const nameEl = document.createElement("span");
      nameEl.className = "ac-name";
      nameEl.textContent = item.name || "";

      const codeEl = document.createElement("span");
      codeEl.className = "ac-code";
      codeEl.textContent = item.code || "";

      const pathEl = document.createElement("span");
      pathEl.className = "ac-path";
      pathEl.textContent = item.path || "";

      li.appendChild(nameEl);
      li.appendChild(codeEl);
      li.appendChild(pathEl);

      // use mousedown so blur on input doesn't kill the click
      li.addEventListener("mousedown", (ev) => {
        ev.preventDefault();
        onSelectProduct(item);
      });

      acList.appendChild(li);
    });
  }

  function fetchSearch(term) {
    if (!term) {
      clearAC();
      return;
    }
    const mode = currentSearchMode();
    const params = new URLSearchParams({ q: term, mode });
    fetch(urlSearch + "?" + params.toString(), {
      headers: { "X-Requested-With": "XMLHttpRequest" },
    })
      .then((r) => r.json())
      .then((data) => {
        renderAC((data && data.results) || []);
      })
      .catch(() => {
        clearAC();
      });
  }

  if (searchInput) {
    searchInput.addEventListener("input", () => {
      const term = searchInput.value.trim();
      if (acTimer) window.clearTimeout(acTimer);
      acTimer = window.setTimeout(() => fetchSearch(term), 200);
    });

    searchInput.addEventListener("blur", () => {
      setTimeout(() => clearAC(), 150);
    });
  }

  function ensureContainersChosen(showAlert = true) {
    const fromVal = (fromSel && fromSel.value) || "";
    const toVal = (toSel && toSel.value) || "";
    if (!fromVal || !toVal) {
      if (showAlert) {
        alert("يجب اختيار الحاوية المصدر والحاوية الهدف أولاً.");
      }
      return false;
    }
    if (fromVal === toVal) {
      if (showAlert) {
        alert("لا يمكن أن تكون الحاوية المصدر هي نفسها الحاوية الهدف.");
      }
      return false;
    }
    return true;
  }

  function onSelectProduct(prod) {
    clearAC();
    if (!ensureContainersChosen()) return;

    const fromCode = fromSel.value;
    if (!fromCode) return;

    const params = new URLSearchParams({
      product_id: String(prod.id),
      container: fromCode,
    });

    fetch(urlBatches + "?" + params.toString(), {
      headers: { "X-Requested-With": "XMLHttpRequest" },
    })
      .then((r) => r.json())
      .then((data) => {
        if (!data.ok) {
          alert(data.error || "تعذر جلب الدفعات من المخزون.");
          return;
        }

        const batches = data.batches || [];
        if (!batches.length) {
          alert("لا توجد أي دفعات لهذه المادة في الحاوية المصدر.");
          return;
        }

        // API is already ordered by created_at ASC (أقدم في الأعلى)
        showCurrentProduct(prod, data.container, batches);
      })
      .catch(() => {
        alert("حدث خطأ أثناء جلب الدفعات.");
      });
  }

  function showCurrentProduct(prod, srcContainer, batches) {
    currentProduct = {
      id: prod.id,
      name: prod.name || "",
      code: prod.code || "",
      path: prod.path || "",
      fromLabel: srcContainer && srcContainer.name ? srcContainer.name : "",
    };
    currentBatches = batches || [];

    // summary
    if (productSummary) {
      const nameSpan = `<span class="mv-product-name">${currentProduct.name}</span>`;
      const codeSpan = currentProduct.code
        ? `<span class="mv-product-code">(${currentProduct.code})</span>`
        : "";
      const pathSpan = currentProduct.path
        ? `<span class="mv-product-path">${currentProduct.path}</span>`
        : "";
      const contSpan = currentProduct.fromLabel
        ? `<span class="mv-product-cont">من: ${currentProduct.fromLabel}</span>`
        : "";

      productSummary.innerHTML =
        nameSpan + " " + codeSpan + " " + pathSpan + contSpan;
    }

    // batches table
    if (batchBody) {
      batchBody.innerHTML = "";
      currentBatches.forEach((b, idx) => {
        const tr = document.createElement("tr");

        const tdRadio = document.createElement("td");
        const radio = document.createElement("input");
        radio.type = "radio";
        radio.name = "currentBatch";
        radio.value = String(b.id);
        tdRadio.appendChild(radio);

        const tdIndex = document.createElement("td");
        tdIndex.textContent = String(idx + 1);

        const tdDate = document.createElement("td");
        let dt = "";
        if (b.created_at) {
          dt = String(b.created_at).slice(0, 16).replace("T", " ");
        }
        tdDate.textContent = dt || "—";

        const tdQty = document.createElement("td");
        tdQty.className = "num";
        tdQty.textContent = b.qty || "0";

        const tdCost = document.createElement("td");
        tdCost.className = "num";
        tdCost.textContent = b.unit_cost || "0";

        const tdSource = document.createElement("td");
        tdSource.textContent = b.source || "";

        tr.appendChild(tdRadio);
        tr.appendChild(tdIndex);
        tr.appendChild(tdDate);
        tr.appendChild(tdQty);
        tr.appendChild(tdCost);
        tr.appendChild(tdSource);

        batchBody.appendChild(tr);
      });
    }

    // reset qty + note
    if (qtyInput) qtyInput.value = "";
    if (rowNoteInput) rowNoteInput.value = "";

    // show panel
    if (productEmpty) productEmpty.style.display = "none";
    if (productContent) productContent.hidden = false;
  }

  function hideEmptyRowIfNeeded() {
    if (!emptyRow) return;
    const hasRealRows = tbody.querySelectorAll("tr[data-row-id]").length > 0;
    emptyRow.style.display = hasRealRows ? "none" : "";
  }

  function recomputeGlobalWarn() {
    let hasNeg = false;
    const rows = tbody.querySelectorAll("tr[data-row-id]");
    rows.forEach((tr) => {
      if (tr.classList.contains("mv-row-neg")) hasNeg = true;
    });
    if (globalWarn) {
      globalWarn.classList.toggle("show", hasNeg);
    }
  }

  // Add row from current selection (product + batch + qty + optional note)
  function addRowFromSelection(prod, batch, qty, note) {
    rowCounter += 1;
    const rowId = "row-" + rowCounter;

    const tr = document.createElement("tr");
    tr.dataset.rowId = rowId;

    // --- col # ---
    const tdIndex = document.createElement("td");
    tdIndex.textContent = String(rowCounter);

    // --- col product + batch info ---
    const tdProd = document.createElement("td");
    const prodMain = document.createElement("div");
    prodMain.className = "mv-prod-main";

    const titleRow = document.createElement("div");
    titleRow.className = "mv-prod-title";
    const strongName = document.createElement("strong");
    strongName.textContent = prod.name || "";
    const spanCode = document.createElement("span");
    spanCode.className = "mv-prod-code";
    spanCode.textContent = prod.code ? "(" + prod.code + ")" : "";
    titleRow.appendChild(strongName);
    titleRow.appendChild(spanCode);

    const subRow = document.createElement("div");
    subRow.className = "mv-prod-sub";

    let dt = "";
    if (batch.created_at) {
      dt = String(batch.created_at).slice(0, 16).replace("T", " ");
    }
    const batchTxt =
      "دفعة #" +
      batch.id +
      (dt ? " — " + dt : "") +
      " — متاح: " +
      (batch.qty || "0");

    const spanBatch = document.createElement("span");
    spanBatch.textContent = batchTxt;

    const spanTag = document.createElement("span");
    spanTag.className = "mv-batch-tag";
    spanTag.textContent = "من الحاوية المصدر";

    subRow.appendChild(spanBatch);
    subRow.appendChild(spanTag);

    prodMain.appendChild(titleRow);
    prodMain.appendChild(subRow);

    // hidden inputs for POST
    const inputProdId = document.createElement("input");
    inputProdId.type = "hidden";
    inputProdId.name = "product_id";
    inputProdId.value = String(prod.id);

    const inputBatchId = document.createElement("input");
    inputBatchId.type = "hidden";
    inputBatchId.name = "batch_id";
    inputBatchId.value = String(batch.id);

    tdProd.appendChild(prodMain);
    tdProd.appendChild(inputProdId);
    tdProd.appendChild(inputBatchId);

    // --- col source qty (batch remaining before) ---
    const tdSrcQty = document.createElement("td");
    tdSrcQty.className = "num";
    const srcQtySpan = document.createElement("span");
    srcQtySpan.className = "mv-src-qty";
    const batchQtyNum = parseFloat(batch.qty || "0") || 0;
    srcQtySpan.textContent = batchQtyNum.toFixed(3);
    tdSrcQty.appendChild(srcQtySpan);

    // --- col target qty (current in TO container) ---
    const tdDstQty = document.createElement("td");
    tdDstQty.className = "num";
    const dstQtySpan = document.createElement("span");
    dstQtySpan.className = "mv-dst-qty";
    dstQtySpan.textContent = "—";
    tdDstQty.appendChild(dstQtySpan);

    // fetch current qty in target container (per product, not per batch)
    const toCode = toSel && toSel.value;
    if (toCode && urlStock) {
      const params = new URLSearchParams({ product_id: String(prod.id) });
      fetch(urlStock + "?" + params.toString(), {
        headers: { "X-Requested-With": "XMLHttpRequest" },
      })
        .then((r) => r.json())
        .then((data) => {
          if (!data.ok) return;
          const conts = data.containers || [];
          const item = conts.find((c) => c.code === toCode);
          if (item) dstQtySpan.textContent = item.qty || "0";
        })
        .catch(() => {});
    }

    // --- col qty to move ---
    const tdMoveQty = document.createElement("td");
    const inputQty = document.createElement("input");
    inputQty.type = "number";
    inputQty.name = "qty";
    inputQty.step = "0.001";
    inputQty.min = "0.001";
    inputQty.className = "mv-qty-input numeric-math";
    inputQty.style.width = "90px";
    inputQty.placeholder = "0.000";
    inputQty.value = qty > 0 ? qty.toFixed(3) : "";
    tdMoveQty.appendChild(inputQty);

    // --- col remaining src ---
    const tdSrcAfter = document.createElement("td");
    tdSrcAfter.className = "num";
    const srcAfterSpan = document.createElement("span");
    srcAfterSpan.className = "mv-src-after";
    tdSrcAfter.appendChild(srcAfterSpan);

    // --- col remaining dst ---
    const tdDstAfter = document.createElement("td");
    tdDstAfter.className = "num";
    const dstAfterSpan = document.createElement("span");
    dstAfterSpan.className = "mv-dst-after";
    tdDstAfter.appendChild(dstAfterSpan);

    // --- col row note (optional, for future use) ---
    const tdNote = document.createElement("td");
    tdNote.className = "mv-note-cell";
    const spanNote = document.createElement("span");
    spanNote.textContent = note || "";
    tdNote.appendChild(spanNote);

    // If you ever want per-row note in backend, you can add:
    // const inputRowNote = document.createElement("input");
    // inputRowNote.type = "hidden";
    // inputRowNote.name = "row_note";
    // inputRowNote.value = note || "";
    // tdNote.appendChild(inputRowNote);

    // --- col warn ---
    const tdWarn = document.createElement("td");
    const warnSpan = document.createElement("span");
    warnSpan.className = "mv-warn-text";
    warnSpan.style.fontSize = "11px";
    tdWarn.appendChild(warnSpan);

    // --- col actions ---
    const tdActions = document.createElement("td");
    const btnDel = document.createElement("button");
    btnDel.type = "button";
    btnDel.className = "btn-mini";
    btnDel.textContent = "حذف";
    btnDel.addEventListener("click", () => {
      tr.remove();
      hideEmptyRowIfNeeded();
      recomputeGlobalWarn();
    });
    tdActions.appendChild(btnDel);

    tr.appendChild(tdIndex);
    tr.appendChild(tdProd);
    tr.appendChild(tdSrcQty);
    tr.appendChild(tdDstQty);
    tr.appendChild(tdMoveQty);
    tr.appendChild(tdSrcAfter);
    tr.appendChild(tdDstAfter);
    tr.appendChild(tdNote);
    tr.appendChild(tdWarn);
    tr.appendChild(tdActions);

    tbody.appendChild(tr);
    hideEmptyRowIfNeeded();

    function updateForRow() {
      const batchQty = batchQtyNum;
      const moveQty = parseFloat(inputQty.value || "0") || 0;
      const dstBefore = parseFloat(
        dstQtySpan.textContent === "—"
          ? "0"
          : dstQtySpan.textContent || "0"
      ) || 0;

      srcQtySpan.textContent = batchQty.toFixed(3);

      if (moveQty > 0) {
        const srcAfter = batchQty - moveQty;
        const dstAfter = dstBefore + moveQty;
        srcAfterSpan.textContent = srcAfter.toFixed(3);
        dstAfterSpan.textContent = dstAfter.toFixed(3);

        if (srcAfter < 0) {
          tr.classList.add("mv-row-neg");
          warnSpan.textContent = "الرصيد في المصدر سيصبح سالباً.";
        } else {
          tr.classList.remove("mv-row-neg");
          warnSpan.textContent = "";
        }
      } else {
        srcAfterSpan.textContent = "";
        dstAfterSpan.textContent = "";
        tr.classList.remove("mv-row-neg");
        warnSpan.textContent = "";
      }

      recomputeGlobalWarn();
    }

    inputQty.addEventListener("input", updateForRow);

    // initial compute based on default qty
    updateForRow();
  }

  // Handle "إضافة إلى قائمة النقل"
  if (addRowBtn) {
    addRowBtn.addEventListener("click", () => {
      if (!currentProduct || !currentBatches.length) {
        alert("اختر مادة أولاً من مربع البحث.");
        return;
      }
      if (!ensureContainersChosen()) return;

      const chosen = batchBody
        ? batchBody.querySelector("input[name='currentBatch']:checked")
        : null;
      if (!chosen) {
        alert("يجب اختيار دفعة (باتش) واحدة من القائمة.");
        return;
      }
      const batchId = chosen.value;
      const batch = currentBatches.find((b) => String(b.id) === batchId);
      if (!batch) {
        alert("الدفعة المحددة غير صالحة.");
        return;
      }

      const qtyVal = parseFloat(qtyInput.value || "0") || 0;
      if (qtyVal <= 0) {
        alert("يجب إدخال كمية أكبر من صفر.");
        return;
      }

      const batchQtyNum = parseFloat(batch.qty || "0") || 0;
      if (qtyVal > batchQtyNum) {
        alert("الكمية المراد نقلها أكبر من الكمية المتاحة في هذه الدفعة.");
        return;
      }

      const note = rowNoteInput.value || "";

      addRowFromSelection(currentProduct, batch, qtyVal, note);

      // you can optionally clear qty/note after add
      if (qtyInput) qtyInput.value = "";
      if (rowNoteInput) rowNoteInput.value = "";
    });
  }

  // initial state
  hideEmptyRowIfNeeded();
})();
