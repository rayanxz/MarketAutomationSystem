// static/js/manager_search.js — highlight-only for collection/set; open+highlight for product
(() => {
  const box = document.getElementById("searchBox");
  if (!box) return;

  const q   = document.getElementById("q");
  const sug = document.getElementById("suggestions");
  const err = document.getElementById("searchErr");
  const scopeBox = document.getElementById("nameScope");
  if (!q || !sug || !err) return;

  // ---------- constants ----------
  const MODE_KEY = "mgr.search.mode";
  const SCOPE_KEY = "mgr.search.scope";
  const PENDING_KEY = "mgr.search.pending.open";
  const SHOW_DISABLED_KEY = "mgr.browser.show_disabled";
  const API_URL  = "/manager/products/api/search/";
  const ICON     = { collection: "📁", set: "👥", product: "📦" };
  const PENDING_TTL_MS = 5 * 60 * 1000;

  // ---------- state ----------
  let mode = localStorage.getItem(MODE_KEY) || "barcode";
  let scope = localStorage.getItem(SCOPE_KEY) || "all";
  let activeIndex = -1;
  let items = [];
  let debounceTimer = null;
  let inFlight = null; // AbortController for search

  // ---------- util ----------
  const setScope = (s) => {
    scope = s;
    localStorage.setItem(SCOPE_KEY, scope);
    sessionStorage.removeItem(PENDING_KEY);
    hideSuggestions();
    hideError();
    activeIndex = -1;
    if (q.value.trim()) {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => doSearch(q.value.trim()), 150);
    }
  };

  const updateScopeUI = () => {
    if (!scopeBox) return;
    const isName = (mode === "name");
    if (isName && !scope) scope = "all";
    scopeBox.querySelectorAll('input[name="scope"]').forEach((r) => {
      r.disabled = !isName;
      if (isName && r.value === scope) r.checked = true;
    });
    scopeBox.classList.toggle("is-disabled", !isName);
    if (isName) localStorage.setItem(SCOPE_KEY, scope);
  };

  const setMode = (m) => {
    mode = m;
    localStorage.setItem(MODE_KEY, mode);
    sessionStorage.removeItem(PENDING_KEY);
    updateScopeUI();
    hideSuggestions();
    hideError();
    activeIndex = -1;
    q.value = "";
    q.focus();
  };

  const hideSuggestions = () => {
    sug.hidden = true;
    sug.innerHTML = "";
    sug.removeAttribute("role");
    activeIndex = -1;
    items = [];
  };

  const hideError = () => {
    err.hidden = true;
    err.textContent = "";
  };

  const setActive = (i) => {
    activeIndex = i;
    const lis = [...sug.querySelectorAll("li[role='option']")];
    lis.forEach((li, idx) => {
      li.classList.toggle("active", idx === activeIndex);
      if (idx === activeIndex) {
        li.setAttribute("aria-selected", "true");
        li.scrollIntoView({ block: "nearest" });
      } else {
        li.removeAttribute("aria-selected");
      }
    });
  };
  const clearActive = () => {
    activeIndex = -1;
    const lis = [...sug.querySelectorAll("li[role='option']")];
    lis.forEach((li) => li.classList.remove("active"));
  };
  const syncActiveIndex = () => {
    if (activeIndex < 0) return;
    const lis = [...sug.querySelectorAll("li[role='option']")];
    if (!lis[activeIndex] || !lis[activeIndex].classList.contains("active")) {
      activeIndex = -1;
    }
  };

  const pathText = (it) => {
    if (it.type === "collection") return it.col_name || it.name || "";
    if (it.type === "set")        return `${it.col_name || ""}`;
    // product
    const col = it.col_name || it.col_code || "";
    const set = it.set_name || it.set_code || "";
    return `${col} · ${set}`;
  };

  const readShowDisabled = () => {
    const toggle = document.getElementById("showDisabledProducts");
    if (toggle) return !!toggle.checked;
    return localStorage.getItem(SHOW_DISABLED_KEY) === "1";
  };

  const applyShowDisabledToUrl = (u) => {
    u.searchParams.set("show_disabled", readShowDisabled() ? "1" : "0");
  };

  const currentSearchMatchMeta = () => {
    const value = (q.value || "").trim();
    if (!value) return null;
    if (mode === "barcode") return { highlightType: "barcode", highlightValue: value };
    if (mode === "id") return { highlightType: "unit_id", highlightValue: value };
    return null;
  };

  const applyProductHighlightParams = (u, meta) => {
    if (!u) return;
    if (!meta?.highlightType || !meta?.highlightValue) return;
    u.searchParams.set("highlight_type", meta.highlightType);
    u.searchParams.set("highlight_value", meta.highlightValue);
  };

  // Build suggestion list via DOM (avoid innerHTML injection)
  const renderSuggestions = (arr, queryText = "") => {
    items = (arr || []).slice(0, 20);
    if (!items.length) {
      if (!queryText) { hideSuggestions(); return; }
      sug.innerHTML = "";
      sug.hidden = false;
      sug.setAttribute("role", "listbox");
      const li = document.createElement("li");
      li.textContent = "لم يتم العثور على منتج مطابق";
      li.setAttribute("aria-disabled", "true");
      sug.appendChild(li);
      activeIndex = -1;
      return;
    }

    sug.innerHTML = "";
    sug.hidden = false;
    sug.setAttribute("role", "listbox");

    const frag = document.createDocumentFragment();
    items.forEach((p, i) => {
      const li = document.createElement("li");
      li.setAttribute("role", "option");
      li.dataset.i = String(i);
      if (p.type === "product" && p.is_active === false) {
        li.classList.add("is-disabled");
      }

      const icon = document.createElement("span");
      icon.className = "s-code";
      icon.textContent = ICON[p.type] || "";

      const name = document.createElement("span");
      name.textContent = p.name || "";

      const meta = document.createElement("span");
      meta.className = "s-path";
      meta.textContent = pathText(p);

      li.append(icon, name);
      li.addEventListener("click", () => onChoose(items[i]));
      frag.appendChild(li);
    });

    sug.appendChild(frag);
    clearActive();
  };

  const storePendingOpen = (it) => {
    if (!it || !it.type || !it.id) return;
    const matchMeta = it.type === "product" ? currentSearchMatchMeta() : null;
    const payload = {
      type: it.type,
      id: it.id,
      col_id: it.col_id,
      set_id: it.set_id,
      name: it.name,
      col_name: it.col_name || it.col_code || "",
      set_name: it.type === "set" ? (it.name || "") : (it.set_name || it.set_code || ""),
      query: (q.value || "").trim(),
      highlight_type: matchMeta?.highlightType || "",
      highlight_value: matchMeta?.highlightValue || "",
      ts: Date.now(),
    };
    sessionStorage.setItem(PENDING_KEY, JSON.stringify(payload));
  };

  const readPendingOpen = () => {
    const raw = sessionStorage.getItem(PENDING_KEY);
    if (!raw) return null;
    try {
      const data = JSON.parse(raw);
      if (!data || !data.ts || (Date.now() - data.ts) > PENDING_TTL_MS) {
        sessionStorage.removeItem(PENDING_KEY);
        return null;
      }
      return data;
    } catch {
      sessionStorage.removeItem(PENDING_KEY);
      return null;
    }
  };

  const clearPendingOpen = () => {
    sessionStorage.removeItem(PENDING_KEY);
  };

  const isEditableTarget = (el) => {
    if (!el) return false;
    if (el.isContentEditable) return true;
    const tag = (el.tagName || "").toLowerCase();
    return tag === "input" || tag === "textarea" || tag === "select";
  };

  const isInDialog = (el) => {
    if (!el || !el.closest) return false;
    return !!el.closest('[role="dialog"], .modal, .modal-backdrop');
  };

  // ---------- deep-link helpers ----------
  // Collection: stay on collections level and just highlight that collection
  function gotoCollection(item) {
    const u = new URL("/manager/products/", window.location.origin);
    // DO NOT set cid here; we want to remain on collections level
    u.searchParams.set("hl_col", item.id); // highlight specific collection
    u.searchParams.set("cname", item.name || item.col_name || "");
    applyShowDisabledToUrl(u);
    if (item.page) u.searchParams.set("page", item.page);
    window.location.href = u.toString();
  }

  // Set: go to sets level of its collection and highlight the set (don’t open products)
  function gotoSet(item) {
    const u = new URL("/manager/products/", window.location.origin);
    u.searchParams.set("cid", item.col_id);
    u.searchParams.set("cname", item.col_name || item.col_code || "");
    u.searchParams.set("hl_set", item.id);
    applyShowDisabledToUrl(u);
    if (item.page) u.searchParams.set("page", item.page);
    // do NOT set sid
    window.location.href = u.toString();
  }

  // Product: open products level and highlight the product
  function gotoProduct(item) {
    const u = new URL("/manager/products/", window.location.origin);
    u.searchParams.set("cid", item.col_id);
    if (item.set_id) u.searchParams.set("sid", item.set_id);
    u.searchParams.set("cname", item.col_name || item.col_code || "");
    if (item.set_name || item.set_code) {
      u.searchParams.set("sname", item.set_name || item.set_code);
    }
    applyShowDisabledToUrl(u);
    u.searchParams.set("hl_prod", item.id);
    if (item.page) u.searchParams.set("page", item.page);
    window.location.href = u.toString();
  }

  function openCollection(item) {
    if (!item?.id) return false;
    const u = new URL("/manager/products/", window.location.origin);
    u.searchParams.set("cid", item.id);
    u.searchParams.set("cname", item.name || item.col_name || "");
    applyShowDisabledToUrl(u);
    window.location.href = u.toString();
    return true;
  }

  function openSet(item) {
    if (!item?.id || !item?.col_id) return false;
    const u = new URL("/manager/products/", window.location.origin);
    u.searchParams.set("cid", item.col_id);
    u.searchParams.set("sid", item.id);
    u.searchParams.set("cname", item.col_name || "");
    if (item.set_name) u.searchParams.set("sname", item.set_name);
    applyShowDisabledToUrl(u);
    window.location.href = u.toString();
    return true;
  }

  function openProduct(item) {
    if (!item?.id) return false;
    const u = new URL(`/manager/products/${item.id}/edit/`, window.location.origin);
    applyProductHighlightParams(u, {
      highlightType: item.highlight_type || "",
      highlightValue: item.highlight_value || "",
    });
    window.location.href = u.toString();
    return true;
  }

  function tryOpenPending() {
    const pending = readPendingOpen();
    if (!pending) return false;
    const curQuery = (q.value || "").trim();
    if (curQuery && pending.query && pending.query !== curQuery) return false;
    let opened = false;
    if (pending.type === "collection") opened = openCollection(pending);
    else if (pending.type === "set") opened = openSet(pending);
    else if (pending.type === "product") opened = openProduct(pending);
    if (opened) clearPendingOpen();
    return opened;
  }

  function onChoose(it) {
    if (!it) return;
    storePendingOpen(it);
    if (it.type === "collection") return gotoCollection(it);
    if (it.type === "set")        return gotoSet(it);
    return gotoProduct(it);
  }

  // ---------- mode radios ----------
  box.querySelectorAll('input[name="mode"]').forEach((r) => {
    r.checked = (r.value === mode);
    r.addEventListener("change", () => setMode(r.value));
  });
  scopeBox && scopeBox.querySelectorAll('input[name="scope"]').forEach((r) => {
    r.checked = (r.value === scope);
    r.addEventListener("change", () => setScope(r.value));
  });
  updateScopeUI();

  // ---------- search ----------
  const doSearch = async (val) => {
    if (inFlight) inFlight.abort();
    inFlight = new AbortController();

    try {
      const params = new URLSearchParams();
      params.set("mode", mode);
      params.set("q", val);
      params.set("show_disabled", readShowDisabled() ? "1" : "0");
      if (mode === "name") params.set("scope", scope || "all");
      const res = await fetch(
        `${API_URL}?${params.toString()}`,
        { headers: { Accept: "application/json" }, signal: inFlight.signal }
      );
      if (!res.ok) return renderSuggestions([], val);
      const data = await res.json();
      if (!data.ok) return renderSuggestions([], val);
      renderSuggestions(data.items || [], val);
    } catch {
      // aborted or network error
      renderSuggestions([], val);
    } finally {
      inFlight = null;
    }
  };

  // Typing (name / id / code modes)
  q.addEventListener("input", () => {
    hideError();
    clearPendingOpen();
    if (mode === "barcode") { hideSuggestions(); return; }
    const val = q.value.trim();
    if (!val) { hideSuggestions(); return; }
    activeIndex = -1;
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => doSearch(val), 150);
  });

  // Keyboard navigation: Tab / Shift+Tab / Arrows / Enter (for suggestions)
  q.addEventListener("keydown", (e) => {
    if (sug.hidden || !items.length) return;

    if (e.key === "ArrowDown") {
      e.preventDefault();
      syncActiveIndex();
      const next = activeIndex < 0 ? 0 : Math.min(activeIndex + 1, items.length - 1);
      setActive(next);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      syncActiveIndex();
      if (activeIndex < 0) return;
      const prev = Math.max(activeIndex - 1, 0);
      setActive(prev);
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (activeIndex >= 0) return onChoose(items[activeIndex]);
      if (tryOpenPending()) return;
    } else if (e.key === "Escape") {
      hideSuggestions();
    }
  });

  // Give suggestion list keyboard priority even when radios have focus
  document.addEventListener("keydown", (e) => {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    if (sug.hidden || !items.length) return;
    if (!box.contains(document.activeElement)) return;
    if (document.activeElement === q) return;
    e.preventDefault();
    if (e.key === "ArrowDown") {
      syncActiveIndex();
      const next = activeIndex < 0 ? 0 : Math.min(activeIndex + 1, items.length - 1);
      setActive(next);
    } else {
      syncActiveIndex();
      if (activeIndex < 0) return;
      const prev = Math.max(activeIndex - 1, 0);
      setActive(prev);
    }
  });

  // Enter selection when focus is not on the input (radios, etc.)
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    if (sug.hidden || !items.length) return;
    if (!box.contains(document.activeElement)) return;
    if (document.activeElement === q) return;
    if (activeIndex < 0) return;
    e.preventDefault();
    onChoose(items[activeIndex]);
  });

  // Global Enter: open pending search result even if input lost focus
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    if (box.contains(document.activeElement)) return;
    if (isEditableTarget(e.target)) return;
    if (isInDialog(e.target)) return;
    if (tryOpenPending()) e.preventDefault();
  });

  // Enter with no suggestions: try "open" on the highlighted search result
  q.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    if (!sug.hidden && items.length) return;
    if (tryOpenPending()) e.preventDefault();
  });

  // Barcode mode → highlight product (not edit)
  q.addEventListener("keydown", async (e) => {
    if (mode !== "barcode" || e.key !== "Enter") return;
    const val = q.value.trim();
    if (!val) return;
    clearPendingOpen();

    try {
      const res = await fetch(
        `${API_URL}?mode=barcode&q=${encodeURIComponent(val)}&show_disabled=${readShowDisabled() ? "1" : "0"}`,
        { headers: { Accept: "application/json" } }
      );
      if (!res.ok) {
        err.textContent = "لم يتم العثور على نتيجة.";
        err.hidden = false;
        return;
      }
      const data = await res.json();
      if (!data.ok || !(data.items && data.items.length)) {
        err.textContent = "لم يتم العثور على نتيجة.";
        err.hidden = false;
        return;
      }
      const item = { ...(data.items[0] || {}), type: "product" };
      storePendingOpen(item);
      gotoProduct(item);
    } catch {
      err.textContent = "حدث خطأ في البحث.";
      err.hidden = false;
    }
  });
})();
