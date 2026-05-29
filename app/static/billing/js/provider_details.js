(() => {
  const analysisDataNode = document.getElementById("provider-analysis-data");
  let analysisPanel = null;
  if (analysisDataNode) {
    try {
      analysisPanel = JSON.parse(analysisDataNode.textContent || "{}");
    } catch (_) {
      analysisPanel = null;
    }
  }
  const analysisActivities = (analysisPanel && analysisPanel.activities) || {};

  const analysisChooserList = document.getElementById("analysis-chooser-list");
  const analysisGroups = Array.from(document.querySelectorAll("[data-analysis-group]"));
  const analysisOptions = Array.from(document.querySelectorAll("[data-analysis-option]"));
  const detailsBox = document.getElementById("analysis-details-box");
  const visualBox = document.getElementById("analysis-visual-box");
  const detailsLoading = document.getElementById("analysis-details-loading");
  const visualLoading = document.getElementById("analysis-visual-loading");
  const DETAILS_IDLE_MESSAGE = "اختر بنداً من القائمة لعرض التفاصيل";
  const VISUAL_IDLE_MESSAGE = "اختر بنداً من القائمة لعرض الرسم أو التحليل";

  function num(val) {
    const v = Number(val);
    return Number.isFinite(v) ? v : 0;
  }

  function fmtPercent(val) {
    return `${num(val).toFixed(2)}%`;
  }

  function fmtQty(val) {
    return num(val).toFixed(3);
  }

  function fmtMoney(val) {
    return num(val).toFixed(2);
  }

  function escapeHtml(raw) {
    return String(raw || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/\"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function showLoading(loadingNode, show) {
    if (!loadingNode) return;
    loadingNode.hidden = !show;
  }

  function renderDetailsIdle() {
    if (!detailsBox) return;
    detailsBox.innerHTML = `<div class="analysis-empty">${DETAILS_IDLE_MESSAGE}</div>`;
  }

  function renderVisualIdle() {
    if (!visualBox) return;
    visualBox.innerHTML = `<div class="analysis-empty">${VISUAL_IDLE_MESSAGE}</div>`;
  }

  function renderDetails(activity) {
    if (!detailsBox) return;
    if (!activity || !activity.details_kind) {
      renderDetailsIdle();
      return;
    }
    const details = activity.details || null;
    if (!details) {
      detailsBox.innerHTML = '<div class="analysis-empty">لا توجد بيانات كافية</div>';
      return;
    }

    if (activity.details_kind === "totals") {
      detailsBox.innerHTML = `
        <div class="analysis-cards-grid">
          <div class="analysis-mini-card">
            <div class="analysis-mini-label">عدد عمليات هذا المورد</div>
            <div class="analysis-mini-value">${escapeHtml(details.provider_count)}</div>
          </div>
          <div class="analysis-mini-card">
            <div class="analysis-mini-label">إجمالي العمليات لكل الموردين</div>
            <div class="analysis-mini-value">${escapeHtml(details.total_count)}</div>
          </div>
          <div class="analysis-mini-card">
            <div class="analysis-mini-label">نسبة هذا المورد</div>
            <div class="analysis-mini-value">${fmtPercent(details.provider_percent)}</div>
          </div>
          <div class="analysis-mini-card">
            <div class="analysis-mini-label">نسبة باقي الموردين</div>
            <div class="analysis-mini-value">${fmtPercent(details.others_percent)}</div>
          </div>
        </div>
        ${details.metric_note ? `<div class="analysis-separator"></div><p class="analysis-note">${escapeHtml(details.metric_note)}</p>` : ""}
      `;
      return;
    }

    if (activity.details_kind === "latest-purchase") {
      detailsBox.innerHTML = `
        <div class="analysis-detail-groups">
          <div class="analysis-detail-group">
            <div class="analysis-detail-group-title">هوية العملية</div>
            <div class="analysis-cards-grid">
              <div class="analysis-mini-card"><div class="analysis-mini-label">رقم الفاتورة</div><div class="analysis-mini-value">${escapeHtml(details.public_id || "—")}</div></div>
              <div class="analysis-mini-card"><div class="analysis-mini-label">التاريخ</div><div class="analysis-mini-value">${escapeHtml(details.created_at || "—")}</div></div>
            </div>
            ${details.view_url ? `<div class="analysis-detail-group-action"><a class="inline-link" href="${escapeHtml(details.view_url)}">عرض العملية</a></div>` : ""}
          </div>
          <div class="analysis-detail-group">
            <div class="analysis-detail-group-title">الملخص</div>
            <div class="analysis-cards-grid">
              <div class="analysis-mini-card"><div class="analysis-mini-label">عدد المنتجات</div><div class="analysis-mini-value">${escapeHtml(details.items_count || 0)}</div></div>
              <div class="analysis-mini-card"><div class="analysis-mini-label">الحالة الحالية</div><div class="analysis-mini-value">${escapeHtml(details.status_current_label || "—")}</div></div>
            </div>
          </div>
          <div class="analysis-detail-group">
            <div class="analysis-detail-group-title">الإجماليات</div>
            <div class="analysis-row"><span class="label">إجمالي SYP</span><span class="value">${fmtMoney(details.total_syp)}</span></div>
            <div class="analysis-row"><span class="label">إجمالي USD</span><span class="value">${fmtMoney(details.total_usd)}</span></div>
          </div>
        </div>
      `;
      return;
    }

    if (activity.details_kind === "latest-return") {
      detailsBox.innerHTML = `
        <div class="analysis-detail-groups">
          <div class="analysis-detail-group">
            <div class="analysis-detail-group-title">هوية العملية</div>
            <div class="analysis-cards-grid">
              <div class="analysis-mini-card"><div class="analysis-mini-label">رقم الإرجاع</div><div class="analysis-mini-value">${escapeHtml(details.public_id || "—")}</div></div>
              <div class="analysis-mini-card"><div class="analysis-mini-label">التاريخ</div><div class="analysis-mini-value">${escapeHtml(details.created_at || "—")}</div></div>
            </div>
            ${details.view_url ? `<div class="analysis-detail-group-action"><a class="inline-link" href="${escapeHtml(details.view_url)}">عرض العملية</a></div>` : ""}
          </div>
          <div class="analysis-detail-group">
            <div class="analysis-detail-group-title">الملخص</div>
            <div class="analysis-cards-grid">
              <div class="analysis-mini-card"><div class="analysis-mini-label">عدد المنتجات</div><div class="analysis-mini-value">${escapeHtml(details.items_count || 0)}</div></div>
              <div class="analysis-mini-card"><div class="analysis-mini-label">الحالة الحالية</div><div class="analysis-mini-value">${escapeHtml(details.status_label || "—")}</div></div>
            </div>
          </div>
          <div class="analysis-detail-group">
            <div class="analysis-detail-group-title">الإجماليات</div>
            <div class="analysis-row"><span class="label">إجمالي SYP</span><span class="value">${fmtMoney(details.total_syp)}</span></div>
            <div class="analysis-row"><span class="label">إجمالي USD</span><span class="value">${fmtMoney(details.total_usd)}</span></div>
          </div>
        </div>
      `;
      return;
    }

    if (activity.details_kind === "latest-debt") {
      detailsBox.innerHTML = `
        <div class="analysis-cards-grid">
          <div class="analysis-mini-card"><div class="analysis-mini-label">رقم الدين</div><div class="analysis-mini-value">${escapeHtml(details.public_id || "—")}</div></div>
          <div class="analysis-mini-card"><div class="analysis-mini-label">التاريخ</div><div class="analysis-mini-value">${escapeHtml(details.created_at || "—")}</div></div>
          <div class="analysis-mini-card"><div class="analysis-mini-label">الاتجاه</div><div class="analysis-mini-value">${escapeHtml(details.direction_label || "—")}</div></div>
          <div class="analysis-mini-card"><div class="analysis-mini-label">الحالة</div><div class="analysis-mini-value">${escapeHtml(details.status_label || "—")}</div></div>
        </div>
        <div class="analysis-separator"></div>
        <div class="analysis-row"><span class="label">السبب</span><span class="value rtl">${escapeHtml(details.cause_label || "—")}</span></div>
        <div class="analysis-row"><span class="label">المبلغ SYP</span><span class="value">${fmtMoney(details.total_syp)}</span></div>
        <div class="analysis-row"><span class="label">المبلغ USD</span><span class="value">${fmtMoney(details.total_usd)}</span></div>
        ${details.reason_note ? `<div class="analysis-row"><span class="label">ملاحظة</span><span class="value rtl">${escapeHtml(details.reason_note)}</span></div>` : ""}
        ${details.view_url ? `<div class="analysis-separator"></div><a class="inline-link" href="${escapeHtml(details.view_url)}">عرض العملية</a>` : ""}
      `;
      return;
    }

    if (activity.details_kind === "latest-payment" || activity.details_kind === "latest-collection") {
      detailsBox.innerHTML = `
        <div class="analysis-cards-grid">
          <div class="analysis-mini-card"><div class="analysis-mini-label">التاريخ</div><div class="analysis-mini-value">${escapeHtml(details.created_at || "—")}</div></div>
          <div class="analysis-mini-card"><div class="analysis-mini-label">العملة</div><div class="analysis-mini-value">${escapeHtml(details.currency_label || "—")}</div></div>
          <div class="analysis-mini-card"><div class="analysis-mini-label">المبلغ</div><div class="analysis-mini-value">${fmtMoney(details.amount_value)}</div></div>
          <div class="analysis-mini-card"><div class="analysis-mini-label">المصدر</div><div class="analysis-mini-value">${escapeHtml(details.source_label || "—")}</div></div>
        </div>
        <div class="analysis-separator"></div>
        <div class="analysis-row"><span class="label">المبلغ SYP</span><span class="value">${fmtMoney(details.amount_syp)}</span></div>
        <div class="analysis-row"><span class="label">المبلغ USD</span><span class="value">${fmtMoney(details.amount_usd)}</span></div>
        <div class="analysis-row"><span class="label">الحاوية المالية</span><span class="value rtl">${escapeHtml(details.money_container_name || "لا توجد بيانات كافية")}</span></div>
        <div class="analysis-row"><span class="label">العملية المرتبطة</span><span class="value rtl">${escapeHtml(details.linked_reference || "لا توجد بيانات كافية")}</span></div>
        ${details.view_url ? `<div class="analysis-separator"></div><a class="inline-link" href="${escapeHtml(details.view_url)}">عرض العملية</a>` : ""}
      `;
      return;
    }

    if (activity.details_kind === "top-products") {
      const rows = (details.items || []).map((item) => `
        <div class="analysis-list-row">
          <div class="analysis-list-main">
            <div class="analysis-list-title">${escapeHtml(item.product_name)}</div>
            <div class="analysis-list-sub">الكود: ${escapeHtml(item.product_code || "—")} | الكمية: ${fmtQty(item.total_qty)}</div>
          </div>
          ${item.view_url ? `<a class="inline-link" href="${escapeHtml(item.view_url)}">عرض المنتج</a>` : ""}
        </div>
      `).join("");
      detailsBox.innerHTML = rows
        ? `<div class="analysis-list">${rows}</div>`
        : '<div class="analysis-empty">لا توجد بيانات كافية</div>';
      return;
    }

    if (activity.details_kind === "visit-frequency") {
      const sources = (details.sources || []).map((s) => `<span class="analysis-badge">${escapeHtml(s)}</span>`).join("");
      detailsBox.innerHTML = `
        <div class="analysis-cards-grid">
          <div class="analysis-mini-card"><div class="analysis-mini-label">أكثر يوم تعامل</div><div class="analysis-mini-value">${escapeHtml(details.top_weekday || "لا توجد بيانات كافية")}</div></div>
          <div class="analysis-mini-card"><div class="analysis-mini-label">أكثر وقت تعامل</div><div class="analysis-mini-value">${escapeHtml(details.top_time || "لا توجد بيانات كافية")}</div></div>
          <div class="analysis-mini-card"><div class="analysis-mini-label">عدد العمليات التي تم تحليلها</div><div class="analysis-mini-value">${escapeHtml(details.operations_count || 0)}</div></div>
        </div>
        <div class="analysis-separator"></div>
        <p class="analysis-note">${escapeHtml(details.note || "القيم تقريبية حسب العمليات المسجلة")}</p>
        ${sources ? `<div class="analysis-separator"></div><div class="analysis-badges">${sources}</div>` : ""}
      `;
      return;
    }

    detailsBox.innerHTML = '<div class="analysis-empty">لا توجد بيانات كافية</div>';
  }

  function renderDonut(segments, centerText) {
    const safeSegments = Array.isArray(segments) ? segments : [];
    if (!safeSegments.length) {
      return '<div class="analysis-empty">لا توجد بيانات كافية</div>';
    }
    let from = 0;
    const gradParts = [];
    safeSegments.forEach((segment) => {
      const pct = Math.max(0, Math.min(100, num(segment.percent)));
      const to = from + pct;
      gradParts.push(`${segment.color} ${from}% ${to}%`);
      from = to;
    });
    const gradient = gradParts.join(", ");
    const legend = safeSegments.map((segment) => `
      <div class="visual-legend-item">
        <span class="visual-legend-main">
          <span class="visual-dot" style="background:${escapeHtml(segment.color)}"></span>
          <span>${escapeHtml(segment.label)}</span>
        </span>
        <span class="visual-percent">${fmtPercent(segment.percent)}</span>
      </div>
    `).join("");
    return `
      <div class="analysis-visual-wrap">
        <div class="donut-chart" style="background:conic-gradient(${gradient});">
          <div class="donut-hole">${escapeHtml(centerText || "توزيع النسب")}</div>
        </div>
        <div class="visual-legend">${legend}</div>
      </div>
    `;
  }

  function renderBars(rows) {
    const safeRows = Array.isArray(rows) ? rows : [];
    const maxCount = Math.max(...safeRows.map((r) => num(r.count)), 0);
    return `
      <div class="bars-list">
        ${safeRows.map((row) => {
          const percent = maxCount > 0 ? (num(row.count) / maxCount) * 100 : 0;
          return `
            <div class="bar-row">
              <span class="bar-label">${escapeHtml(row.label)}</span>
              <span class="bar-track"><span class="bar-fill" style="width:${percent.toFixed(2)}%"></span></span>
              <span class="bar-count">${escapeHtml(row.count)}</span>
            </div>
          `;
        }).join("")}
      </div>
    `;
  }

  function renderVisual(activity) {
    if (!visualBox) return;
    if (!activity || !activity.visual_kind) {
      renderVisualIdle();
      return;
    }
    if (activity.visual_kind === "none") {
      visualBox.innerHTML = '<div class="analysis-empty">لا يوجد رسم بياني لهذا العنصر</div>';
      return;
    }
    if (activity.visual_kind === "donut-two" || activity.visual_kind === "donut-multi") {
      const segments = (activity.visual && activity.visual.segments) || [];
      visualBox.innerHTML = renderDonut(segments, "توزيع النسب");
      return;
    }
    if (activity.visual_kind === "bars-toggle") {
      const data = activity.visual || {};
      const days = data.days || [];
      const times = data.times || [];
      visualBox.innerHTML = `
        <div class="analysis-visual-wrap">
          <div id="visit-bars-holder">${renderBars(days)}</div>
          <div class="bars-toggle">
            <button type="button" data-visit-toggle="days" class="active">الأيام</button>
            <button type="button" data-visit-toggle="times">الأوقات</button>
          </div>
        </div>
      `;
      const holder = visualBox.querySelector("#visit-bars-holder");
      const toggleBtns = Array.from(visualBox.querySelectorAll("[data-visit-toggle]"));
      toggleBtns.forEach((btn) => {
        btn.addEventListener("click", () => {
          const mode = btn.getAttribute("data-visit-toggle") || "days";
          toggleBtns.forEach((x) => x.classList.toggle("active", x === btn));
          if (holder) {
            holder.innerHTML = mode === "times" ? renderBars(times) : renderBars(days);
          }
        });
      });
      return;
    }
    visualBox.innerHTML = '<div class="analysis-empty">لا يوجد رسم بياني لهذا العنصر</div>';
  }

  function updateActiveOption(activeKey) {
    analysisOptions.forEach((btn) => {
      const key = btn.getAttribute("data-analysis-option") || "";
      const active = key === activeKey;
      btn.classList.toggle("active", active);
      btn.setAttribute("aria-pressed", active ? "true" : "false");
    });
  }

  function setGroupExpanded(groupNode, expanded) {
    if (!groupNode) return;
    const toggleNode = groupNode.querySelector("[data-analysis-group-toggle]");
    const optionsNode = groupNode.querySelector(".analysis-group-options");
    groupNode.classList.toggle("is-expanded", expanded);
    groupNode.classList.toggle("is-collapsed", !expanded);
    groupNode.setAttribute("data-expanded", expanded ? "true" : "false");
    if (toggleNode) {
      toggleNode.setAttribute("aria-expanded", expanded ? "true" : "false");
    }
    if (optionsNode) {
      optionsNode.hidden = !expanded;
    }
  }

  function collapseAllGroups(expandKey = "") {
    analysisGroups.forEach((groupNode) => {
      const groupKey = groupNode.getAttribute("data-analysis-group") || "";
      const isExpanded = !!expandKey && groupKey === expandKey;
      setGroupExpanded(groupNode, isExpanded);
    });
  }

  function setIdleState() {
    updateActiveOption("");
    renderDetailsIdle();
    renderVisualIdle();
  }

  function renderActivity(key) {
    if (!key) {
      setIdleState();
      return;
    }
    const activity = analysisActivities[key] || null;
    updateActiveOption(key);
    showLoading(detailsLoading, true);
    showLoading(visualLoading, true);
    window.setTimeout(() => {
      renderDetails(activity);
      renderVisual(activity);
      showLoading(detailsLoading, false);
      showLoading(visualLoading, false);
    }, 140);
  }

  analysisOptions.forEach((btn) => {
    btn.addEventListener("click", () => {
      const key = btn.getAttribute("data-analysis-option") || "";
      const groupNode = btn.closest("[data-analysis-group]");
      const groupKey = groupNode ? (groupNode.getAttribute("data-analysis-group") || "") : "";
      if (groupKey) {
        collapseAllGroups(groupKey);
      }
      renderActivity(key);
    });
  });

  if (analysisChooserList) {
    analysisChooserList.addEventListener("click", (event) => {
      const target = event.target;
      if (!(target instanceof Element)) return;
      const toggleNode = target.closest("[data-analysis-group-toggle]");
      if (!toggleNode || !analysisChooserList.contains(toggleNode)) return;
      event.preventDefault();
      const groupKey = toggleNode.getAttribute("data-analysis-group-toggle") || "";
      const groupNode = toggleNode.closest("[data-analysis-group]");
      if (!groupNode) return;
      const alreadyExpanded = groupNode.getAttribute("data-expanded") === "true";
      if (alreadyExpanded) {
        collapseAllGroups("");
        return;
      }
      collapseAllGroups(groupKey);
    });
  }

  collapseAllGroups("");
  setIdleState();

  const phonesWrap = document.getElementById("provider-phones-wrap");
  const phonesEmptyState = document.getElementById("phones-empty-state");
  const addPhoneBtn = document.getElementById("btn-show-add-phone");
  const addPhoneRow = document.getElementById("phone-add-row");
  const addPhoneInput = document.getElementById("phone-add-input");
  const addPhoneForm = document.getElementById("phone-add-form");
  const addPhoneCancelBtn = document.getElementById("phone-add-cancel-btn");
  const phoneSuccessMessage = document.getElementById("phone-success-message");

  const deleteModal = document.getElementById("phone-delete-modal");
  const deleteConfirmBtn = document.getElementById("phone-delete-confirm-btn");
  const deleteCancelBtn = document.getElementById("phone-delete-cancel-btn");

  const phoneRows = Array.from(document.querySelectorAll('[data-phone-row="existing"]'));

  let activeRow = null;
  let activeMode = "idle";
  let pendingDeleteForm = null;

  if (phoneSuccessMessage) {
    window.setTimeout(() => {
      phoneSuccessMessage.remove();
    }, 2000);
  }

  function rowNodes(row) {
    if (!row) return null;
    return {
      form: row.querySelector("[data-phone-row-form]"),
      input: row.querySelector("[data-phone-input]"),
      defaultBtn: row.querySelector("[data-phone-default-btn]"),
      actionGroup: row.querySelector("[data-phone-action-group]"),
      editGroup: row.querySelector("[data-phone-edit-group]"),
      editIcon: row.querySelector("[data-phone-edit-icon]"),
      deleteIcon: row.querySelector("[data-phone-delete-icon]"),
      actionCancel: row.querySelector("[data-phone-action-cancel-btn]"),
      saveBtn: row.querySelector("[data-phone-save-btn]"),
      editCancel: row.querySelector("[data-phone-edit-cancel-btn]"),
      deleteForm: row.querySelector("[data-phone-delete-form]"),
    };
  }

  function hasExistingPhoneRows() {
    if (!phonesWrap) return false;
    return phonesWrap.querySelectorAll('[data-phone-row="existing"]').length > 0;
  }

  function refreshEmptyState() {
    if (!phonesEmptyState) return;
    const hasAddOpen = addPhoneRow && !addPhoneRow.hidden;
    phonesEmptyState.hidden = hasExistingPhoneRows() || hasAddOpen;
  }

  function closeDeleteModal() {
    if (!deleteModal) return;
    deleteModal.hidden = true;
    pendingDeleteForm = null;
  }

  function resetActiveRowToDefault() {
    if (!activeRow) {
      activeMode = "idle";
      applyLockState();
      return;
    }
    const nodes = rowNodes(activeRow);
    if (nodes && nodes.input) {
      nodes.input.value = nodes.input.dataset.originalValue || nodes.input.value;
    }
    setRowMode(activeRow, "default");
    activeRow = null;
    activeMode = "idle";
    applyLockState();
  }

  function openDeleteModal(form) {
    if (!deleteModal) return;
    pendingDeleteForm = form;
    deleteModal.hidden = false;
  }

  function setRowMode(row, mode) {
    const nodes = rowNodes(row);
    if (!nodes) return;

    if (nodes.input) {
      if (!nodes.input.dataset.originalValue) {
        nodes.input.dataset.originalValue = nodes.input.value;
      }
      nodes.input.readOnly = mode !== "edit";
    }
    if (nodes.defaultBtn) nodes.defaultBtn.hidden = mode !== "default";
    if (nodes.actionGroup) nodes.actionGroup.hidden = mode !== "action";
    if (nodes.editGroup) nodes.editGroup.hidden = mode !== "edit";

    if (mode === "edit" && nodes.input) {
      nodes.input.focus();
      nodes.input.select();
    }
  }

  function applyLockState() {
    const isLocked = activeMode !== "idle";

    phoneRows.forEach((row) => {
      const nodes = rowNodes(row);
      if (!nodes) return;
      const isActiveRow = row === activeRow;
      const disableRow = isLocked && !isActiveRow;
      [
        nodes.defaultBtn,
        nodes.editIcon,
        nodes.deleteIcon,
        nodes.actionCancel,
        nodes.saveBtn,
        nodes.editCancel,
      ].forEach((button) => {
        if (!button) return;
        button.disabled = disableRow;
      });
    });

    if (addPhoneBtn) {
      addPhoneBtn.disabled = isLocked;
    }
  }

  phoneRows.forEach((row) => {
    const nodes = rowNodes(row);
    if (!nodes) return;

    setRowMode(row, "default");

    if (nodes.defaultBtn) {
      nodes.defaultBtn.addEventListener("click", () => {
        if (activeMode !== "idle") return;
        activeRow = row;
        activeMode = "action";
        setRowMode(row, "action");
        applyLockState();
      });
    }

    if (nodes.actionCancel) {
      nodes.actionCancel.addEventListener("click", () => {
        if (activeRow !== row) return;
        resetActiveRowToDefault();
      });
    }

    if (nodes.editIcon) {
      nodes.editIcon.addEventListener("click", () => {
        if (activeRow !== row || activeMode !== "action") return;
        activeMode = "edit";
        setRowMode(row, "edit");
        applyLockState();
      });
    }

    if (nodes.editCancel) {
      nodes.editCancel.addEventListener("click", () => {
        if (activeRow !== row || activeMode !== "edit") return;
        resetActiveRowToDefault();
      });
    }

    if (nodes.deleteIcon) {
      nodes.deleteIcon.addEventListener("click", () => {
        if (activeRow !== row || activeMode !== "action") return;
        openDeleteModal(nodes.deleteForm);
      });
    }

    if (nodes.form) {
      nodes.form.addEventListener("submit", (event) => {
        if (activeRow !== row || activeMode !== "edit") {
          event.preventDefault();
        }
      });
    }
  });

  if (deleteConfirmBtn) {
    deleteConfirmBtn.addEventListener("click", () => {
      if (!pendingDeleteForm) return;
      pendingDeleteForm.submit();
    });
  }

  if (deleteCancelBtn) {
    deleteCancelBtn.addEventListener("click", () => {
      closeDeleteModal();
      resetActiveRowToDefault();
    });
  }

  if (deleteModal) {
    deleteModal.addEventListener("click", (event) => {
      if (event.target === deleteModal) {
        closeDeleteModal();
        resetActiveRowToDefault();
      }
    });
  }

  if (addPhoneBtn && addPhoneRow && phonesWrap) {
    addPhoneBtn.addEventListener("click", () => {
      if (activeMode !== "idle") return;
      activeMode = "add";
      activeRow = null;
      applyLockState();
      addPhoneRow.hidden = false;
      phonesWrap.appendChild(addPhoneRow);
      if (addPhoneInput) {
        addPhoneInput.value = "";
        addPhoneInput.focus();
      }
      refreshEmptyState();
    });
  }

  if (addPhoneCancelBtn && addPhoneRow) {
    addPhoneCancelBtn.addEventListener("click", () => {
      addPhoneRow.hidden = true;
      if (addPhoneInput) {
        addPhoneInput.value = "";
      }
      activeMode = "idle";
      activeRow = null;
      applyLockState();
      refreshEmptyState();
    });
  }

  if (addPhoneForm) {
    addPhoneForm.addEventListener("submit", (event) => {
      if (activeMode !== "add") {
        event.preventDefault();
      }
    });
  }

  refreshEmptyState();
  applyLockState();
})();

