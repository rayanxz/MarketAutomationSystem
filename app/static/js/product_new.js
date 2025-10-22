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

    items.forEach((it, idx) => {
      const li = document.createElement("li");
      li.setAttribute("role", "option");
      li.dataset.index = String(idx);
      li.dataset.id = it.id;
      li.dataset.code = it.code || "";

      // label text then code chip (use DOM nodes to avoid injection)
      const nameSpan = document.createElement("span");
      nameSpan.textContent = it.name || "";
      li.appendChild(nameSpan);

      const code = document.createElement("span");
      code.className = "code";
      code.style.marginInlineStart = "auto";
      code.textContent = it.code || "";
      li.appendChild(code);

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
      const lis = $$(".ac-list li", listEl);
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
      const lis = $$(".ac-list li", listEl);
      return lis[active] || null;
    };

    // preselect first item so Tab/Enter behavior is clear
    setActive(0);
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
    colInput.value = item.name || "";
    selectedCollection = { id: item.id, code: item.code || "", name: item.name || "" };
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
    setTimeout(() => clearList(colList), 120);
  });

  colInput.addEventListener("keydown", (e) => {
    if (!colList || colList.hidden) return; // let Tab move focus normally
    const lis = $$(".ac-list li", colList);
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
          name: el.querySelector("span")?.textContent.trim() || "",
          code: el.dataset.code || "",
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
    setInput.value = item.name || "";
    clearList(setList);
  }

  let setToken = 0;
  const onSetType = debounce(async () => {
    const q = (setInput.value || "").trim();
    if (!q || !selectedCollection) return clearList(setList);

    const myToken = ++setToken;
    const items = await fetchSets(q, selectedCollection.id);
    if (myToken !== setToken) return; // stale response
    renderList(setList, items, pickSet);
  }, 200);

  setInput.addEventListener("input", onSetType);
  setInput.addEventListener("focus", onSetType);
  setInput.addEventListener("blur", () => {
    setTimeout(() => clearList(setList), 120);
  });

  setInput.addEventListener("keydown", (e) => {
    if (!setList || setList.hidden) return; // allow normal Tab when list closed
    const lis = $$(".ac-list li", setList);
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
          name: el.querySelector("span")?.textContent.trim() || "",
          code: el.dataset.code || "",
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
    setInput.disabled = false;
  }

  // Disable set input until a collection is chosen (first render), then bootstrap
  setInput.disabled = true;
  bootstrapSelectedCollection();
})();
