(function () {
  "use strict";

  const CFG = window.CENTRAL_DEBTS_CFG || {};
  const API = {
    LIST: CFG.listUrl || "/manager/debts/api/records/",
    SUGGEST: CFG.suggestUrl || "/manager/debts/api/other-party-suggest/",
  };

  const $ = (s, r = document) => r.querySelector(s);

  const rows = $("#rows");
  const loadMore = $("#loadMore");
  const endMsg = $("#endMsg");

  const fDebtType = $("#fDebtType");
  const fCauseType = $("#fCauseType");
  const fCauseId = $("#fCauseId");
  const fOtherPartyType = $("#fOtherPartyType");
  const fOtherPartyName = $("#fOtherPartyName");
  const fOtherPartyId = $("#fOtherPartyId");
  const fDebtId = $("#fDebtId");
  const fStatus = $("#fStatus");
  const fFrom = $("#fFrom");
  const fTo = $("#fTo");
  const btnSearch = $("#btnSearch");
  const partySuggest = $("#partySuggest");

  let cursor = null;
  let busy = false;
  let done = false;

  let suggestItems = [];
  let suggestIdx = -1;
  let suggestAbort = null;

  function nf(x) {
    const n = Number(x);
    return Number.isFinite(n) ? new Intl.NumberFormat().format(n) : (x ?? "");
  }

  function eh(s) {
    return String(s ?? "").replace(/[&<>"]/g, (c) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      "\"": "&quot;",
    }[c]));
  }

  function qs(obj) {
    const u = new URLSearchParams();
    Object.entries(obj).forEach(([k, v]) => {
      if (v !== "" && v != null) u.append(k, v);
    });
    return u.toString();
  }

  function params(reset = false) {
    const causeType = (fCauseType?.value || "").trim();
    const p = {
      page_size: 30,
      debt_type: (fDebtType?.value || "").trim(),
      cause_type: causeType,
      cause_id: causeType ? (fCauseId?.value || "").trim() : "",
      other_party_type: (fOtherPartyType?.value || "").trim(),
      other_party_name: (fOtherPartyName?.value || "").trim(),
      other_party_id: (fOtherPartyId?.value || "").trim(),
      debt_id: (fDebtId?.value || "").trim(),
      status: (fStatus?.value || "").trim(),
      date_from: fFrom?.value || "",
      date_to: fTo?.value || "",
    };
    if (!reset && cursor) p.cursor = cursor;
    return p;
  }

  function arDebtType(v) {
    const x = String(v || "").toLowerCase();
    if (x === "debtor") return "مدين";
    if (x === "creditor") return "دائن";
    return "—";
  }

  function arCauseType(v) {
    const x = String(v || "").toLowerCase();
    if (x === "purchase_bill") return "فاتورة شراء";
    if (x === "pos_bill") return "فاتورة POS";
    if (x === "provider_return") return "مرتجع مورد";
    if (x === "manual") return "دين يدوي";
    return "—";
  }

  function arPartyType(v) {
    const x = String(v || "").toLowerCase();
    if (x === "provider") return "مورد";
    if (x === "customer") return "زبون";
    if (x === "system_user") return "مستخدم نظام";
    if (x === "other") return "أخرى";
    return "—";
  }

  function arStatus(v) {
    return (String(v || "").toLowerCase() === "closed") ? "مغلق" : "مفتوح";
  }

  function statusClass(v) {
    return (String(v || "").toLowerCase() === "closed") ? "status-closed" : "status-open";
  }

  function fmtTotal(item) {
    const syp = Number(item.total_syp || 0);
    const usd = Number(item.total_usd || 0);
    return `SYP ${nf(syp)} | USD ${nf(usd)}`;
  }

  function fmtDate(isoValue) {
    if (!isoValue) return "—";
    const d = new Date(isoValue);
    if (Number.isNaN(d.getTime())) return "—";
    return `${d.toLocaleDateString()} ${d.toLocaleTimeString()}`;
  }

  function sourceUrl(item) {
    const causeType = String(item.cause_type || "").toLowerCase();
    const causeId = String(item.cause_id || "").trim();
    if (!/^\d+$/.test(causeId)) return "";
    if (causeType === "purchase_bill") return `/manager/billing/bills/${causeId}/`;
    if (causeType === "provider_return") return `/manager/billing/returns/${causeId}/`;
    if (causeType === "pos_bill") return `/pos/manager/bill/${causeId}/`;
    return "";
  }

  function rowHtml(item) {
    const remSyp = Number(item.remaining_syp || 0);
    const remUsd = Number(item.remaining_usd || 0);
    const totalText = fmtTotal(item);
    const otherPartyName = item.other_party_name || item.other_party_id || "";
    const sourceHref = sourceUrl(item);
    const sourceAction = sourceHref
      ? `<a class="btn" href="${eh(sourceHref)}">عرض المصدر</a>`
      : `<span class="muted">—</span>`;

    return `
      <tr data-id="${eh(item.debt_id || item.id || "")}">
        <td><span class="truncate" title="${eh(item.debt_id || "")}">${eh(item.debt_id || "")}</span></td>
        <td>${eh(arDebtType(item.debt_type))}</td>
        <td>${eh(arCauseType(item.cause_type))}</td>
        <td><span class="truncate" title="${eh(item.cause_id || "")}">${eh(item.cause_id || "")}</span></td>
        <td>${eh(arPartyType(item.other_party_type))}</td>
        <td><span class="truncate" title="${eh(otherPartyName)}">${eh(otherPartyName)}</span></td>
        <td>${nf(remSyp)}</td>
        <td>${nf(remUsd)}</td>
        <td><span class="truncate" title="${eh(totalText)}">${eh(totalText)}</span></td>
        <td><span class="status-pill ${statusClass(item.status)}">${eh(arStatus(item.status))}</span></td>
        <td><span class="truncate" title="${eh(item.actor_username || "")}">${eh(item.actor_username || "—")}</span></td>
        <td>${eh(fmtDate(item.created_at))}</td>
        <td class="left">${sourceAction}</td>
      </tr>
    `;
  }

  async function load(reset = false) {
    if (busy || (done && !reset)) return;
    busy = true;
    loadMore.disabled = true;
    endMsg.hidden = true;

    if (reset) {
      rows.innerHTML = "";
      cursor = null;
      done = false;
    }

    try {
      const url = `${API.LIST}?${qs(params(reset))}`;
      const res = await fetch(url, { headers: { Accept: "application/json" } });
      if (!res.ok) {
        const txt = await res.text().catch(() => "(no body)");
        alert(`فشل التحميل\nHTTP ${res.status}\n${txt.slice(0, 300)}`);
        return;
      }
      const data = await res.json();
      if (!data.ok) {
        alert(`فشل التحميل\n${data.error || "unknown error"}`);
        return;
      }

      const frag = document.createDocumentFragment();
      for (const item of (data.items || [])) {
        const tmp = document.createElement("tbody");
        tmp.innerHTML = rowHtml(item);
        frag.appendChild(tmp.firstElementChild);
      }
      rows.appendChild(frag);

      cursor = data.next_cursor || null;
      done = !cursor;
      loadMore.style.display = done ? "none" : "inline-flex";
      if (done && rows.children.length) endMsg.hidden = false;
    } catch (e) {
      alert(`فشل التحميل\n${e?.message || e}`);
    } finally {
      busy = false;
      loadMore.disabled = false;
    }
  }

  function closeSuggest() {
    if (!partySuggest) return;
    partySuggest.hidden = true;
    partySuggest.innerHTML = "";
    suggestItems = [];
    suggestIdx = -1;
  }

  function renderSuggest(items) {
    if (!partySuggest) return;
    if (!items.length) {
      closeSuggest();
      return;
    }
    partySuggest.innerHTML = "";
    items.forEach((it, idx) => {
      const li = document.createElement("li");
      li.dataset.id = String(it.id ?? "");
      li.dataset.name = String(it.name ?? "");
      li.className = (idx === 0) ? "active" : "";
      li.textContent = String(it.name ?? "");
      partySuggest.appendChild(li);
    });
    partySuggest.hidden = false;
    suggestItems = Array.from(partySuggest.querySelectorAll("li"));
    suggestIdx = suggestItems.length ? 0 : -1;
  }

  function pickSuggest(li) {
    if (!li) return;
    if (fOtherPartyName) fOtherPartyName.value = li.dataset.name || "";
    if (fOtherPartyId) fOtherPartyId.value = li.dataset.id || "";
    closeSuggest();
  }

  async function fetchSuggest() {
    const ptype = (fOtherPartyType?.value || "").trim();
    const q = (fOtherPartyName?.value || "").trim();
    if (!ptype || !q) {
      closeSuggest();
      return;
    }
    if (suggestAbort) suggestAbort.abort();
    suggestAbort = new AbortController();
    const signal = suggestAbort.signal;
    const url = `${API.SUGGEST}?${qs({ other_party_type: ptype, q, limit: 8 })}`;
    try {
      const res = await fetch(url, { headers: { Accept: "application/json" }, signal });
      const data = await res.json();
      if (!data.ok || !Array.isArray(data.items)) {
        closeSuggest();
        return;
      }
      renderSuggest(data.items);
    } catch (e) {
      if (e?.name !== "AbortError") closeSuggest();
    }
  }

  function handleSuggestKey(e) {
    if (partySuggest?.hidden || !suggestItems.length) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      suggestIdx = Math.min(suggestIdx + 1, suggestItems.length - 1);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      suggestIdx = Math.max(suggestIdx - 1, 0);
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (suggestIdx >= 0) pickSuggest(suggestItems[suggestIdx]);
      return;
    } else {
      return;
    }
    suggestItems.forEach((li, idx) => li.classList.toggle("active", idx === suggestIdx));
  }

  btnSearch?.addEventListener("click", () => load(true));
  [fDebtType, fCauseType, fStatus, fFrom, fTo].forEach((el) => {
    el?.addEventListener("change", () => load(true));
  });
  loadMore?.addEventListener("click", () => load(false));

  [fCauseId, fDebtId].forEach((el) => {
    el?.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        load(true);
      }
    });
  });

  fCauseType?.addEventListener("change", () => {
    if (!fCauseType.value && fCauseId) fCauseId.value = "";
  });

  fOtherPartyType?.addEventListener("change", () => {
    if (fOtherPartyName) fOtherPartyName.value = "";
    if (fOtherPartyId) fOtherPartyId.value = "";
    closeSuggest();
    load(true);
  });

  let suggestTimer = null;
  fOtherPartyName?.addEventListener("input", () => {
    if (fOtherPartyId) fOtherPartyId.value = "";
    clearTimeout(suggestTimer);
    suggestTimer = setTimeout(fetchSuggest, 180);
  });
  fOtherPartyName?.addEventListener("keydown", handleSuggestKey);
  fOtherPartyName?.addEventListener("blur", () => {
    setTimeout(closeSuggest, 120);
  });

  partySuggest?.addEventListener("mousedown", (e) => {
    const li = e.target.closest("li[data-id]");
    if (!li) return;
    e.preventDefault();
    pickSuggest(li);
    load(true);
  });

  load(true);
})();
