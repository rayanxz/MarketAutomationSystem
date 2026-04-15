(function (global) {
  "use strict";

  const NAME_FILTER_KEYS = new Set(["from", "to", "date_from", "date_to"]);
  const ID_FILTER_KEYS = new Set(["from", "to", "ffrom", "fto"]);
  const SEGMENTS = [
    { start: 0, end: 2, len: 2, max: 31, placeholder: "DD" },
    { start: 3, end: 5, len: 2, max: 12, placeholder: "MM" },
    { start: 6, end: 10, len: 4, max: null, placeholder: "YYYY" },
  ];
  const DATE_TEMPLATE = "DD/MM/YYYY";
  const stateByInput = new WeakMap();

  function toStr(v) {
    return String(v == null ? "" : v).trim();
  }

  function pad2(v) {
    return String(v).padStart(2, "0");
  }

  function isDigit(ch) {
    return ch >= "0" && ch <= "9";
  }

  function digitsOnly(v) {
    return toStr(v).replace(/\D/g, "");
  }

  function isDateFilterInput(input) {
    if (!input || input.tagName !== "INPUT") return false;
    if ((input.type || "").toLowerCase() === "hidden") return false;

    const marker = toStr(input.dataset.dateFilter || "").toLowerCase();
    if (marker === "off") return false;
    if (marker === "true") return true;

    const type = (input.type || "").toLowerCase();
    if (type !== "date") return false;

    const name = toStr(input.name).toLowerCase();
    const id = toStr(input.id).toLowerCase();
    return NAME_FILTER_KEYS.has(name) || ID_FILTER_KEYS.has(id);
  }

  function parseDateToken(rawValue) {
    const raw = toStr(rawValue);
    if (!raw) return null;

    let m = raw.match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/);
    if (m) {
      const y = Number(m[1]);
      const mo = Number(m[2]);
      const d = Number(m[3]);
      const dt = new Date(y, mo - 1, d);
      if (
        Number.isFinite(y) && Number.isFinite(mo) && Number.isFinite(d) &&
        dt.getFullYear() === y && dt.getMonth() === (mo - 1) && dt.getDate() === d
      ) {
        return dt;
      }
      return null;
    }

    m = raw.match(/^(\d{1,4})[\/-](\d{1,2})[\/-](\d{1,4})$/);
    if (!m) return null;

    let y;
    let mo;
    let d;
    if (m[1].length === 4) {
      if (m[3].length !== 1 && m[3].length !== 2) return null;
      y = Number(m[1]);
      mo = Number(m[2]);
      d = Number(m[3]);
    } else {
      if (m[3].length !== 4) return null;
      d = Number(m[1]);
      mo = Number(m[2]);
      y = Number(m[3]);
    }

    const dt = new Date(y, mo - 1, d);
    if (
      Number.isFinite(y) && Number.isFinite(mo) && Number.isFinite(d) &&
      dt.getFullYear() === y && dt.getMonth() === (mo - 1) && dt.getDate() === d
    ) {
      return dt;
    }
    return null;
  }

  function formatDDMMYYYY(dateObj) {
    if (!(dateObj instanceof Date)) return "";
    return `${pad2(dateObj.getDate())}/${pad2(dateObj.getMonth() + 1)}/${dateObj.getFullYear()}`;
  }

  function setCaret(input, pos) {
    if (!input || typeof input.setSelectionRange !== "function") return;
    try {
      input.setSelectionRange(pos, pos);
    } catch (_) {
      // Ignore unsupported selection updates.
    }
  }

  function setSelection(input, start, end) {
    if (!input || typeof input.setSelectionRange !== "function") return;
    try {
      input.setSelectionRange(start, end);
    } catch (_) {
      // Ignore unsupported selection updates.
    }
  }

  function dispatchFilterEvents(input) {
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function blankSegments() {
    return ["", "", ""];
  }

  function createState() {
    return {
      segments: blankSegments(),
      activeSeg: 0,
      ignoreInput: false,
      committedValue: "",
    };
  }

  function ensureState(input) {
    let st = stateByInput.get(input);
    if (!st) {
      st = createState();
      stateByInput.set(input, st);
    }
    return st;
  }

  function renderSegmentValue(value, def) {
    const v = digitsOnly(value).slice(0, def.len);
    if (!v) return def.placeholder;
    return `${v}${def.placeholder.slice(v.length)}`;
  }

  function composeDisplay(segments) {
    return `${renderSegmentValue(segments[0], SEGMENTS[0])}/${renderSegmentValue(segments[1], SEGMENTS[1])}/${renderSegmentValue(segments[2], SEGMENTS[2])}`;
  }

  function applyDisplay(input, state) {
    state.ignoreInput = true;
    input.value = composeDisplay(state.segments);
    state.ignoreInput = false;
  }

  function isAllSegmentsEmpty(segments) {
    return !digitsOnly(segments[0]) && !digitsOnly(segments[1]) && !digitsOnly(segments[2]);
  }

  function segmentIndexFromCaret(pos) {
    const p = Math.max(0, Number(pos || 0));
    if (p <= SEGMENTS[0].end) return 0;
    if (p <= SEGMENTS[1].end) return 1;
    return 2;
  }

  function selectSegment(input, idx) {
    const state = ensureState(input);
    const segIdx = Math.max(0, Math.min(2, Number(idx || 0)));
    const def = SEGMENTS[segIdx];
    state.activeSeg = segIdx;
    setSelection(input, def.start, def.end);
  }

  function moveToNextSegment(input) {
    const state = ensureState(input);
    const next = Math.min(2, state.activeSeg + 1);
    selectSegment(input, next);
  }

  function moveToPrevSegment(input) {
    const state = ensureState(input);
    const prev = Math.max(0, state.activeSeg - 1);
    selectSegment(input, prev);
  }

  function parseLooseSegments(rawValue) {
    const raw = toStr(rawValue);
    if (!raw || raw.toUpperCase() === DATE_TEMPLATE) return blankSegments();

    const dt = parseDateToken(raw);
    if (dt) {
      return [pad2(dt.getDate()), pad2(dt.getMonth() + 1), String(dt.getFullYear())];
    }

    const parts = raw.replace(/-/g, "/").split("/").map(digitsOnly);
    const out = blankSegments();
    for (let i = 0; i < 3; i += 1) {
      const part = toStr(parts[i] || "");
      if (!part) continue;
      out[i] = part.slice(0, SEGMENTS[i].len);
    }
    return out;
  }

  function padDayMonth(value) {
    const v = digitsOnly(value).slice(0, 2);
    if (!v) return "";
    if (v.length === 1) return `0${v}`;
    return v;
  }

  function normalizeSegmentValue(idx, value) {
    const def = SEGMENTS[idx];
    let v = digitsOnly(value).slice(0, def.len);
    if (!v) return "";
    if (idx < 2) {
      v = padDayMonth(v);
      const n = Number(v);
      if (!Number.isFinite(n) || n < 1 || n > (def.max || 99)) return "";
      return v;
    }
    return v;
  }

  function hasCompleteSegments(segments) {
    return digitsOnly(segments[0]).length === 2 &&
      digitsOnly(segments[1]).length === 2 &&
      digitsOnly(segments[2]).length === 4;
  }

  function trySegmentsToDate(segments) {
    if (!hasCompleteSegments(segments)) return null;
    const token = `${digitsOnly(segments[0]).padStart(2, "0")}/${digitsOnly(segments[1]).padStart(2, "0")}/${digitsOnly(segments[2]).padStart(4, "0")}`;
    return parseDateToken(token);
  }

  function syncWithFlatpickr(input, opts) {
    const options = opts || {};
    const state = ensureState(input);
    const fp = input._dateFilterFlatpickr || null;
    const dt = trySegmentsToDate(state.segments);
    const allEmpty = isAllSegmentsEmpty(state.segments);

    if (allEmpty) {
      input.setCustomValidity("");
      if (fp && fp.selectedDates && fp.selectedDates.length) {
        fp.clear(false);
      }
      if (options.dispatch && state.committedValue !== "") {
        state.committedValue = "";
        dispatchFilterEvents(input);
      }
      return;
    }

    if (!dt) {
      input.setCustomValidity("Use DD/MM/YYYY");
      return;
    }

    input.setCustomValidity("");
    const normalized = formatDDMMYYYY(dt);
    state.segments = normalized.split("/");
    applyDisplay(input, state);

    if (fp) {
      fp.setDate(dt, false);
    }

    if (options.dispatch && state.committedValue !== normalized) {
      state.committedValue = normalized;
      dispatchFilterEvents(input);
    } else {
      state.committedValue = normalized;
    }
  }

  function setSegmentsFromDate(input, dateObj, dispatchChange) {
    const state = ensureState(input);
    const normalized = formatDDMMYYYY(dateObj);
    state.segments = normalized.split("/");
    applyDisplay(input, state);
    syncWithFlatpickr(input, { dispatch: !!dispatchChange });
  }

  function resetToTemplate(input, dispatchChange) {
    const state = ensureState(input);
    state.segments = blankSegments();
    applyDisplay(input, state);
    state.activeSeg = 0;
    syncWithFlatpickr(input, { dispatch: !!dispatchChange });
    selectSegment(input, 0);
  }

  function finalizeSegment(input, idx) {
    const state = ensureState(input);
    state.segments[idx] = normalizeSegmentValue(idx, state.segments[idx]);
    applyDisplay(input, state);
  }

  function sanitizeSegmentForTyping(idx, value) {
    const def = SEGMENTS[idx];
    const raw = digitsOnly(value).slice(0, def.len);
    if (!raw) return "";
    if (idx < 2 && raw.length === 2) {
      const n = Number(raw);
      if (!Number.isFinite(n) || n > (def.max || 99)) return "";
    }
    return raw;
  }

  function handleSegmentedKeydown(input, e) {
    const state = ensureState(input);
    const key = e.key || "";

    if (e.ctrlKey || e.metaKey || e.altKey) return;

    if (key === "Tab") return;

    if (key === "ArrowLeft") {
      e.preventDefault();
      moveToPrevSegment(input);
      return;
    }
    if (key === "ArrowRight") {
      e.preventDefault();
      moveToNextSegment(input);
      return;
    }
    if (key === "Home") {
      e.preventDefault();
      selectSegment(input, 0);
      return;
    }
    if (key === "End") {
      e.preventDefault();
      selectSegment(input, 2);
      return;
    }

    if (key === "Backspace" || key === "Delete") {
      e.preventDefault();
      state.segments[state.activeSeg] = "";
      applyDisplay(input, state);
      selectSegment(input, state.activeSeg);
      if (isAllSegmentsEmpty(state.segments)) {
        syncWithFlatpickr(input, { dispatch: true });
      }
      return;
    }

    if (key === "Enter") {
      e.preventDefault();
      finalizeSegment(input, state.activeSeg);
      if (state.activeSeg < 2) {
        moveToNextSegment(input);
      } else {
        selectSegment(input, 2);
      }
      syncWithFlatpickr(input, { dispatch: true });
      return;
    }

    if (key.length === 1 && !isDigit(key)) {
      e.preventDefault();
      return;
    }

    if (key.length === 1 && isDigit(key)) {
      e.preventDefault();
      const idx = state.activeSeg;
      const def = SEGMENTS[idx];

      const selStart = input.selectionStart || 0;
      const selEnd = input.selectionEnd || 0;
      const replacingWhole = selStart === def.start && selEnd === def.end;

      const base = replacingWhole ? "" : digitsOnly(state.segments[idx]).slice(0, def.len);
      const candidate = sanitizeSegmentForTyping(idx, `${base}${key}`);
      if (!candidate) return;

      state.segments[idx] = candidate;
      applyDisplay(input, state);

      if (candidate.length >= def.len) {
        if (idx < 2) {
          moveToNextSegment(input);
        } else {
          selectSegment(input, 2);
          syncWithFlatpickr(input, { dispatch: true });
        }
      } else {
        const caretPos = def.start + candidate.length;
        setCaret(input, caretPos);
      }
    }
  }

  function attachSegmentedInput(input) {
    if (!input || input.dataset.dateSegmentedInit === "1") return;
    input.dataset.dateSegmentedInit = "1";

    input.addEventListener("focus", function () {
      const state = ensureState(input);
      const idx = state.activeSeg || 0;
      setTimeout(() => selectSegment(input, idx), 0);
    });

    input.addEventListener("click", function () {
      const idx = segmentIndexFromCaret(input.selectionStart || 0);
      selectSegment(input, idx);
    });

    input.addEventListener("keydown", function (e) {
      handleSegmentedKeydown(input, e);
    });

    input.addEventListener("paste", function (e) {
      e.preventDefault();
      const text = toStr((e.clipboardData && e.clipboardData.getData("text")) || "");
      if (!text) {
        resetToTemplate(input, true);
        return;
      }
      const dt = parseDateToken(text);
      if (dt) {
        setSegmentsFromDate(input, dt, true);
        selectSegment(input, 2);
        return;
      }
      const state = ensureState(input);
      state.segments = parseLooseSegments(text);
      applyDisplay(input, state);
      syncWithFlatpickr(input, { dispatch: true });
      selectSegment(input, state.activeSeg);
    });

    input.addEventListener("input", function () {
      const state = ensureState(input);
      if (state.ignoreInput) return;
      const raw = toStr(input.value);
      if (!raw) {
        resetToTemplate(input, true);
        return;
      }
      const dt = parseDateToken(raw);
      if (dt) {
        setSegmentsFromDate(input, dt, false);
        return;
      }
      state.segments = parseLooseSegments(raw);
      applyDisplay(input, state);
    });

    input.addEventListener("blur", function () {
      const state = ensureState(input);
      finalizeSegment(input, state.activeSeg);
      syncWithFlatpickr(input, { dispatch: true });
    });
  }

  function resolveFlatpickrLocale() {
    const lang = toStr(document.documentElement.lang).toLowerCase();
    if (lang.startsWith("ar") && global.flatpickr && global.flatpickr.l10ns && global.flatpickr.l10ns.ar) {
      return global.flatpickr.l10ns.ar;
    }
    return "default";
  }

  function initialRawValue(input) {
    const live = toStr(input.value);
    if (live) return live;
    return toStr(input.getAttribute("value"));
  }

  function initInput(input) {
    if (!isDateFilterInput(input)) return;
    if (input.dataset.flatpickrInit === "1") return;
    if (!global.flatpickr) return;

    const parsedInitial = parseDateToken(initialRawValue(input));

    input.dataset.flatpickrInit = "1";
    input.dataset.dateFilter = "true";
    input.autocomplete = "off";
    input.inputMode = input.inputMode || "numeric";
    input.placeholder = "";
    attachSegmentedInput(input);

    if ((input.type || "").toLowerCase() === "date") {
      try {
        input.type = "text";
      } catch (_) {
        // Keep current type if browser disallows reassignment.
      }
    }

    const isRtl = toStr(document.documentElement.dir).toLowerCase() === "rtl";

    const fp = global.flatpickr(input, {
      dateFormat: "d/m/Y",
      defaultDate: parsedInitial || null,
      allowInput: true,
      disableMobile: true,
      locale: resolveFlatpickrLocale(),
      position: isRtl ? "auto right" : "auto left",
      onReady: function (_selectedDates, _dateStr, instance) {
        const cal = instance && instance.calendarContainer;
        if (!cal) return;
        cal.classList.add("date-filter-calendar");
        cal.setAttribute("dir", isRtl ? "rtl" : "ltr");
      },
      onChange: function (selectedDates) {
        if (!selectedDates || !selectedDates.length) return;
        setSegmentsFromDate(input, selectedDates[0], true);
      },
      onClose: function () {
        const state = ensureState(input);
        finalizeSegment(input, state.activeSeg);
        syncWithFlatpickr(input, { dispatch: true });
      },
    });
    input._dateFilterFlatpickr = fp;

    if (input.form && input.form.dataset.dateFilterSubmitHook !== "1") {
      input.form.dataset.dateFilterSubmitHook = "1";
      input.form.addEventListener("submit", function () {
        const fields = input.form.querySelectorAll("input[data-date-filter='true']");
        fields.forEach((field) => {
          const st = ensureState(field);
          finalizeSegment(field, st.activeSeg);
          syncWithFlatpickr(field, { dispatch: false });
        });
      });
    }

    if (parsedInitial) {
      setSegmentsFromDate(input, parsedInitial, false);
    } else {
      resetToTemplate(input, false);
    }
  }

  function initWithin(root) {
    const scope = root || document;
    const inputs = scope.querySelectorAll("input");
    inputs.forEach(initInput);
  }

  function startObserver() {
    if (!(global.MutationObserver && document.body)) return;
    const observer = new MutationObserver((mutations) => {
      for (const m of mutations) {
        if (!m.addedNodes || !m.addedNodes.length) continue;
        for (const node of m.addedNodes) {
          if (!node || node.nodeType !== 1) continue;
          if (node.matches && node.matches("input")) {
            initInput(node);
          }
          initWithin(node);
        }
      }
    });
    observer.observe(document.body, { childList: true, subtree: true });
  }

  function bootstrap() {
    initWithin(document);
    startObserver();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bootstrap, { once: true });
  } else {
    setTimeout(bootstrap, 0);
  }

  global.DateFilterFlatpickr = {
    initWithin,
    initInput,
    selectSegment,
    resetToTemplate,
    parseDateToken,
    formatDDMMYYYY,
  };
})(window);
