// static/stock/stock_list.js
(function () {
  const searchInput = document.getElementById("stockSearch");
  const rows = Array.from(
    document.querySelectorAll("table.stock-table tr.row-product")
  );
  if (!searchInput || rows.length === 0) return;

  const modeRadios = Array.from(
    document.querySelectorAll('input[name="searchMode"]')
  );
  const qtyRadios = Array.from(
    document.querySelectorAll('input[name="qtyFilter"]')
  );

  function getSearchMode() {
    const r = modeRadios.find((x) => x.checked);
    return r ? r.value : "name";
  }

  function getQtyFilter() {
    const r = qtyRadios.find((x) => x.checked);
    return r ? r.value : "all";
  }

  function matchesQtyFilter(qty, filter) {
    if (isNaN(qty)) qty = 0;
    switch (filter) {
      case "negative":
        return qty < 0;
      case "zero":
        return qty === 0;
      case "positive":
        return qty > 0;
      default:
        return true;
    }
  }

  function matchesSearch(row, q, mode) {
    if (!q) return true;

    const name = (row.dataset.name || "").toLowerCase();
    const code = (row.dataset.code || "").toLowerCase();
    const id = (row.dataset.id || "").toString();
    const barcode = (row.dataset.barcode || "").toLowerCase();
    const coll = (row.dataset.collection || "").toLowerCase();
    const set = (row.dataset.set || "").toLowerCase();

    switch (mode) {
      case "id":
        return id.includes(q);
      case "code":
        return code.includes(q);
      case "barcode":
        return barcode.includes(q);
      case "name":
      default:
        // match product name, collection name, or father set name
        return (
          name.includes(q) ||
          coll.includes(q) ||
          set.includes(q)
        );
    }
  }

  function refreshHeadersVisibility() {
    const allRows = Array.from(
      document.querySelectorAll("table.stock-table tr")
    );

    let currentCollectionRow = null;
    let currentSetRow = null;
    let anyInCollection = false;
    let anyInSet = false;

    allRows.forEach((row) => {
      if (row.classList.contains("row-collection")) {
        // close previous collection
        if (currentCollectionRow) {
          currentCollectionRow.style.display = anyInCollection ? "" : "none";
        }
        currentCollectionRow = row;
        anyInCollection = false;
        currentSetRow = null;
        anyInSet = false;
      } else if (row.classList.contains("row-set")) {
        // close previous set
        if (currentSetRow) {
          currentSetRow.style.display = anyInSet ? "" : "none";
        }
        currentSetRow = row;
        anyInSet = false;
      } else if (row.classList.contains("row-product")) {
        const visible = row.dataset.visible === "1";
        if (visible) {
          anyInCollection = true;
          if (currentSetRow) anyInSet = true;
        }
      } else if (row.classList.contains("set-separator")) {
        // separator visible only if its set had visible products
        row.style.display = anyInSet ? "" : "none";
      }
    });

    // finalize last open set/collection
    if (currentSetRow) currentSetRow.style.display = anyInSet ? "" : "none";
    if (currentCollectionRow)
      currentCollectionRow.style.display = anyInCollection ? "" : "none";
  }

  function applyFilters() {
    const q = (searchInput.value || "").trim().toLowerCase();
    const mode = getSearchMode();
    const qtyFilter = getQtyFilter();

    rows.forEach((row) => {
      const qty = parseFloat(row.dataset.qty || "0");

      const okQty = matchesQtyFilter(qty, qtyFilter);
      const okSearch = matchesSearch(row, q, mode);

      const visible = okQty && okSearch;
      row.dataset.visible = visible ? "1" : "0";
      row.style.display = visible ? "" : "none";
    });

    refreshHeadersVisibility();
  }

  searchInput.addEventListener("input", applyFilters);
  modeRadios.forEach((r) => r.addEventListener("change", applyFilters));
  qtyRadios.forEach((r) => r.addEventListener("change", applyFilters));

  // initial pass
  applyFilters();
})();
