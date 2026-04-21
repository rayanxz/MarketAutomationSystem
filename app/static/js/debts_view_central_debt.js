(() => {
  "use strict";

  const CFG = window.CENTRAL_DEBT_VIEW_CFG || {};
  const EPS = 0.000001;

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

  const displayCurrencySelect = $("#debtSettlementCurrency");
  const settleTotalEl = $("#debtSettleTotal");
  const settleCurLabel = $("#debtSettleCurLabel");

  const zoneEl = $("#leftSettlementZone");
  const statusBadgeEl = $("#debtStatusBadge");
  const statusTextEl = $("#debtStatusText");
  const remainingSypEl = $("#debtRemainingSypValue");
  const remainingUsdEl = $("#debtRemainingUsdValue");

  const startBtn = $("#debtBatchStartBtn");
  const formEl = $("#debtBatchForm");
  const containerSelect = $("#debtBatchContainer");
  const submitBtn = $("#debtBatchSubmitBtn");
  const cancelBtn = $("#debtBatchCancelBtn");
  const errorEl = $("#debtBatchError");

  const separateRadio = $("#debtBatchMethodSeparate");
  const separateLabel = $("#debtBatchSeparateLabel");

  const sypOnlyInput = $("#debtBatchSypOnly");
  const usdOnlyInput = $("#debtBatchUsdOnly");
  const mixedSypInput = $("#debtBatchMixedSyp");
  const mixedUsdInput = $("#debtBatchMixedUsd");
  const sepSypInput = $("#debtBatchSeparateSyp");
  const sepUsdInput = $("#debtBatchSeparateUsd");

  const settlementRowsEl = $("#centralSettlementRows");
  const emptyRowEl = () => $("#centralSettlementEmptyRow");

  const coverTypeRadios = $$('input[name="debtBatchCoverType"]');
  const methodRadios = $$('input[name="debtBatchPaymentMethod"]');
  const methodPanels = $$("[data-method-panel]");

  if (!displayCurrencySelect || !settleTotalEl || !settleCurLabel || !startBtn || !formEl) {
    return;
  }

  const toNum = (v) => {
    const n = Number(String(v ?? "").trim().replace(",", "."));
    return Number.isFinite(n) ? n : 0;
  };

  const fmtNum = (v, maxFractionDigits = 3) => {
    const n = Number(v);
    if (!Number.isFinite(n)) return "-";
    return new Intl.NumberFormat(undefined, {
      minimumFractionDigits: 0,
      maximumFractionDigits: maxFractionDigits,
    }).format(n);
  };

  const state = {
    remainingSyp: toNum(CFG.remainingSyp),
    remainingUsd: toNum(CFG.remainingUsd),
    fxCurrent: toNum(CFG.fxCurrent),
    debtStatus: String(CFG.debtStatus || "open").toLowerCase(),
    formEnabled: false,
    busy: false,
  };

  const hasFx = () => state.fxCurrent > 0;

  const statusText = (status) => (status === "closed" ? "مغلق" : "مفتوح");

  const selectedCoverType = () =>
    coverTypeRadios.find((r) => r.checked)?.value || "full";

  const selectedMethod = () =>
    methodRadios.find((r) => r.checked)?.value || "syp_only";

  const getCsrf = () => {
    const m = document.cookie.match(/(?:^|;)\s*csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  };

  const settlementTotalSyp = (paidSyp, paidUsd) => {
    if (paidUsd > EPS && !hasFx()) return null;
    return paidSyp + (paidUsd * state.fxCurrent);
  };

  const fullRemainingTotalSyp = () => settlementTotalSyp(state.remainingSyp, state.remainingUsd);
  const fullRemainingTotalUsd = () => {
    if (state.remainingSyp > EPS && !hasFx()) return null;
    return state.remainingUsd + (state.remainingSyp / state.fxCurrent);
  };

  const computeDisplayTotals = () => {
    const remSyp = state.remainingSyp;
    const remUsd = state.remainingUsd;

    if (remSyp <= EPS && remUsd <= EPS) {
      return { totalSyp: 0, totalUsd: 0 };
    }

    if (!hasFx()) {
      return {
        totalSyp: remUsd <= EPS ? remSyp : null,
        totalUsd: remSyp <= EPS ? remUsd : null,
      };
    }

    return {
      totalSyp: remSyp + (remUsd * state.fxCurrent),
      totalUsd: remUsd + (remSyp / state.fxCurrent),
    };
  };

  const fmtSettlementTotal = (v) => {
    const n = Number(v);
    if (!Number.isFinite(n)) return "-";
    const isInt = Math.abs(n - Math.trunc(n)) <= EPS;
    return new Intl.NumberFormat(undefined, {
      minimumFractionDigits: isInt ? 0 : 2,
      maximumFractionDigits: 2,
    }).format(n);
  };

  const updateSettlementDisplay = () => {
    const cur = String(displayCurrencySelect.value || "SYP").toUpperCase();
    const totals = computeDisplayTotals();
    const raw = cur === "USD" ? totals.totalUsd : totals.totalSyp;
    settleCurLabel.textContent = cur;
    settleTotalEl.textContent = raw == null ? "-" : fmtSettlementTotal(raw);
  };

  const setError = (msg) => {
    if (errorEl) errorEl.textContent = msg || "";
  };

  const setInput = (el, value) => {
    if (!el) return;
    const n = Number(value);
    el.value = Number.isFinite(n) ? String(n) : "0";
  };

  const toggleMethodPanel = () => {
    const method = selectedMethod();
    methodPanels.forEach((panel) => {
      panel.classList.toggle("active", panel.dataset.methodPanel === method);
    });
  };

  const syncMethodConstraints = () => {
    const coverType = selectedCoverType();
    const isPartial = coverType === "partial";
    if (separateRadio) {
      separateRadio.disabled = isPartial;
      if (isPartial && separateRadio.checked) {
        const fallback = methodRadios.find((r) => r.value === "mixed")
          || methodRadios.find((r) => r.value === "syp_only");
        if (fallback) fallback.checked = true;
      }
    }
    if (separateLabel) separateLabel.classList.toggle("is-disabled", isPartial);
  };

  const fillDefaultsForMode = () => {
    const coverType = selectedCoverType();
    const method = selectedMethod();
    const totalSyp = fullRemainingTotalSyp();
    const totalUsd = fullRemainingTotalUsd();

    if (sepSypInput) setInput(sepSypInput, state.remainingSyp);
    if (sepUsdInput) setInput(sepUsdInput, state.remainingUsd);

    if (coverType === "full") {
      if (method === "syp_only" && totalSyp != null) setInput(sypOnlyInput, totalSyp);
      if (method === "usd_only" && totalUsd != null) setInput(usdOnlyInput, totalUsd);
      if (method === "mixed") {
        if (mixedSypInput && mixedUsdInput) {
          const curSyp = toNum(mixedSypInput.value);
          const curUsd = toNum(mixedUsdInput.value);
          if (curSyp <= EPS && curUsd <= EPS) {
            setInput(mixedSypInput, state.remainingSyp);
            setInput(mixedUsdInput, state.remainingUsd);
          }
        }
      }
      return;
    }

  };

  const readPaymentAmounts = () => {
    const method = selectedMethod();
    const coverType = selectedCoverType();
    const totalSyp = fullRemainingTotalSyp();
    const totalUsd = fullRemainingTotalUsd();
    let paidSyp = 0;
    let paidUsd = 0;

    if (method === "syp_only") {
      paidSyp = toNum(sypOnlyInput?.value);
      if (coverType === "full" && paidSyp <= EPS && totalSyp != null) paidSyp = totalSyp;
    } else if (method === "usd_only") {
      paidUsd = toNum(usdOnlyInput?.value);
      if (coverType === "full" && paidUsd <= EPS && totalUsd != null) paidUsd = totalUsd;
    } else if (method === "separate") {
      paidSyp = state.remainingSyp;
      paidUsd = state.remainingUsd;
    } else {
      paidSyp = toNum(mixedSypInput?.value);
      paidUsd = toNum(mixedUsdInput?.value);
      if (coverType === "full" && paidSyp <= EPS && paidUsd <= EPS) {
        paidSyp = state.remainingSyp;
        paidUsd = state.remainingUsd;
      }
    }

    return { method, coverType, paidSyp, paidUsd };
  };

  const validatePayload = () => {
    if (state.debtStatus === "closed") return { ok: false, error: "الدين مغلق." };

    const moneyContainerId = Number(containerSelect?.value || 0);
    if (!moneyContainerId) return { ok: false, error: "اختر حاوية المال أولاً." };

    const { method, coverType, paidSyp, paidUsd } = readPaymentAmounts();
    if (paidSyp < 0 || paidUsd < 0) {
      return { ok: false, error: "لا يمكن إدخال قيم سالبة." };
    }

    if (coverType === "partial" && method === "separate") {
      return { ok: false, error: "طريقة separate غير متاحة مع الدفعة الجزئية." };
    }
    if (method === "syp_only" && paidUsd > EPS) {
      return { ok: false, error: "طريقة SYP only تتطلب USD = 0." };
    }
    if (method === "usd_only" && paidSyp > EPS) {
      return { ok: false, error: "طريقة USD only تتطلب SYP = 0." };
    }
    if (coverType === "partial" && method === "mixed" && (paidSyp <= EPS || paidUsd <= EPS)) {
      return { ok: false, error: "في mixed الجزئي يجب إدخال مبلغين SYP و USD." };
    }

    const remainingTotal = fullRemainingTotalSyp();
    const paidTotal = settlementTotalSyp(paidSyp, paidUsd);
    if (remainingTotal == null || paidTotal == null) {
      return { ok: false, error: "تعذر حساب التحويل بسبب غياب سعر الصرف الحالي." };
    }

    if (coverType === "partial") {
      if (paidTotal <= EPS) return { ok: false, error: "الدفعة الجزئية يجب أن تكون أكبر من الصفر." };
      if (paidTotal > remainingTotal + EPS) {
        return { ok: false, error: "لا يمكن أن تتجاوز الدفعة الجزئية كامل المتبقي." };
      }
      if (Math.abs(paidTotal - remainingTotal) <= EPS) {
        return { ok: false, error: "هذه تسوية كاملة، اختر تغطية كاملة." };
      }
    } else {
      if (Math.abs(paidTotal - remainingTotal) > 0.01) {
        return { ok: false, error: "التغطية الكاملة يجب أن تصفر كامل المتبقي." };
      }
    }

    return {
      ok: true,
      payload: {
        cover_type: coverType,
        payment_method: method,
        money_container_id: moneyContainerId,
        paid_syp: String(paidSyp),
        paid_usd: String(paidUsd),
      },
    };
  };

  const setFormEnabled = (enabled) => {
    state.formEnabled = !!enabled;
    formEl.classList.toggle("is-disabled", !state.formEnabled);
    formEl.setAttribute("aria-disabled", state.formEnabled ? "false" : "true");
    if (!state.formEnabled) {
      setError("");
    }
  };

  const appendSettlementRow = (row) => {
    if (!settlementRowsEl || !row) return;
    const empty = emptyRowEl();
    if (empty) empty.remove();

    const tr = document.createElement("tr");
    const date = row.created_at ? new Date(row.created_at) : null;
    const dateText = date && !Number.isNaN(date.getTime())
      ? date.toLocaleString()
      : "-";

    const cells = [
      dateText,
      fmtNum(row.payment_syp),
      fmtNum(row.payment_usd),
      fmtNum(row.applied_syp),
      fmtNum(row.applied_usd),
      row.money_container_name || "-",
      row.receipt_serial || "-",
      row.actor_username || "-",
    ];
    cells.forEach((value) => {
      const td = document.createElement("td");
      td.textContent = value;
      tr.appendChild(td);
    });
    settlementRowsEl.prepend(tr);
  };

  const syncStatusUi = () => {
    const isClosed = state.debtStatus === "closed";
    if (statusTextEl) statusTextEl.textContent = statusText(state.debtStatus);
    if (statusBadgeEl) {
      statusBadgeEl.classList.toggle("is-open", !isClosed);
      statusBadgeEl.classList.toggle("is-closed", isClosed);
    }
    if (zoneEl) {
      zoneEl.classList.toggle("is-open", !isClosed);
      zoneEl.classList.toggle("is-closed", isClosed);
    }
    if (startBtn) startBtn.disabled = isClosed || state.busy;
  };

  const syncRemainingUi = () => {
    if (remainingSypEl) remainingSypEl.textContent = fmtNum(state.remainingSyp);
    if (remainingUsdEl) remainingUsdEl.textContent = fmtNum(state.remainingUsd);
  };

  const syncTotalsFromPayload = (settlementUi) => {
    if (!settlementUi) return;
    const nextFx = toNum(settlementUi.fx_syp_per_usd_current);
    if (nextFx > 0) state.fxCurrent = nextFx;
    if (settlementUi.default_currency && displayCurrencySelect) {
      displayCurrencySelect.value = settlementUi.default_currency;
    }
    updateSettlementDisplay();
  };

  const applySettlementResponse = (payload) => {
    if (!payload || !payload.debt) return;
    state.remainingSyp = toNum(payload.debt.remaining_syp);
    state.remainingUsd = toNum(payload.debt.remaining_usd);
    state.debtStatus = String(payload.debt.status || state.debtStatus).toLowerCase();

    syncRemainingUi();
    syncStatusUi();
    syncTotalsFromPayload(payload.settlement_ui || {});
    appendSettlementRow(payload.settlement || {});

    setFormEnabled(false);
  };

  const submitSettlement = async () => {
    if (state.busy) return;
    const check = validatePayload();
    if (!check.ok) {
      setError(check.error || "بيانات غير صالحة.");
      return;
    }
    setError("");
    state.busy = true;
    syncStatusUi();
    if (submitBtn) submitBtn.disabled = true;

    try {
      const response = await fetch(CFG.settleUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": getCsrf(),
          "Accept": "application/json",
        },
        body: JSON.stringify(check.payload),
      });
      const data = await response.json().catch(() => ({ ok: false, error: "server error" }));
      if (!response.ok || !data.ok) {
        setError(data.error || "فشل إضافة الدفعة.");
        return;
      }
      applySettlementResponse(data);
    } catch (err) {
      setError("فشل الاتصال بالخادم.");
    } finally {
      state.busy = false;
      syncStatusUi();
      if (submitBtn) submitBtn.disabled = false;
    }
  };

  const syncFormUi = () => {
    syncMethodConstraints();
    toggleMethodPanel();
    fillDefaultsForMode();
    setError("");
  };

  displayCurrencySelect.addEventListener("change", updateSettlementDisplay);
  coverTypeRadios.forEach((r) => r.addEventListener("change", syncFormUi));
  methodRadios.forEach((r) => r.addEventListener("change", syncFormUi));
  [sypOnlyInput, usdOnlyInput, mixedSypInput, mixedUsdInput].forEach((el) => {
    if (!el) return;
    el.addEventListener("input", () => setError(""));
  });

  startBtn.addEventListener("click", () => {
    if (state.debtStatus === "closed") return;
    setFormEnabled(true);
    syncFormUi();
  });

  cancelBtn?.addEventListener("click", () => {
    setFormEnabled(false);
  });
  submitBtn?.addEventListener("click", submitSettlement);

  syncRemainingUi();
  syncStatusUi();
  updateSettlementDisplay();
  setFormEnabled(false);
})();
