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
  const pathPreviewEnabled = !!(pathEl && pathEl.dataset.pathLive === "1");
  const isEditMode = !!(pathEl && pathEl.dataset.mode === "edit");
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

  async function fetchCollectionsValidation(q) {
    if (!q) return [];
    const url = `/manager/products/api/ac/collections/?q=${encodeURIComponent(q)}`;
    try {
      const r = await fetch(url, {
        headers: { "X-Requested-With": "fetch", "Accept": "application/json" }
      });
      if (!r.ok) return [];
      const data = await r.json();
      return data.items || [];
    } catch {
      return [];
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

  colInput.addEventListener("keydown", async (e) => {
    if (!colList || colList.hidden) {
      if (e.key !== "Enter") return; // let Tab move focus normally
      e.preventDefault();
      const val = (colInput.value || "").trim();
      if (!val) return;
      const items = await fetchCollections(val);
      const exact = items.find(it => (it.name || "").toLowerCase() === val.toLowerCase());
      if (exact) {
        pickCollection(exact);
        return;
      }
      skipNextFocusoutValidation.add(colInput);
      const ok = await validateFieldForKeyboard("collection_name", colInput);
      if (!ok) colInput.focus();
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
        const items = await fetchCollections(val);
        const exact = items.find(it => (it.name || "").toLowerCase() === val.toLowerCase());
        if (exact) {
          pickCollection(exact);
          return;
        }
        skipNextFocusoutValidation.add(colInput);
        const ok = await validateFieldForKeyboard("collection_name", colInput);
        if (!ok) colInput.focus();
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

  async function fetchSetsValidation(q, cid) {
    if (!cid || !q) return [];
    const url = `/manager/products/api/ac/sets/?q=${encodeURIComponent(q)}&cid=${encodeURIComponent(cid)}`;
    try {
      const r = await fetch(url, {
        headers: { "X-Requested-With": "fetch", "Accept": "application/json" }
      });
      if (!r.ok) return [];
      const data = await r.json();
      return data.items || [];
    } catch {
      return [];
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

  setInput.addEventListener("keydown", async (e) => {
    if (!setList || setList.hidden) {
      if (e.key !== "Enter") return; // allow normal Tab when list closed
      e.preventDefault();
      const val = (setInput.value || "").trim();
      if (!val) return;
      if (pathPreviewEnabled && isCreateParentChecked()) {
        liveSetTyping = true;
        emitPathLiveSet(val);
        skipNextFocusoutValidation.add(setInput);
        const ok = await validateFieldForKeyboard("set_name", setInput);
        if (ok) {
          const next = nextFocusableFrom(setInput);
          focusElement(next);
        } else {
          setInput.focus();
        }
        return;
      }
      if (!selectedCollection) return;
      const items = await fetchSets(val, selectedCollection.id);
      const exact = items.find(it => (it.name || "").toLowerCase() === val.toLowerCase());
      if (exact) {
        pickSet(exact);
        focusProductNameField();
        return;
      }
      skipNextFocusoutValidation.add(setInput);
      const ok = await validateFieldForKeyboard("set_name", setInput);
      if (!ok && createParentInput && !isCreateParentChecked() && isFocusable(createParentInput)) {
        createParentInput.focus();
        return;
      }
      if (ok) {
        focusProductNameField();
      } else {
        setInput.focus();
      }
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
    } else if (e.key === "Enter") {
      e.preventDefault();
      const el = setList._getActiveItem() || lis[0];
      if (el) {
        pickSet({
          id: +el.dataset.id,
          name: (el.dataset.name || "").trim(),
          code: el.dataset.code || "",
        });
        focusProductNameField();
      } else {
        const val = (setInput.value || "").trim();
        if (!val) return;
        if (pathPreviewEnabled && isCreateParentChecked()) {
          liveSetTyping = true;
          emitPathLiveSet(val);
          skipNextFocusoutValidation.add(setInput);
          const ok = await validateFieldForKeyboard("set_name", setInput);
          if (ok) {
            const next = nextFocusableFrom(setInput);
            focusElement(next);
          } else {
            setInput.focus();
          }
          return;
        }
        if (!selectedCollection) return;
        const items = await fetchSets(val, selectedCollection.id);
        const exact = items.find(it => (it.name || "").toLowerCase() === val.toLowerCase());
        if (exact) {
          pickSet(exact);
          focusProductNameField();
          return;
        }
        skipNextFocusoutValidation.add(setInput);
        const ok = await validateFieldForKeyboard("set_name", setInput);
        if (!ok && createParentInput && !isCreateParentChecked() && isFocusable(createParentInput)) {
          createParentInput.focus();
          return;
        }
        if (ok) {
          focusProductNameField();
        } else {
          setInput.focus();
        }
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

    // Prefer exact match. On edit hydration, avoid fallback so invalid text is not auto-committed.
    const exact = items.find(it => (it.name || "").toLowerCase() === text.toLowerCase());
    const pick = isEditMode ? exact : (exact || items[0]);
    if (!pick) return;
    selectedCollection = { id: pick.id, code: pick.code || "", name: pick.name || "" };
    committedCollectionName = selectedCollection.name || "";
    setInput.disabled = false;

    if (isEditMode && !isCreateParentChecked()) {
      const setText = (setInput && setInput.value || "").trim();
      if (setText) {
        const setItems = await fetchSets(setText, selectedCollection.id);
        const exactSet = setItems.find(it => (it.name || "").toLowerCase() === setText.toLowerCase());
        committedSetName = exactSet ? (exactSet.name || "").trim() : "";
      } else {
        committedSetName = "";
      }
    }

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

  // ---- Real-time validation controller ----
  const formEl = document.querySelector("form.pn-wrap");
  const productId = (formEl?.dataset.productId || "").trim();
  const touched = new Set();
  const asyncTokens = new Map();
  const rowTokens = new WeakMap();
  const rowCache = new WeakMap();
  const skipNextFocusoutValidation = new WeakSet();
  const unitSelectFocusValue = new WeakMap();
  const unitSelectEnterCount = new WeakMap();
  let submittingValidatedForm = false;
  const productNameInput = document.querySelector('[name="name"]');

  function normalizeText(v) {
    return (v || "").trim();
  }

  const highlightParams = isEditMode ? new URLSearchParams(window.location.search) : null;
  const highlightType = normalizeText(highlightParams?.get("highlight_type") || "");
  const highlightValue = normalizeText(highlightParams?.get("highlight_value") || "");
  let highlightedIdentifierInput = null;
  let identifierHighlightDismissed = false;

  function identifierSelectorForHighlight(type) {
    if (type === "unit_id") {
      return 'input[name="unit_primary_ids[]"], input[name="unit_secondary_ids[]"]';
    }
    if (type === "barcode") {
      return 'input[name="barcodes_u1[]"], input[name="barcodes_u2[]"]';
    }
    return "";
  }

  function clearIdentifierHighlight(input) {
    if (!input) return;
    input.classList.remove("search-hit");
    if (highlightedIdentifierInput === input) highlightedIdentifierInput = null;
  }

  function bindIdentifierHighlightDismiss(input) {
    if (!input || input.dataset.highlightDismissBound === "1") return;
    const dismiss = () => {
      identifierHighlightDismissed = true;
      clearIdentifierHighlight(input);
    };
    input.addEventListener("input", dismiss);
    input.addEventListener("change", dismiss);
    input.dataset.highlightDismissBound = "1";
  }

  function applyIdentifierHighlightIfNeeded() {
    if (!isEditMode || identifierHighlightDismissed || highlightedIdentifierInput) return;
    if (!highlightType || !highlightValue) return;
    const selector = identifierSelectorForHighlight(highlightType);
    if (!selector) return;
    const match = Array.from(document.querySelectorAll(selector)).find((input) => {
      if (!(input instanceof HTMLInputElement) || input.disabled) return false;
      return normalizeText(input.value) === highlightValue;
    });
    if (!match) return;
    highlightedIdentifierInput = match;
    match.classList.add("search-hit");
    bindIdentifierHighlightDismiss(match);
  }

  function isLocked(el) {
    return !!(el && (el.disabled || el.hasAttribute("readonly")));
  }

  function markTouched(name) {
    if (name) touched.add(name);
  }

  function shouldShow(name, force = false) {
    return !!(force || touched.has(name));
  }

  function errorNodeFor(name) {
    let node = document.querySelector(`[data-live-error-for="${name}"]`);
    if (node) return node;
    const field = document.querySelector(`[data-live-field="${name}"]`);
    if (!field) return null;
    node = document.createElement("div");
    node.className = "err";
    node.dataset.liveErrorFor = name;
    node.hidden = true;
    field.appendChild(node);
    return node;
  }

  function fieldElements(name) {
    return Array.from(document.querySelectorAll(`[name="${name}"]`));
  }

  function setFieldError(name, msg, { force = false } = {}) {
    const els = fieldElements(name);
    els.forEach((el) => el.classList.toggle("invalid", !!msg && shouldShow(name, force)));
    const node = errorNodeFor(name);
    if (!node) return;
    if (!msg || !shouldShow(name, force)) {
      node.textContent = "";
      node.hidden = true;
      return;
    }
    node.textContent = msg;
    node.hidden = false;
  }

  function clearFieldError(name) {
    setFieldError(name, "");
  }

  function fieldHasError(name) {
    const node = document.querySelector(`[data-live-error-for="${name}"]`);
    if (node && !node.hidden && (node.textContent || "").trim()) return true;
    return fieldElements(name).some((el) => el.classList.contains("invalid"));
  }

  function isFocusable(el) {
    if (!(el instanceof HTMLElement)) return false;
    if (el.hasAttribute("disabled")) return false;
    if (el.getAttribute("type") === "hidden") return false;
    if (el.hasAttribute("readonly")) return false;
    if (el.closest("[hidden]")) return false;
    return true;
  }

  function focusElement(el) {
    if (!isFocusable(el)) return false;
    el.focus();
    return true;
  }

  function nextFocusableFrom(current) {
    if (!formEl || !(current instanceof HTMLElement)) return null;
    const candidates = Array.from(formEl.querySelectorAll("input, select, textarea, button, a[href]"))
      .filter((el) => isFocusable(el));
    const idx = candidates.indexOf(current);
    if (idx < 0) return null;
    for (let i = idx + 1; i < candidates.length; i += 1) {
      if (isFocusable(candidates[i])) return candidates[i];
    }
    return null;
  }

  function focusProductNameField() {
    return focusElement(productNameInput);
  }

  function focusNextForField(name, current) {
    if (name === "unit_primary") {
      return focusElement(unitSecondary);
    }
    if (name === "unit_secondary") {
      if ((unitSecondary?.value || "") && isFocusable(convInput)) {
        return focusElement(convInput);
      }
      return focusElement(nextFocusableFrom(current));
    }
    return focusElement(nextFocusableFrom(current));
  }

  async function commitUnitSelectNavigation(target) {
    if (!(target instanceof HTMLSelectElement)) return;
    const name = target.name;
    if (!name) return;

    skipNextFocusoutValidation.add(target);
    enforceSingleUnitUI();
    const ok = await validateFieldForKeyboard(name, target);
    if (!ok) {
      target.focus();
      return;
    }

    requestAnimationFrame(() => {
      focusNextForField(name, target);
    });
  }

  async function exactCollectionByName(name) {
    const q = normalizeText(name);
    if (!q) return null;
    const items = await fetchCollectionsValidation(q);
    return items.find((it) => normalizeText(it.name).toLowerCase() === q.toLowerCase()) || null;
  }

  async function exactSetByName(name, cid) {
    const q = normalizeText(name);
    if (!q || !cid) return null;
    const items = await fetchSetsValidation(q, cid);
    return items.find((it) => normalizeText(it.name).toLowerCase() === q.toLowerCase()) || null;
  }

  async function validateProductName({ force = false } = {}) {
    const el = document.querySelector('[name="name"]');
    if (!el || isLocked(el)) return clearFieldError("name");
    const name = normalizeText(el.value);
    if (!name) {
      setFieldError("name", "يرجى إدخال اسم المنتج.", { force });
      return false;
    }
    const token = Symbol("name");
    asyncTokens.set("name", token);
    try {
      const url = `/manager/products/api/validate/name/?name=${encodeURIComponent(name)}${productId ? `&exclude_pk=${encodeURIComponent(productId)}` : ""}`;
      const res = await fetch(url, { headers: { "X-Requested-With": "fetch", "Accept": "application/json" } });
      if (!res.ok) return true;
      const data = await res.json();
      if (asyncTokens.get("name") !== token) return true;
      if (data.exists) {
        setFieldError("name", "اسم المنتج موجود مسبقاً.", { force });
        return false;
      }
      clearFieldError("name");
      return true;
    } catch {
      return true;
    }
  }

  async function validateCollection({ force = false } = {}) {
    const el = document.querySelector('[name="collection_name"]');
    if (!el || isLocked(el)) return clearFieldError("collection_name");
    const value = normalizeText(el.value);
    if (!value) {
      setFieldError("collection_name", "يرجى إدخال اسم الزمرة.", { force });
      return false;
    }
    const token = Symbol("collection");
    asyncTokens.set("collection_name", token);
    try {
      const exact = await exactCollectionByName(value);
      if (asyncTokens.get("collection_name") !== token) return true;
      if (!exact) {
        setFieldError("collection_name", "الزمرة غير موجودة.", { force });
        return false;
      }
      clearFieldError("collection_name");
      return true;
    } catch {
      return true;
    }
  }

  async function validateSet({ force = false } = {}) {
    const el = document.querySelector('[name="set_name"]');
    const colEl = document.querySelector('[name="collection_name"]');
    if (!el || isLocked(el)) return clearFieldError("set_name");
    const setName = normalizeText(el.value);
    const colName = normalizeText(colEl?.value);
    const wantsCreate = isCreateParentChecked();

    if (!colName) {
      clearFieldError("set_name");
      return true;
    }

    const token = Symbol("set");
    asyncTokens.set("set_name", token);
    try {
      const col = await exactCollectionByName(colName);
      if (asyncTokens.get("set_name") !== token) return true;
      if (!col) {
        clearFieldError("set_name");
        return true;
      }
      if (!setName) {
        if (!wantsCreate) {
          setFieldError("set_name", "المجموعة الأب غير موجودة. حدِّد اسماً صحيحاً أو فعّل خيار الإنشاء.", { force });
          return false;
        }
        const defaultNewSet = await exactSetByName("مجموعة جديدة", col.id);
        if (asyncTokens.get("set_name") !== token) return true;
        if (defaultNewSet) {
          setFieldError("set_name", "اسم المجموعة الأب موجود مسبقاً ضمن نفس الزمرة.", { force });
          return false;
        }
        clearFieldError("set_name");
        return true;
      }
      const exactSet = await exactSetByName(setName, col.id);
      if (asyncTokens.get("set_name") !== token) return true;
      if (wantsCreate) {
        if (exactSet) {
          setFieldError("set_name", "اسم المجموعة الأب موجود مسبقاً ضمن نفس الزمرة.", { force });
          return false;
        }
        clearFieldError("set_name");
        return true;
      }
      if (!exactSet) {
        setFieldError("set_name", "المجموعة الأب غير موجودة. حدِّد اسماً صحيحاً أو فعّل خيار الإنشاء.", { force });
        return false;
      }
      clearFieldError("set_name");
      return true;
    } catch {
      return true;
    }
  }

  function validateUnits({ force = false } = {}) {
    const primary = unitPrimary;
    const secondary = unitSecondary;
    if (!primary || !secondary || isLocked(primary) || isLocked(secondary)) {
      clearFieldError("unit_secondary");
      return true;
    }
    const p = primary.value || "";
    const s = secondary.value || "";
    if (p && s && p === s) {
      setFieldError("unit_secondary", "Second unit must differ from primary unit.", { force });
      return false;
    }
    clearFieldError("unit_secondary");
    return true;
  }

  function validateConversion({ force = false } = {}) {
    const secondary = unitSecondary;
    if (!secondary || !convInput || isLocked(secondary) || isLocked(convInput)) {
      clearFieldError("conversion_factor");
      return true;
    }
    const s = secondary.value || "";
    const raw = normalizeText(convInput.value);
    if (!s) {
      clearFieldError("conversion_factor");
      return true;
    }
    if (!raw) {
      setFieldError("conversion_factor", "مطلوب عند تحديد الوحدة الثانية.", { force });
      return false;
    }
    const n = Number.parseFloat(raw.replace(/,/g, ""));
    if (!Number.isFinite(n) || n <= 0) {
      setFieldError("conversion_factor", "Required and must be > 0 when second unit is set.", { force });
      return false;
    }
    clearFieldError("conversion_factor");
    return true;
  }

  function selectedRadioValue(name) {
    return document.querySelector(`input[name="${name}"]:checked`)?.value || "";
  }

  function validateNonNegativeNumberField(name, { force = false, active = true } = {}) {
    const el = document.querySelector(`[name="${name}"]`);
    if (!el || !active || isLocked(el)) {
      clearFieldError(name);
      return true;
    }

    const raw = normalizeText(el.value);
    if (!raw) {
      clearFieldError(name);
      return true;
    }

    if (el.validity?.badInput) {
      setFieldError(name, "يرجى إدخال رقم صالح.", { force });
      return false;
    }

    const value = Number.parseFloat(raw.replace(/,/g, ""));
    if (!Number.isFinite(value)) {
      setFieldError(name, "يرجى إدخال رقم صالح.", { force });
      return false;
    }
    if (value < 0) {
      setFieldError(name, "يجب أن تكون القيمة أكبر من أو تساوي 0.", { force });
      return false;
    }

    clearFieldError(name);
    return true;
  }

  function validatePurchaseCurrency({ force = false } = {}) {
    const allowSyp = !!document.getElementById("allowSypPurch")?.checked;
    const allowUsd = !!document.getElementById("allowUsdPurch")?.checked;
    let ok = true;

    ok = validateNonNegativeNumberField("default_cost_syp", { force, active: allowSyp }) && ok;
    ok = validateNonNegativeNumberField("default_cost_usd", { force, active: allowUsd }) && ok;

    if (allowSyp && allowUsd && !selectedRadioValue("default_purchase_currency")) {
      setFieldError("default_purchase_currency", "Default purchase currency must be enabled.", { force });
      ok = false;
    } else {
      clearFieldError("default_purchase_currency");
    }
    return ok;
  }

  function validateSalesCurrency({ force = false } = {}) {
    const allowSyp = !!document.getElementById("allowSypSales")?.checked;
    const allowUsd = !!document.getElementById("allowUsdSales")?.checked;
    let ok = true;

    ok = validateNonNegativeNumberField("default_price_syp", { force, active: allowSyp }) && ok;
    ok = validateNonNegativeNumberField("default_price_usd", { force, active: allowUsd }) && ok;

    if (allowSyp && allowUsd && !selectedRadioValue("default_sale_currency")) {
      setFieldError("default_sale_currency", "Default sale currency must be enabled.", { force });
      ok = false;
    } else {
      clearFieldError("default_sale_currency");
    }
    return ok;
  }

  function rowErrorNode(input, { create = false } = {}) {
    const row = input?.closest(".inline-input");
    if (!row) return null;
    let node = row.querySelector("[data-row-error]");
    if (!node && create) {
      node = document.createElement("div");
      node.className = "err";
      node.dataset.rowError = "1";
      row.appendChild(node);
    }
    return node;
  }

  function setRowError(input, key, msg) {
    if (!input) return;
    if (msg) input.dataset[`rowErr${key}`] = msg;
    else delete input.dataset[`rowErr${key}`];

    const messages = [];
    if (input.dataset.rowErrWhitespace) messages.push(input.dataset.rowErrWhitespace);
    if (input.dataset.rowErrLocal) messages.push(input.dataset.rowErrLocal);
    if (input.dataset.rowErrRemote) messages.push(input.dataset.rowErrRemote);

    input.classList.toggle("invalid", messages.length > 0);
    if (!messages.length) {
      const node = rowErrorNode(input, { create: false });
      if (!node) return;
      node.remove();
      return;
    }
    const node = rowErrorNode(input, { create: true });
    if (!node) return;
    node.textContent = messages[0];
  }

  function clearRowErrors() {
    document.querySelectorAll(".inline-input input").forEach((input) => {
      setRowError(input, "Whitespace", "");
      setRowError(input, "Local", "");
      setRowError(input, "Remote", "");
    });
  }

  function flagRepeatedInputs(inputs, msg) {
    inputs.forEach((input) => {
      if (!input) return;
      setRowError(input, "Local", msg);
    });
  }

  function validateRepeatedEntries() {
    document.querySelectorAll(".inline-input input").forEach((input) => {
      setRowError(input, "Whitespace", "");
      setRowError(input, "Local", "");
    });

    const groups = [
      { name: 'unit_primary_ids[]', label: "هذا المعرّف مكرر داخل النموذج." },
      { name: 'unit_secondary_ids[]', label: "هذا المعرّف مكرر داخل النموذج." },
      { name: 'barcodes_u1[]', label: "هذا الباركود مكرر داخل النموذج." },
      { name: 'barcodes_u2[]', label: "هذا الباركود مكرر داخل النموذج." },
    ];

    groups.forEach(({ name, label }) => {
      const seen = new Map();
      Array.from(document.querySelectorAll(`input[name="${name}"]`))
        .filter((input) => !input.disabled)
        .forEach((input) => {
          const raw = input.value || "";
          const key = normalizeText(raw);
          if (!key) {
            if (raw && !raw.trim()) setRowError(input, "Whitespace", "يرجى إزالة المسافات أو إدخال قيمة صالحة.");
            return;
          }
          if (!seen.has(key)) seen.set(key, []);
          seen.get(key).push(input);
        });
      seen.forEach((inputs) => {
        if (inputs.length > 1) flagRepeatedInputs(inputs, label);
      });
    });

    const crossGroups = [
      ['unit_primary_ids[]', 'unit_secondary_ids[]', "هذا المعرّف مكرر بين الوحدتين."],
      ['barcodes_u1[]', 'barcodes_u2[]', "هذا الباركود مكرر بين الوحدتين."],
    ];
    crossGroups.forEach(([aName, bName, msg]) => {
      const mapA = new Map();
      Array.from(document.querySelectorAll(`input[name="${aName}"]`))
        .filter((input) => !input.disabled)
        .forEach((input) => {
          const raw = input.value || "";
          const key = normalizeText(raw);
          if (!key) {
            if (raw && !raw.trim()) setRowError(input, "Whitespace", "يرجى إزالة المسافات أو إدخال قيمة صالحة.");
            return;
          }
          if (!mapA.has(key)) mapA.set(key, []);
          mapA.get(key).push(input);
        });
      Array.from(document.querySelectorAll(`input[name="${bName}"]`))
        .filter((input) => !input.disabled)
        .forEach((input) => {
          const raw = input.value || "";
          const key = normalizeText(raw);
          if (!key) {
            if (raw && !raw.trim()) setRowError(input, "Whitespace", "يرجى إزالة المسافات أو إدخال قيمة صالحة.");
            return;
          }
          if (!mapA.has(key)) return;
          flagRepeatedInputs([...mapA.get(key), input], msg);
        });
    });
  }

  function identifierKindForName(name) {
    if (name === "unit_primary_ids[]" || name === "unit_secondary_ids[]") return "unit_id";
    if (name === "barcodes_u1[]" || name === "barcodes_u2[]") return "barcode";
    return "";
  }

  function localRowHasError(input) {
    return !!(input?.dataset.rowErrWhitespace || input?.dataset.rowErrLocal);
  }

  async function validateIdentifierRow(input) {
    if (!(input instanceof HTMLInputElement) || input.disabled) return true;
    const kind = identifierKindForName(input.name);
    if (!kind) return true;

    const raw = input.value || "";
    const value = normalizeText(raw);
    if (!value) {
      if (raw && !raw.trim()) {
        setRowError(input, "Whitespace", "يرجى إزالة المسافات أو إدخال قيمة صالحة.");
        return false;
      }
      setRowError(input, "Remote", "");
      rowCache.delete(input);
      return true;
    }

    if (localRowHasError(input)) {
      setRowError(input, "Remote", "");
      rowCache.delete(input);
      return false;
    }

    const cached = rowCache.get(input);
    if (cached && cached.kind === kind && cached.value === value && cached.excludePk === productId) {
      setRowError(input, "Remote", cached.exists ? (kind === "unit_id" ? "هذا المعرّف مستخدم مسبقاً." : "هذا الباركود مستخدم مسبقاً.") : "");
      return !cached.exists;
    }

    const token = Symbol(`${kind}:${value}`);
    rowTokens.set(input, token);
    try {
      const url = `/manager/products/api/validate/identifier/?kind=${encodeURIComponent(kind)}&value=${encodeURIComponent(value)}${productId ? `&exclude_pk=${encodeURIComponent(productId)}` : ""}`;
      const res = await fetch(url, { headers: { "X-Requested-With": "fetch", "Accept": "application/json" } });
      if (!res.ok) return true;
      const data = await res.json();
      if (rowTokens.get(input) !== token) return true;
      const exists = !!data.exists;
      rowCache.set(input, { kind, value, excludePk: productId, exists });
      setRowError(input, "Remote", exists ? (kind === "unit_id" ? "هذا المعرّف مستخدم مسبقاً." : "هذا الباركود مستخدم مسبقاً.") : "");
      return !exists;
    } catch {
      return true;
    }
  }

  async function validateAllIdentifierRows() {
    const inputs = Array.from(document.querySelectorAll(
      'input[name="unit_primary_ids[]"], input[name="unit_secondary_ids[]"], input[name="barcodes_u1[]"], input[name="barcodes_u2[]"]'
    )).filter((input) => !input.disabled);
    let ok = true;
    for (const input of inputs) {
      ok = (await validateIdentifierRow(input)) && ok;
    }
    return ok;
  }

  async function validateFieldForKeyboard(name, target) {
    if (!name) return true;
    markTouched(name);

    switch (name) {
      case "collection_name":
        await validateCollection({ force: true });
        return !fieldHasError("collection_name");
      case "set_name":
        await validateSet({ force: true });
        return !fieldHasError("set_name");
      case "create_parent":
        await validateSet({ force: true });
        return !fieldHasError("set_name");
      case "name":
        await validateProductName({ force: true });
        return !fieldHasError("name");
      case "unit_primary":
      case "unit_secondary":
        validateUnits({ force: true });
        validateConversion({ force: true });
        return !fieldHasError("unit_secondary");
      case "conversion_factor":
        validateConversion({ force: true });
        return !fieldHasError("conversion_factor");
      case "default_cost_syp":
      case "default_cost_usd":
        validatePurchaseCurrency({ force: true });
        return !fieldHasError(name);
      case "default_price_syp":
      case "default_price_usd":
        validateSalesCurrency({ force: true });
        return !fieldHasError(name);
      case "cost_syp":
      case "cost_usd":
      case "price_syp":
      case "price_usd":
        validateNonNegativeNumberField(name, { force: true });
        return !fieldHasError(name);
      case "unit_primary_ids[]":
      case "unit_secondary_ids[]":
      case "barcodes_u1[]":
      case "barcodes_u2[]":
        validateRepeatedEntries();
        if (target instanceof HTMLInputElement) {
          await validateIdentifierRow(target);
        }
        return !(target instanceof HTMLElement && target.classList.contains("invalid"));
      default:
        validateSyncFor(name, true);
        await validateAsyncFor(name, true);
        return !fieldHasError(name);
    }
  }

  function validateSyncFor(name, force = false) {
    switch (name) {
      case "unit_primary":
      case "unit_secondary":
        validateUnits({ force });
        validateConversion({ force });
        validateRepeatedEntries();
        break;
      case "conversion_factor":
        validateConversion({ force });
        break;
      case "default_purchase_currency":
      case "allow_syp_purchasing":
      case "allow_usd_purchasing":
      case "default_cost_syp":
      case "default_cost_usd":
        validatePurchaseCurrency({ force });
        break;
      case "default_sale_currency":
      case "allow_syp_sales":
      case "allow_usd_sales":
      case "default_price_syp":
      case "default_price_usd":
        validateSalesCurrency({ force });
        break;
      case "cost_syp":
      case "cost_usd":
      case "price_syp":
      case "price_usd":
        validateNonNegativeNumberField(name, { force });
        break;
      case "unit_primary_ids[]":
      case "unit_secondary_ids[]":
      case "barcodes_u1[]":
      case "barcodes_u2[]":
        validateRepeatedEntries();
        break;
      default:
        break;
    }
  }

  async function validateAsyncFor(name, force = false) {
    switch (name) {
      case "name":
        return validateProductName({ force });
      case "collection_name":
        await validateCollection({ force });
        if (shouldShow("set_name")) await validateSet({ force });
        return true;
      case "set_name":
      case "create_parent":
        return validateSet({ force });
      case "unit_primary_ids[]":
      case "unit_secondary_ids[]":
      case "barcodes_u1[]":
      case "barcodes_u2[]":
        return true;
      default:
        return true;
    }
  }

  function scheduleFieldValidation(name, { force = false, delay = 0, target = null } = {}) {
    if (!name) return;
    const run = async () => {
      validateSyncFor(name, force);
      await validateAsyncFor(name, force);
      if (target && identifierKindForName(name)) {
        await validateIdentifierRow(target);
      }
    };
    if (delay > 0) {
      window.setTimeout(run, delay);
    } else {
      run();
    }
  }

  formEl?.addEventListener("focusout", (e) => {
    const target = e.target;
    if (!(target instanceof HTMLInputElement || target instanceof HTMLSelectElement || target instanceof HTMLTextAreaElement)) return;
    if (target instanceof HTMLSelectElement && (target === unitPrimary || target === unitSecondary)) {
      unitSelectEnterCount.delete(target);
    }
    if (skipNextFocusoutValidation.has(target)) {
      skipNextFocusoutValidation.delete(target);
      return;
    }
    const name = target.name;
    if (!name) return;
    markTouched(name);
    const delay = (name === "collection_name" || name === "set_name") ? 170 : 0;
    scheduleFieldValidation(name, { force: true, delay, target });
  });

  formEl?.addEventListener("focusin", (e) => {
    const target = e.target;
    if (!(target instanceof HTMLSelectElement)) return;
    if (target !== unitPrimary && target !== unitSecondary) return;
    unitSelectFocusValue.set(target, target.value || "");
    unitSelectEnterCount.set(target, 0);
  });

  formEl?.addEventListener("change", async (e) => {
    const target = e.target;
    if (!(target instanceof HTMLInputElement || target instanceof HTMLSelectElement || target instanceof HTMLTextAreaElement)) return;
    const name = target.name;
    if (!name) return;
    markTouched(name);
    if (target instanceof HTMLSelectElement && (target === unitPrimary || target === unitSecondary)) {
      unitSelectEnterCount.set(target, 0);
      await commitUnitSelectNavigation(target);
      return;
    }
    scheduleFieldValidation(name, { force: true, target });
  });

  formEl?.addEventListener("keyup", async (e) => {
    if (e.key !== "Enter" || e.ctrlKey || e.metaKey) return;
    const target = e.target;
    if (!(target instanceof HTMLSelectElement)) return;
    if (target !== unitPrimary && target !== unitSecondary) return;
    const enterCount = unitSelectEnterCount.get(target) || 0;
    if (enterCount < 2) return;
    unitSelectEnterCount.set(target, 0);
    const focusedValue = unitSelectFocusValue.get(target);
    const currentValue = target.value || "";
    if (focusedValue !== currentValue) return;
    await commitUnitSelectNavigation(target);
  });

  document.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" || (!e.ctrlKey && !e.metaKey)) return;
    if (!formEl || !document.body.contains(formEl)) return;
    e.preventDefault();
    e.stopPropagation();
    formEl.requestSubmit();
  }, true);

  formEl?.addEventListener("keydown", async (e) => {
    if (e.key !== "Enter") return;
    const target = e.target;
    if (!(target instanceof HTMLElement)) return;

    if (e.defaultPrevented) return;
    if (target instanceof HTMLTextAreaElement) return;
    if (target instanceof HTMLSelectElement) {
      if (target === unitPrimary || target === unitSecondary) {
        unitSelectEnterCount.set(target, (unitSelectEnterCount.get(target) || 0) + 1);
      }
      return;
    }
    if (!(target instanceof HTMLInputElement)) return;

    const inputType = (target.type || "text").toLowerCase();
    if (["submit", "button", "reset", "hidden"].includes(inputType)) return;

    e.preventDefault();

    if (inputType === "checkbox") {
      target.checked = !target.checked;
      target.dispatchEvent(new Event("change", { bubbles: true }));
      const next = nextFocusableFrom(target);
      focusElement(next);
      return;
    }

    const name = target.name;
    if (!name) return;
    skipNextFocusoutValidation.add(target);
    const ok = await validateFieldForKeyboard(name, target);
    if (!ok) {
      target.focus();
      return;
    }
    if (identifierKindForName(name)) {
      target.blur();
      return;
    }
    const next = nextFocusableFrom(target);
    if (next) {
      focusElement(next);
      return;
    }
    target.blur();
  });

  function firstInvalidFocusable() {
    const invalid = document.querySelector(
      ".pn-wrap .input.invalid, .pn-wrap input.invalid, .pn-wrap select.invalid, .pn-wrap textarea.invalid"
    );
    if (invalid instanceof HTMLElement && !invalid.disabled) return invalid;
    return null;
  }

  async function runClientValidation({ force = false } = {}) {
    const fieldNames = [
      "name",
      "collection_name",
      "set_name",
      "create_parent",
      "unit_primary",
      "unit_secondary",
      "conversion_factor",
      "allow_syp_purchasing",
      "allow_usd_purchasing",
      "default_purchase_currency",
      "default_cost_syp",
      "default_cost_usd",
      "allow_syp_sales",
      "allow_usd_sales",
      "default_sale_currency",
      "default_price_syp",
      "default_price_usd",
      "cost_syp",
      "cost_usd",
      "price_syp",
      "price_usd",
    ];

    fieldNames.forEach((name) => {
      markTouched(name);
      validateSyncFor(name, force);
    });

    validateRepeatedEntries();

    const asyncResults = [];
    asyncResults.push(await validateProductName({ force }));
    // Collection and set validation share collection lookups, so keep submit-time
    // validation ordered to avoid abort-controller conflicts from autocomplete fetches.
    asyncResults.push(await validateCollection({ force }));
    asyncResults.push(await validateSet({ force }));
    asyncResults.push(await validateAllIdentifierRows());

    const syncOk =
      validateUnits({ force }) &&
      validateConversion({ force }) &&
      validatePurchaseCurrency({ force }) &&
      validateSalesCurrency({ force }) &&
      validateNonNegativeNumberField("cost_syp", { force }) &&
      validateNonNegativeNumberField("cost_usd", { force }) &&
      validateNonNegativeNumberField("price_syp", { force }) &&
      validateNonNegativeNumberField("price_usd", { force }) &&
      !document.querySelector(".inline-input input.invalid");

    return syncOk && asyncResults.every(Boolean);
  }

  formEl?.addEventListener("submit", async (e) => {
    if (submittingValidatedForm) return;
    e.preventDefault();
    const ok = await runClientValidation({ force: true });
    if (ok) {
      submittingValidatedForm = true;
      formEl.submit();
      return;
    }
    const firstInvalid = firstInvalidFocusable();
    if (firstInvalid) {
      firstInvalid.focus({ preventScroll: false });
      firstInvalid.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  });

  // ---- Single-unit UI rules ----
  const unitPrimary = document.querySelector('select[name="unit_primary"]');
  const unitSecondary = document.querySelector('select[name="unit_secondary"]');
  const convInput = document.querySelector('input[name="conversion_factor"]');

  function syncUnitOptions() {
    if (!unitPrimary || !unitSecondary) return;

    const primaryVal = unitPrimary.value || "";
    const secondaryVal = unitSecondary.value || "";

    if (primaryVal && secondaryVal && primaryVal === secondaryVal) {
      unitSecondary.value = "";
    }

    const nextPrimaryVal = unitPrimary.value || "";
    const nextSecondaryVal = unitSecondary.value || "";

    Array.from(unitPrimary.options).forEach((opt) => {
      const disable = !!(opt.value && nextSecondaryVal && opt.value === nextSecondaryVal);
      opt.disabled = disable;
      opt.hidden = disable;
    });

    Array.from(unitSecondary.options).forEach((opt) => {
      const disable = !!(opt.value && nextPrimaryVal && opt.value === nextPrimaryVal);
      opt.disabled = disable;
      opt.hidden = disable;
    });
  }

  function enforceSingleUnitUI() {
    syncUnitOptions();
    const u2 = unitSecondary ? unitSecondary.value : "";
    const isSingle = !u2;

    if (convInput) convInput.disabled = isSingle;

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
  applyIdentifierHighlightIfNeeded();

  document.addEventListener("product:rows-changed", () => {
    enforceSingleUnitUI();
    validateRepeatedEntries();
    applyIdentifierHighlightIfNeeded();
  });
})();
