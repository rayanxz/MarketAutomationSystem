// static/js/manager_search.js — highlight-only for collection/set; open+highlight for product
(() => {
  const box = document.getElementById("searchBox");
  if (!box) return;

  const q   = document.getElementById("q");
  const sug = document.getElementById("suggestions");
  const err = document.getElementById("searchErr");
  if (!q || !sug || !err) return;

  // ---------- constants ----------
  const MODE_KEY = "mgr.search.mode";
  const API_URL  = "/manager/products/api/search/";
  const ICON     = { collection: "📁", set: "👥", product: "📦" };

  // ---------- state ----------
  let mode = localStorage.getItem(MODE_KEY) || "barcode";
  let activeIndex = -1;
  let items = [];
  let debounceTimer = null;
  let inFlight = null; // AbortController for search

  // ---------- util ----------
  const setMode = (m) => {
    mode = m;
    localStorage.setItem(MODE_KEY, mode);
    hideSuggestions();
    hideError();
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

  const pathText = (it) => {
    if (it.type === "collection") return it.col_name || it.name || "";
    if (it.type === "set")        return `${it.col_name || ""}`;
    // product
    const col = it.col_name || it.col_code || "";
    const set = it.set_name || it.set_code || "";
    return `${col} · ${set}`;
  };

  // Build suggestion list via DOM (avoid innerHTML injection)
  const renderSuggestions = (arr, queryText = "") => {
    items = arr || [];
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

      const icon = document.createElement("span");
      icon.className = "s-code";
      icon.textContent = ICON[p.type] || "";

      const name = document.createElement("span");
      name.textContent = p.name || "";

      const meta = document.createElement("span");
      meta.className = "s-path";
      meta.textContent = pathText(p);

      li.append(icon, name, meta);
      li.addEventListener("click", () => onChoose(items[i]));
      frag.appendChild(li);
    });

    sug.appendChild(frag);
    setActive(0);
  };

  // ---------- deep-link helpers ----------
  // Collection: stay on collections level and just highlight that collection
  function gotoCollection(item) {
    const u = new URL("/manager/products/", window.location.origin);
    // DO NOT set cid here; we want to remain on collections level
    u.searchParams.set("hl_col", item.id); // highlight specific collection
    u.searchParams.set("cname", item.name || item.col_name || "");
    if (item.page) u.searchParams.set("page", item.page);
    window.location.href = u.toString();
  }

  // Set: go to sets level of its collection and highlight the set (don’t open products)
  function gotoSet(item) {
    const u = new URL("/manager/products/", window.location.origin);
    u.searchParams.set("cid", item.col_id);
    u.searchParams.set("cname", item.col_name || item.col_code || "");
    u.searchParams.set("hl_set", item.id);
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
    u.searchParams.set("hl_prod", item.id);
    if (item.page) u.searchParams.set("page", item.page);
    window.location.href = u.toString();
  }

  function onChoose(it) {
    if (!it) return;
    if (it.type === "collection") return gotoCollection(it);
    if (it.type === "set")        return gotoSet(it);
    return gotoProduct(it);
  }

  // ---------- mode radios ----------
  box.querySelectorAll('input[name="mode"]').forEach((r) => {
    r.checked = (r.value === mode);
    r.addEventListener("change", () => setMode(r.value));
  });

  // ---------- search ----------
  const doSearch = async (val) => {
    if (inFlight) inFlight.abort();
    inFlight = new AbortController();

    try {
      const res = await fetch(
        `${API_URL}?mode=${encodeURIComponent(mode)}&q=${encodeURIComponent(val)}`,
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
    if (mode === "barcode") { hideSuggestions(); return; }
    const val = q.value.trim();
    if (!val) { hideSuggestions(); return; }
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => doSearch(val), 150);
  });

  // Keyboard navigation: Tab / Shift+Tab / Arrows / Enter (for suggestions)
  q.addEventListener("keydown", (e) => {
    if (sug.hidden || !items.length) return;

    if (e.key === "Tab") {
      e.preventDefault();
      const next = (activeIndex + (e.shiftKey ? -1 : 1) + items.length) % items.length;
      setActive(next);
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((activeIndex + 1) % items.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((activeIndex - 1 + items.length) % items.length);
    } else if (e.key === "Enter") {
      e.preventDefault();
      onChoose(items[Math.max(0, activeIndex)]);
    } else if (e.key === "Escape") {
      hideSuggestions();
    }
  });

  // Barcode mode → highlight product (not edit)
  q.addEventListener("keydown", async (e) => {
    if (mode !== "barcode" || e.key !== "Enter") return;
    const val = q.value.trim();
    if (!val) return;

    try {
      const res = await fetch(`${API_URL}?mode=barcode&q=${encodeURIComponent(val)}`, {
        headers: { Accept: "application/json" },
      });
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
      gotoProduct({ ...(data.items[0] || {}), type: "product" });
    } catch {
      err.textContent = "حدث خطأ في البحث.";
      err.hidden = false;
    }
  });
})();
