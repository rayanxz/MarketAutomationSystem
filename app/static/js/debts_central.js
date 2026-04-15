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

  const causePrefixByType = {
    purchase_bill: "PB-",
    provider_return: "PR-",
    pos_bill: "PS-",
  };

  const debtIdLock = window.IdPrefixLock?.attach(fDebtId, { prefix: "D-" }) || null;
  const causeIdLock = window.IdPrefixLock?.attach(fCauseId, { prefix: "" }) || null;

  function nf(x, maxFractionDigits = 3) {
    const n = Number(x);
    if (!Number.isFinite(n)) return (x ?? "");
    return new Intl.NumberFormat(undefined, {
      minimumFractionDigits: 0,
      maximumFractionDigits: maxFractionDigits,
    }).format(n);
  }

  function fmtSyp(x) {
    return nf(x, 3);
  }

  function fmtUsd(x) {
    return nf(x, 2);
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
    const causeIdValue = causeIdLock
      ? causeIdLock.getValue({ emptyIfNoDigits: true })
      : (fCauseId?.value || "").trim();
    const debtIdValue = debtIdLock
      ? debtIdLock.getValue({ emptyIfNoDigits: true })
      : (fDebtId?.value || "").trim();
    const p = {
      page_size: 30,
      debt_type: (fDebtType?.value || "").trim(),
      cause_type: causeType,
      cause_id: (causeType === "manual" ? "" : causeIdValue),
      other_party_type: (fOtherPartyType?.value || "").trim(),
      other_party_name: (fOtherPartyName?.value || "").trim(),
      other_party_id: (fOtherPartyId?.value || "").trim(),
      debt_id: debtIdValue,
      status: (fStatus?.value || "").trim(),
      date_from: fFrom?.value || "",
      date_to: fTo?.value || "",
    };
    if (!reset && cursor) p.cursor = cursor;
    return p;
  }

  function arDebtType(v) {
    const x = String(v || "").toLowerCase();
    if (x === "debtor") return "\u0645\u062f\u064a\u0646";
    if (x === "creditor") return "\u062f\u0627\u0626\u0646";
    return "\u2014";
  }

  function arCauseType(v) {
    const x = String(v || "").toLowerCase();
    if (x === "purchase_bill") return "\u0641\u0627\u062a\u0648\u0631\u0629 \u0634\u0631\u0627\u0621";
    if (x === "pos_bill") return "\u0641\u0627\u062a\u0648\u0631\u0629 POS";
    if (x === "provider_return") return "\u0645\u0631\u062a\u062c\u0639 \u0645\u0648\u0631\u062f";
    if (x === "manual") return "\u062f\u064a\u0646 \u064a\u062f\u0648\u064a";
    return "\u2014";
  }

  function arPartyType(v) {
    const x = String(v || "").toLowerCase();
    if (x === "provider") return "\u0645\u0648\u0631\u062f";
    if (x === "customer") return "\u0632\u0628\u0648\u0646";
    if (x === "system_user") return "\u0645\u0633\u062a\u062e\u062f\u0645 \u0646\u0638\u0627\u0645";
    if (x === "other") return "\u0623\u062e\u0631\u0649";
    return "\u2014";
  }

  function arStatus(v) {
    return (String(v || "").toLowerCase() === "closed")
      ? "\u0645\u063a\u0644\u0642"
      : "\u0645\u0641\u062a\u0648\u062d";
  }

  function statusClass(v) {
    return (String(v || "").toLowerCase() === "closed") ? "status-closed" : "status-open";
  }

  function fmtDate(isoValue) {
    if (!isoValue) return "\u2014";
    const d = new Date(isoValue);
    if (Number.isNaN(d.getTime())) return "\u2014";
    return `${d.toLocaleDateString()} ${d.toLocaleTimeString()}`;
  }

  function sourceUrl(item) {
    const causeType = String(item.cause_type || "").toLowerCase();
    const causeId = String(item.cause_id || "").trim();
    if (!causeId) return "";
    const encoded = encodeURIComponent(causeId);
    if (causeType === "purchase_bill") return `/manager/billing/bills/${encoded}/`;
    if (causeType === "provider_return") return `/manager/billing/returns/${encoded}/`;
    if (causeType === "pos_bill") return `/pos/manager/bill/${encoded}/`;
    return "";
  }

  function rowHtml(item) {
    const totalSyp = Number(item.total_syp || 0);
    const totalUsd = Number(item.total_usd || 0);
    const totalSypText = fmtSyp(totalSyp);
    const totalUsdText = fmtUsd(totalUsd);
    const otherPartyName = item.other_party_name || item.other_party_id || "";
    const debtViewHref = String(item.debt_view_url || "").trim();
    const sourceHref = sourceUrl(item);
    const viewAction = debtViewHref
      ? `<a class="btn primary" href="${eh(debtViewHref)}">\u0639\u0631\u0636</a>`
      : `<button class="btn primary" type="button" disabled title="\u0644\u0627 \u064A\u0648\u062C\u062F \u0635\u0641\u062D\u0629 \u0639\u0631\u0636 \u0645\u062A\u0627\u062D\u0629">\u0639\u0631\u0636</button>`;
    const sourceAction = sourceHref
      ? `<a class="btn" href="${eh(sourceHref)}">\u0627\u0644\u0645\u0635\u062F\u0631</a>`
      : `<button class="btn" type="button" disabled title="\u0644\u0627 \u064A\u0648\u062C\u062F \u0645\u0635\u062F\u0631 \u0645\u0631\u0628\u0648\u0637">\u0627\u0644\u0645\u0635\u062F\u0631</button>`;
    const actionsHtml = `<span class="actions-group">${viewAction}${sourceAction}</span>`;

    return `
      <tr data-id="${eh(item.debt_id || item.id || "")}">
        <td><span class="truncate" title="${eh(item.debt_id || "")}">${eh(item.debt_id || "")}</span></td>
        <td>${eh(arDebtType(item.debt_type))}</td>
        <td>${eh(arCauseType(item.cause_type))}</td>
        <td><span class="truncate" title="${eh(item.cause_id || "")}">${eh(item.cause_id || "")}</span></td>
        <td>${eh(arPartyType(item.other_party_type))}</td>
        <td><span class="truncate" title="${eh(otherPartyName)}">${eh(otherPartyName)}</span></td>
        <td><span class="truncate" title="${eh(totalSypText)}">${eh(totalSypText)}</span></td>
        <td><span class="truncate" title="${eh(totalUsdText)}">${eh(totalUsdText)}</span></td>
        <td><span class="status-pill ${statusClass(item.status)}">${eh(arStatus(item.status))}</span></td>
        <td><span class="truncate" title="${eh(item.actor_username || "")}">${eh(item.actor_username || "\u2014")}</span></td>
        <td>${eh(fmtDate(item.created_at))}</td>
        <td class="left">${actionsHtml}</td>
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
        alert(`\u0641\u0634\u0644 \u0627\u0644\u062a\u062d\u0645\u064a\u0644\nHTTP ${res.status}\n${txt.slice(0, 300)}`);
        return;
      }
      const data = await res.json();
      if (!data.ok) {
        alert(`\u0641\u0634\u0644 \u0627\u0644\u062a\u062d\u0645\u064a\u0644\n${data.error || "unknown error"}`);
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
      alert(`\u0641\u0634\u0644 \u0627\u0644\u062a\u062d\u0645\u064a\u0644\n${e?.message || e}`);
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
  [fDebtType, fStatus, fFrom, fTo].forEach((el) => {
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

  function applyCauseIdLockFromType() {
    if (!causeIdLock) return;
    const t = String(fCauseType?.value || "").trim().toLowerCase();

    if (t === "manual") {
      causeIdLock.setPrefix("", { preserveNumeric: false, keepRawWhenUnlock: false });
      if (fCauseId) {
        fCauseId.value = "";
        fCauseId.disabled = true;
        fCauseId.title = "غير متاح مع الديون اليدوية";
      }
      return;
    }

    if (fCauseId) {
      fCauseId.disabled = false;
      fCauseId.title = "";
    }

    const nextPrefix = causePrefixByType[t] || "";
    if (nextPrefix) {
      causeIdLock.setPrefix(nextPrefix, { preserveNumeric: true });
      causeIdLock.ensurePrefix();
      return;
    }
    causeIdLock.setPrefix("", { preserveNumeric: false, keepRawWhenUnlock: false });
  }

  fCauseType?.addEventListener("change", () => {
    applyCauseIdLockFromType();
    load(true);
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

  applyCauseIdLockFromType();
  debtIdLock?.ensurePrefix();

  load(true);
})();
