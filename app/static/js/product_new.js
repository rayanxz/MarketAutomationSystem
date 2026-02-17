// static/js/product_new.js
(function () {
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  // DOM
  const colInput = $("#ac-col");
  const colList  = $("#ac-col-list");
  const setInput = $("#ac-set");
  const setList  = $("#ac-set-list");

  // Selected collection (enables sets AC)
  let selectedCollection = null;
  let committedCollectionName = "";
  let committedSetName = "";
  let liveSetTyping = false;
  const pathEl = document.getElementById("productPath");
  const pathPreviewEnabled = pathEl && pathEl.dataset.mode === "create";
  const createParentInput = document.querySelector('input[name="create_parent"]');

  function emitPathCommit() {
    if (!pathPreviewEnabled) return;
    document.dispatchEvent(new CustomEvent("product:path-commit", {
      detail: {
        collectionName: committedCollectionName || "",
        setName: committedSetName || "",
      }
    }));
  }

  function emitPathLiveSet(name) {
    if (!pathPreviewEnabled) return;
    document.dispatchEvent(new CustomEvent("product:path-commit", {
      detail: {
        collectionName: committedCollectionName || "",
        setName: name || "",
      }
    }));
  }

  function isCreateParentChecked() {
    return !!createParentInput?.checked;
  }

  // ---- helpers ----
  const debounce = (fn, ms = 200) => {
    let t;
    return (...args) => {
      clearTimeout(t);
      t = setTimeout(() => fn.apply(null, args), ms);
    };
  };

  function clearList(listEl) {
    if (!listEl) return;
    listEl.innerHTML = "";
    listEl.hidden = true;
    listEl.removeAttribute("role");
    listEl._setActive = null;
    listEl._getActiveItem = null;
  }

  function renderList(listEl, items, onPick) {
    clearList(listEl);
    if (!listEl || !items || !items.length) return;

    listEl.setAttribute("role", "listbox");
    const frag = document.createDocumentFragment();

    const icon = listEl.id === "ac-col-list" ? "📁" : "👥";
    const capped = (items || []).slice(0, 20);
    capped.forEach((it, idx) => {
      const li = document.createElement("li");
      li.setAttribute("role", "option");
      li.dataset.index = String(idx);
      li.dataset.id = it.id;
      li.dataset.code = it.code || "";
      li.dataset.name = it.name || "";

      const iconSpan = document.createElement("span");
      iconSpan.className = "s-code";
      iconSpan.textContent = icon;
      li.appendChild(iconSpan);

      // label text then code chip (use DOM nodes to avoid injection)
      const nameSpan = document.createElement("span");
      nameSpan.textContent = it.name || "";
      li.appendChild(nameSpan);

      // Use mousedown so focus doesn't leave the input before we pick
      li.addEventListener("mousedown", (e) => {
        e.preventDefault();
        onPick(it);
      });

      frag.appendChild(li);
    });

    listEl.appendChild(frag);
    listEl.hidden = false;

    // keyboard navigation
    let active = -1;
    function setActive(i) {
      const lis = $$("li", listEl);
      lis.forEach((el, n) => el.classList.toggle("active", n === i));
      active = i;
      const el = lis[i];
      if (el) {
        el.scrollIntoView({ block: "nearest" });
        el.setAttribute("aria-selected", "true");
      }
    }
    listEl._setActive = setActive;
    listEl._getActiveItem = () => {
      const lis = $$("li", listEl);
      return lis[active] || null;
    };

    // no preselect
  }

  // ---- Collections AC (race-safe with AbortController) ----
  let colAbort = null;
  async function fetchCollections(q) {
    if (!q) return [];
    if (colAbort) colAbort.abort();
    colAbort = new AbortController();
    const url = `/manager/products/api/ac/collections/?q=${encodeURIComponent(q)}`;
    try {
      const r = await fetch(url, {
        headers: { "X-Requested-With": "fetch", "Accept": "application/json" },
        signal: colAbort.signal
      });
      if (!r.ok) return [];
      const data = await r.json();
      return data.items || [];
    } catch {
      return [];
    } finally {
      colAbort = null;
    }
  }

  function pickCollection(item) {
    colInput.value = (item.name || "").trim();
    selectedCollection = { id: item.id, code: item.code || "", name: item.name || "" };
    committedCollectionName = selectedCollection.name || "";
    committedSetName = "";
    liveSetTyping = false;
    emitPathCommit();
    clearList(colList);

    // reset sets input when collection changes
    setInput.value = "";
    clearList(setList);
    setInput.disabled = false;
    setInput.focus();
  }

  let colToken = 0;
  const onColType = debounce(async () => {
    const q = (colInput.value || "").trim();
    selectedCollection = null; // typing invalidates selection
    committedCollectionName = "";
    committedSetName = "";
    liveSetTyping = false;
    setInput.value = "";
    setInput.disabled = true;
    clearList(setList);

    if (!q) return clearList(colList);

    const myToken = ++colToken;
    const items = await fetchCollections(q);
    if (myToken !== colToken) return; // stale response
    renderList(colList, items, pickCollection);
  }, 200);

  colInput.addEventListener("input", onColType);
  colInput.addEventListener("focus", onColType);
  colInput.addEventListener("blur", () => {
    // small delay so mousedown on an item can run first
    setTimeout(async () => {
      clearList(colList);
      const val = (colInput.value || "").trim();
      if (!val) {
        committedCollectionName = "";
        committedSetName = "";
        emitPathCommit();
        return;
      }
      if (selectedCollection && val.toLowerCase() === (selectedCollection.name || "").toLowerCase()) {
        committedCollectionName = selectedCollection.name || "";
        emitPathCommit();
        return;
      }
      const items = await fetchCollections(val);
      const exact = items.find(it => (it.name || "").toLowerCase() === val.toLowerCase());
      if (exact) {
        pickCollection(exact);
      }
    }, 120);
  });

  colInput.addEventListener("keydown", (e) => {
    if (!colList || colList.hidden) {
      if (e.key !== "Enter") return; // let Tab move focus normally
      e.preventDefault();
      const val = (colInput.value || "").trim();
      if (!val) return;
      fetchCollections(val).then((items) => {
        const exact = items.find(it => (it.name || "").toLowerCase() === val.toLowerCase());
        if (exact) pickCollection(exact);
      });
      return;
    }
    const lis = $$("li", colList);
    if (!lis.length) return;

    const cur = lis.findIndex((el) => el.classList.contains("active"));

    if (e.key === "ArrowDown") {
      e.preventDefault();
      const next = (cur + 1) % lis.length;
      colList._setActive(next);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      const prev = (cur - 1 + lis.length) % lis.length;
      colList._setActive(prev);
    } else if (e.key === "Tab") {
      // Tab = next, Shift+Tab = prev
      e.preventDefault();
      const idx = e.shiftKey ? (cur - 1 + lis.length) % lis.length
                             : (cur + 1) % lis.length;
      colList._setActive(idx);
    } else if (e.key === "Enter") {
      e.preventDefault();
      const el = colList._getActiveItem() || lis[0];
      if (el) {
        pickCollection({
          id: +el.dataset.id,
          name: (el.dataset.name || "").trim(),
          code: el.dataset.code || "",
        });
      } else {
        const val = (colInput.value || "").trim();
        if (!val) return;
        fetchCollections(val).then((items) => {
          const exact = items.find(it => (it.name || "").toLowerCase() === val.toLowerCase());
          if (exact) pickCollection(exact);
        });
      }
    } else if (e.key === "Escape") {
      clearList(colList);
    }
  });

  // ---- Sets AC (race-safe with AbortController) ----
  let setAbort = null;
  async function fetchSets(q, cid) {
    if (!cid || !q) return [];
    if (setAbort) setAbort.abort();
    setAbort = new AbortController();
    const url = `/manager/products/api/ac/sets/?q=${encodeURIComponent(q)}&cid=${encodeURIComponent(cid)}`;
    try {
      const r = await fetch(url, {
        headers: { "X-Requested-With": "fetch", "Accept": "application/json" },
        signal: setAbort.signal
      });
      if (!r.ok) return [];
      const data = await r.json();
      return data.items || [];
    } catch {
      return [];
    } finally {
      setAbort = null;
    }
  }

  function pickSet(item) {
    setInput.value = (item.name || "").trim();
    committedSetName = (item.name || "").trim();
    liveSetTyping = false;
    emitPathCommit();
    clearList(setList);
  }

  let setToken = 0;
  const onSetType = debounce(async () => {
    const q = (setInput.value || "").trim();
    committedSetName = "";
    if (pathPreviewEnabled && isCreateParentChecked()) {
      liveSetTyping = true;
      emitPathLiveSet(q);
      clearList(setList);
      return;
    }
    if (!q || !selectedCollection) return clearList(setList);

    const myToken = ++setToken;
    const items = await fetchSets(q, selectedCollection.id);
    if (myToken !== setToken) return; // stale response
    renderList(setList, items, pickSet);
  }, 200);

  setInput.addEventListener("input", onSetType);
  setInput.addEventListener("focus", onSetType);
  setInput.addEventListener("blur", () => {
    setTimeout(async () => {
      clearList(setList);
      const val = (setInput.value || "").trim();
      if (!val) {
        committedSetName = "";
        liveSetTyping = false;
        emitPathCommit();
        return;
      }
      if (committedSetName && val.toLowerCase() === committedSetName.toLowerCase()) {
        emitPathCommit();
        return;
      }
      if (pathPreviewEnabled && isCreateParentChecked()) {
        liveSetTyping = true;
        emitPathLiveSet(val);
        return;
      }
      if (!selectedCollection) return;
      const items = await fetchSets(val, selectedCollection.id);
      const exact = items.find(it => (it.name || "").toLowerCase() === val.toLowerCase());
      if (exact) {
        pickSet(exact);
      }
    }, 120);
  });

  setInput.addEventListener("keydown", (e) => {
    if (!setList || setList.hidden) {
      if (e.key !== "Enter") return; // allow normal Tab when list closed
      e.preventDefault();
      const val = (setInput.value || "").trim();
      if (!val) return;
      if (pathPreviewEnabled && isCreateParentChecked()) {
        liveSetTyping = true;
        emitPathLiveSet(val);
        return;
      }
      if (!selectedCollection) return;
      fetchSets(val, selectedCollection.id).then((items) => {
        const exact = items.find(it => (it.name || "").toLowerCase() === val.toLowerCase());
        if (exact) pickSet(exact);
      });
      return;
    }
    const lis = $$("li", setList);
    if (!lis.length) return;

    const cur = lis.findIndex((el) => el.classList.contains("active"));

    if (e.key === "ArrowDown") {
      e.preventDefault();
      const next = (cur + 1) % lis.length;
      setList._setActive(next);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      const prev = (cur - 1 + lis.length) % lis.length;
      setList._setActive(prev);
    } else if (e.key === "Tab") {
      // Tab = next, Shift+Tab = prev
      e.preventDefault();
      const idx = e.shiftKey ? (cur - 1 + lis.length) % lis.length
                             : (cur + 1) % lis.length;
      setList._setActive(idx);
    } else if (e.key === "Enter") {
      e.preventDefault();
      const el = setList._getActiveItem() || lis[0];
      if (el) {
        pickSet({
          id: +el.dataset.id,
          name: (el.dataset.name || "").trim(),
          code: el.dataset.code || "",
        });
      } else {
        const val = (setInput.value || "").trim();
        if (!val) return;
        if (pathPreviewEnabled && isCreateParentChecked()) {
          liveSetTyping = true;
          emitPathLiveSet(val);
          return;
        }
        if (!selectedCollection) return;
        fetchSets(val, selectedCollection.id).then((items) => {
          const exact = items.find(it => (it.name || "").toLowerCase() === val.toLowerCase());
          if (exact) pickSet(exact);
        });
      }
    } else if (e.key === "Escape") {
      clearList(setList);
    }
  });

  // ---- Initial bootstrap (prefill awareness) ----
  async function bootstrapSelectedCollection() {
    const text = (colInput && colInput.value || "").trim();
    if (!text) return;

    const items = await fetchCollections(text);
    if (!items.length) return;

    // prefer exact (case-insensitive) name match; fallback to first result
    const exact = items.find(it => (it.name || "").toLowerCase() === text.toLowerCase());
    const pick = exact || items[0];
    selectedCollection = { id: pick.id, code: pick.code || "", name: pick.name || "" };
    committedCollectionName = selectedCollection.name || "";
    setInput.disabled = false;
    emitPathCommit();
  }

  if (pathPreviewEnabled && createParentInput) {
    createParentInput.addEventListener("change", () => {
      const val = (setInput?.value || "").trim();
      if (isCreateParentChecked()) {
        liveSetTyping = true;
        emitPathLiveSet(val);
        clearList(setList);
      } else {
        liveSetTyping = false;
        committedSetName = "";
        emitPathCommit();
      }
    });
  }

  // Disable set input until a collection is chosen (first render), then bootstrap
  setInput.disabled = true;
  bootstrapSelectedCollection();

  // ---- Single-unit UI rules ----
  const unitPrimary = document.querySelector('select[name="unit_primary"]');
  const unitSecondary = document.querySelector('select[name="unit_secondary"]');
  const convInput = document.querySelector('input[name="conversion_factor"]');
  function enforceSingleUnitUI() {
    const u1 = unitPrimary ? unitPrimary.value : "";
    const u2 = unitSecondary ? unitSecondary.value : "";
    const isSingle = !!u2 && u1 === u2;

    if (convInput) convInput.readOnly = isSingle;

    const u2IdInputs = document.querySelectorAll('input[name="unit_secondary_ids[]"]');
    const u2BarcodeInputs = document.querySelectorAll('input[name="barcodes_u2[]"]');
    u2IdInputs.forEach((el) => { el.disabled = isSingle; });
    u2BarcodeInputs.forEach((el) => { el.disabled = isSingle; });

    document.querySelectorAll('#u2-ids button.btn-muted, #u2-barcodes button.btn-muted').forEach((btn) => {
      btn.disabled = isSingle;
    });
  }

  unitPrimary?.addEventListener("change", enforceSingleUnitUI);
  unitSecondary?.addEventListener("change", enforceSingleUnitUI);
  enforceSingleUnitUI();

  const observer = new MutationObserver(() => {
    enforceSingleUnitUI();
  });
  observer.observe(document.body, { childList: true, subtree: true });
})();
