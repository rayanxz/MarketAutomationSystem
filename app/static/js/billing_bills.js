// static/js/billing_bills.js
(() => {
  const API_LIST = window.__BILLING__?.billsListUrl;
  const DELETE_TPL = window.__BILLING__?.billDeleteUrlTemplate;

  if (!API_LIST || !DELETE_TPL) {
    console.error("Missing __BILLING__ URLs; ensure template injected them.");
    return;
  }

  const form = document.getElementById("filters");
  const rowsEl = document.getElementById("rows");
  const loadMoreBtn = document.getElementById("loadMore");
  const endMsg = document.getElementById("endMsg");
  const sentinelEl = document.getElementById("sentinel");

  const modal = document.getElementById("modal-del");
  const delLabel = document.getElementById("delLabel");
  const btnClose = modal.querySelector("[data-close]");
  const btnConfirm = modal.querySelector("[data-confirm]");

  let cursor = null;
  let loading = false;
  let done = false;
  let pendingDeleteId = null;
  let debounceTimer = null;

  // ---------- Helpers ----------
  function qs(obj) {
    const p = new URLSearchParams(obj);
    return p.toString();
  }

  function nfmt(x) {
    const n = Number(x);
    return Number.isFinite(n) ? new Intl.NumberFormat().format(n) : (x ?? "");
  }

  function escHtml(v) {
    return String(v ?? "").replace(/[&<>"']/g, (ch) => {
      if (ch === "&") return "&amp;";
      if (ch === "<") return "&lt;";
      if (ch === ">") return "&gt;";
      if (ch === '"') return "&quot;";
      return "&#39;";
    });
  }

  function asText(v, fallback = "—") {
    if (v === null || v === undefined) return fallback;
    const t = String(v).trim();
    return t || fallback;
  }

  function ellipsisCell(v, fallback = "—") {
    const txt = asText(v, fallback);
    const safe = escHtml(txt);
    return `<span class="pname" title="${safe}"><span class="pname-text">${safe}</span></span>`;
  }

  function pill(status) {
    const cls = status === "paid" ? "paid" : (status === "partial" ? "partial" : "unpaid");
    const label = status === "paid"
      ? "مدفوعة"
      : (status === "partial" ? "مدفوعة جزئياً" : "غير مدفوعة");
    return `<span class="status ${cls}">${label}</span>`;
  }

  function row(b) {
    const dt = b.created_at ? new Date(b.created_at).toLocaleString() : "—";
    const viewUrl = `${window.__BILLING__.billViewBase}${b.id}/`;

    const creator =
      b.created_by_name ||
      (b.created_by && b.created_by.name) ||
      "—";

    const deleteLabel = asText(b.serial ?? b.id, "—");
    const safeDeleteLabel = escHtml(deleteLabel);

    let deleteBtn = "";
    if (b.can_delete) {
      deleteBtn = `<button class="btn danger" data-del="${b.id}" data-label="${safeDeleteLabel}">حذف</button>`;
    } else {
      deleteBtn = `<button class="btn danger" type="button" disabled title="لا يمكن حذف هذه الفاتورة (تم استخدام كميتها)">حذف</button>`;
    }

    return `
      <tr data-id="${b.id}">
        <td>${ellipsisCell(b.serial, "—")}</td>
        <td>${ellipsisCell(b.provider?.name, "—")}</td>
        <td>${ellipsisCell(creator, "—")}</td>
        <td>${ellipsisCell(nfmt(b.total_syp), "0")}</td>
        <td>${ellipsisCell(nfmt(b.total_usd), "0")}</td>
        <td>${pill(b.status)}</td>
        <td>${ellipsisCell(dt, "—")}</td>
        <td>
          <div class="actions-cell">
            <a class="btn btn-show" href="${viewUrl}">عرض</a>
            ${deleteBtn}
          </div>
        </td>
      </tr>
    `;
  }

  function readFilters(includeCursor = true) {
    const fd = new FormData(form);
    const obj = {};
    for (const [k, v] of fd.entries()) {
      if (v) obj[k] = v;
    }
    obj.page_size = 30;
    if (includeCursor && cursor) obj.cursor = cursor;
    return obj;
  }

  function updateUrlFromFilters() {
    const params = readFilters(false);
    const url = new URL(location.href);
    url.search = new URLSearchParams(params).toString();
    history.replaceState(null, "", url.toString());
  }

  function prefillFromUrl() {
    const params = new URLSearchParams(location.search);
    let changed = false;
    for (const [k, v] of params.entries()) {
      if (form.elements[k]) {
        form.elements[k].value = v;
        changed = true;
      }
    }
    return changed;
  }

  function debounce(fn, ms) {
    return (...args) => {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => fn(...args), ms);
    };
  }

  // ---------- Load ----------
  async function load(reset = false) {
    if (loading || (done && !reset)) return;
    loading = true;
    loadMoreBtn.disabled = true;
    endMsg.hidden = true;

    if (reset) {
      rowsEl.innerHTML = "";
      cursor = null;
      done = false;
    }

    const params = readFilters(!reset);

    try {
      const res = await fetch(`${API_LIST}?${qs(params)}`, { headers: { Accept: "application/json" } });
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || "error");

      const frag = document.createDocumentFragment();
      for (const b of data.items) {
        const tb = document.createElement("tbody");
        tb.innerHTML = row(b);
        frag.appendChild(tb.firstElementChild);
      }
      rowsEl.appendChild(frag);

      cursor = data.next_cursor || null;
      done = !cursor;
      loadMoreBtn.style.display = done ? "none" : "inline-block";
      if (done && rowsEl.children.length > 0) endMsg.hidden = false;
    } catch (e) {
      console.error(e);
      alert("فشل التحميل");
    } finally {
      loading = false;
      loadMoreBtn.disabled = false;
    }
  }

  // ---------- Events ----------
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    updateUrlFromFilters();
    load(true);
  });

  for (const el of form.querySelectorAll("input,select")) {
    el.addEventListener("input", debounce(() => {
      updateUrlFromFilters();
      load(true);
    }, 300));
    el.addEventListener("change", () => {
      updateUrlFromFilters();
      load(true);
    });
  }

  loadMoreBtn.addEventListener("click", () => load(false));

  if ("IntersectionObserver" in window && sentinelEl) {
    const io = new IntersectionObserver((entries) => {
      for (const en of entries) {
        if (en.isIntersecting) load(false);
      }
    });
    io.observe(sentinelEl);
  }

  document.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-del]");
    if (!btn) return;
    pendingDeleteId = parseInt(btn.dataset.del, 10);
    delLabel.textContent = `#${btn.dataset.label}`;
    modal.classList.add("open");
  });

  btnClose.addEventListener("click", () => {
    modal.classList.remove("open");
    pendingDeleteId = null;
  });

  modal.addEventListener("click", (e) => {
    if (e.target === modal) {
      modal.classList.remove("open");
      pendingDeleteId = null;
    }
  });

  btnConfirm.addEventListener("click", async () => {
    if (!pendingDeleteId) return;
    btnConfirm.disabled = true;
    try {
      const url = DELETE_TPL.replace("123456", String(pendingDeleteId));
      const res = await fetch(url, { method: "POST", headers: { "X-CSRFToken": getCsrf() } });
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || "delete failed");
      const tr = rowsEl.querySelector(`tr[data-id="${pendingDeleteId}"]`);
      if (tr) tr.remove();
      modal.classList.remove("open");
      pendingDeleteId = null;
    } catch (e) {
      console.error(e);
      alert("فشل الحذف");
    } finally {
      btnConfirm.disabled = false;
    }
  });

  function getCsrf() {
    const m = document.cookie.match(/(?:^|;)\s*csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  }

  prefillFromUrl();
  load(true);
})();
