(() => {
  "use strict";

  if (window.__numericMathFormatterInitialized) return;
  window.__numericMathFormatterInitialized = true;

  const TARGET_SELECTOR = 'input.numeric-math, input[data-math-numeric="1"]';
  const valueDescriptor = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value");
  const nativeGetValue = valueDescriptor && valueDescriptor.get;
  const nativeSetValue = valueDescriptor && valueDescriptor.set;

  if (!nativeGetValue || !nativeSetValue) return;

  function readNativeValue(input) {
    return String(nativeGetValue.call(input) ?? "");
  }

  function writeNativeValue(input, value) {
    nativeSetValue.call(input, String(value ?? ""));
  }

  function allowsDecimal(input) {
    if ((input.dataset.mathDecimal || "").trim() === "1") return true;
    if ((input.dataset.mathDecimal || "").trim() === "0") return false;

    const step = (input.getAttribute("step") || "").trim().toLowerCase();
    if (!step || step === "any") return true;
    return step.includes(".");
  }

  function allowsNegative(input) {
    if ((input.dataset.mathAllowNegative || "").trim() === "1") return true;
    if ((input.dataset.mathAllowNegative || "").trim() === "0") return false;

    const minRaw = (input.getAttribute("min") || "").trim();
    if (!minRaw) return false;
    const minNum = Number(minRaw.replace(/,/g, ""));
    return Number.isFinite(minNum) && minNum < 0;
  }

  function inputMaxDecimals(input) {
    const raw = String(input?.dataset?.mathMaxDecimals ?? "").trim();
    if (!raw) return null;
    const n = Number(raw);
    if (!Number.isFinite(n)) return null;
    return Math.max(0, Math.trunc(n));
  }

  function getOptions(input) {
    return {
      allowDecimal: allowsDecimal(input),
      allowNegative: allowsNegative(input),
      maxDecimals: inputMaxDecimals(input),
    };
  }

  function displayMaxDecimals(input) {
    const raw = String(input?.dataset?.mathDisplayMaxDecimals ?? "").trim();
    if (!raw) return null;
    const n = Number(raw);
    if (!Number.isFinite(n)) return null;
    return Math.max(0, Math.trunc(n));
  }

  function sanitizeRaw(rawValue, options) {
    let value = String(rawValue ?? "");
    if (!value) return "";

    value = value
      .replace(/,/g, "")
      .replace(/\u066C/g, "")
      .replace(/\s+/g, "");

    let negative = false;
    if (options.allowNegative && value.startsWith("-")) {
      negative = true;
      value = value.slice(1);
    }

    value = value.replace(/-/g, "");
    value = value.replace(/[^\d.]/g, "");

    const dotIndex = value.indexOf(".");
    if (dotIndex >= 0) {
      value = value.slice(0, dotIndex + 1) + value.slice(dotIndex + 1).replace(/\./g, "");
    }

    if (!options.allowDecimal && dotIndex >= 0) {
      value = value.slice(0, dotIndex);
    } else if (
      options.allowDecimal &&
      dotIndex >= 0 &&
      Number.isInteger(options.maxDecimals) &&
      options.maxDecimals >= 0
    ) {
      const intPart = value.slice(0, dotIndex);
      const fracPart = value.slice(dotIndex + 1, dotIndex + 1 + options.maxDecimals);
      value = options.maxDecimals === 0 ? intPart : `${intPart}.${fracPart}`;
    }

    if (value.startsWith(".")) value = `0${value}`;
    if (negative && value) value = `-${value}`;
    if (negative && !value) return "-";

    return value;
  }

  function trimFractionZeros(rawValue) {
    const raw = String(rawValue ?? "");
    if (!raw) return "";
    if (raw === "-") return raw;

    let sign = "";
    let numeric = raw;
    if (numeric.startsWith("-")) {
      sign = "-";
      numeric = numeric.slice(1);
    }

    if (!numeric.includes(".")) return raw;

    const split = numeric.split(".");
    const integerPart = split[0];
    const fractionPart = split.slice(1).join("").replace(/0+$/, "");

    if (!fractionPart) return `${sign}${integerPart}`;
    return `${sign}${integerPart}.${fractionPart}`;
  }

  function clampDisplayDecimals(rawValue, maxDecimals) {
    if (maxDecimals == null) return String(rawValue ?? "");
    const raw = String(rawValue ?? "");
    if (!raw || raw === "-") return raw;

    let sign = "";
    let numeric = raw;
    if (numeric.startsWith("-")) {
      sign = "-";
      numeric = numeric.slice(1);
    }

    if (!numeric.includes(".")) return raw;

    const split = numeric.split(".");
    const integerPart = split[0];
    const fractionPart = split.slice(1).join("").slice(0, maxDecimals);
    if (!fractionPart) return `${sign}${integerPart}`;
    return `${sign}${integerPart}.${fractionPart}`;
  }

  function formatDisplay(rawValue, { trimTrailingZeros = false, maxDisplayDecimals = null } = {}) {
    const raw = String(rawValue ?? "");
    if (!raw) return "";

    let normalizedRaw = clampDisplayDecimals(raw, maxDisplayDecimals);
    if (trimTrailingZeros) normalizedRaw = trimFractionZeros(normalizedRaw);

    let sign = "";
    let numeric = normalizedRaw;
    if (numeric.startsWith("-")) {
      sign = "-";
      numeric = numeric.slice(1);
    }

    const hasDot = numeric.includes(".");
    let integerPart = numeric;
    let decimalPart = "";
    if (hasDot) {
      const split = numeric.split(".");
      integerPart = split[0];
      decimalPart = split.slice(1).join("");
    }

    const formattedInteger = integerPart.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
    return hasDot ? `${sign}${formattedInteger}.${decimalPart}` : `${sign}${formattedInteger}`;
  }

  function countRawCharsUntil(displayValue, cursorPosition, options) {
    const display = String(displayValue ?? "");
    const cap = Math.max(0, Math.min(Number(cursorPosition || 0), display.length));
    let rawCount = 0;

    for (let i = 0; i < cap; i += 1) {
      const ch = display[i];
      if (ch >= "0" && ch <= "9") {
        rawCount += 1;
        continue;
      }
      if (ch === "." && options.allowDecimal) {
        rawCount += 1;
        continue;
      }
      if (ch === "-" && options.allowNegative && i === 0) {
        rawCount += 1;
      }
    }

    return rawCount;
  }

  function caretForRawCount(displayValue, rawCount, options) {
    const display = String(displayValue ?? "");
    if (rawCount <= 0) return 0;

    let seen = 0;
    for (let i = 0; i < display.length; i += 1) {
      const ch = display[i];
      if (
        (ch >= "0" && ch <= "9") ||
        (ch === "." && options.allowDecimal) ||
        (ch === "-" && options.allowNegative && i === 0)
      ) {
        seen += 1;
        if (seen >= rawCount) return i + 1;
      }
    }

    return display.length;
  }

  function currentRaw(input) {
    const raw = input.dataset.mathRaw;
    if (raw != null) return raw;
    return sanitizeRaw(readNativeValue(input), getOptions(input));
  }

  function setCurrentRaw(input, raw) {
    input.dataset.mathRaw = String(raw ?? "");
  }

  function patchValueProperty(input) {
    if (input.dataset.mathValuePatched === "1") return;

    Object.defineProperty(input, "value", {
      configurable: true,
      enumerable: true,
      get() {
        return currentRaw(this);
      },
      set(nextValue) {
        const opts = getOptions(this);
        const raw = sanitizeRaw(nextValue, opts);
        setCurrentRaw(this, raw);
        const maxDisplayDecimals = (document.activeElement === this) ? null : displayMaxDecimals(this);
        writeNativeValue(this, formatDisplay(raw, { trimTrailingZeros: true, maxDisplayDecimals }));
      },
    });

    input.dataset.mathValuePatched = "1";
  }

  function setInputModeIfMissing(input) {
    if (input.getAttribute("inputmode")) return;
    input.setAttribute("inputmode", allowsDecimal(input) ? "decimal" : "numeric");
  }

  function convertNumberInputToText(input) {
    if ((input.type || "").toLowerCase() !== "number") return;
    input.dataset.mathOriginalType = "number";
    input.type = "text";
    setInputModeIfMissing(input);
  }

  function applyFormatting(input, preserveSelection) {
    const options = getOptions(input);
    const beforeDisplay = readNativeValue(input);
    const existingRaw = currentRaw(input);
    const isFocused = document.activeElement === input;
    const maxDisplayDecimals = isFocused ? null : displayMaxDecimals(input);
    const trimTrailingZeros = !(preserveSelection && isFocused);
    const expectedDisplay = formatDisplay(existingRaw, { trimTrailingZeros, maxDisplayDecimals });
    const raw = (beforeDisplay === expectedDisplay) ? existingRaw : sanitizeRaw(beforeDisplay, options);
    setCurrentRaw(input, raw);
    const afterDisplay = formatDisplay(raw, { trimTrailingZeros, maxDisplayDecimals });
    writeNativeValue(input, afterDisplay);

    if (!preserveSelection || document.activeElement !== input) return;

    const start = input.selectionStart;
    const end = input.selectionEnd;
    if (typeof start !== "number" || typeof end !== "number") return;

    const rawStart = countRawCharsUntil(beforeDisplay, start, options);
    const rawEnd = countRawCharsUntil(beforeDisplay, end, options);
    const nextStart = caretForRawCount(afterDisplay, rawStart, options);
    const nextEnd = caretForRawCount(afterDisplay, rawEnd, options);

    try {
      input.setSelectionRange(nextStart, nextEnd);
    } catch (_) {
      // no-op
    }
  }

  function shiftDecimalRight(rawValue, places) {
    const raw = String(rawValue ?? "");
    if (!raw || raw === "-") return "";

    let sign = "";
    let numeric = raw;
    if (numeric.startsWith("-")) {
      sign = "-";
      numeric = numeric.slice(1);
    }

    const dotIndex = numeric.indexOf(".");
    if (dotIndex < 0) {
      return `${sign}${numeric}${"0".repeat(places)}`;
    }

    const intPart = numeric.slice(0, dotIndex);
    const fracPart = numeric.slice(dotIndex + 1);
    const digits = `${intPart}${fracPart}`;
    const splitAt = intPart.length + places;

    if (splitAt >= digits.length) {
      const shifted = `${digits}${"0".repeat(splitAt - digits.length)}`;
      const normalized = shifted.replace(/^0+(?=\d)/, "");
      return `${sign}${normalized || "0"}`;
    }

    const nextInt = digits.slice(0, splitAt).replace(/^0+(?=\d)/, "") || "0";
    const nextFrac = digits.slice(splitAt);
    return `${sign}${nextInt}.${nextFrac}`;
  }

  function applyZeroShortcut(input, zerosToAppend) {
    const options = getOptions(input);
    const raw = sanitizeRaw(currentRaw(input), options);
    if (!raw || raw === "-") return;

    const shifted = shiftDecimalRight(raw, zerosToAppend);
    const nextRaw = sanitizeRaw(shifted, options);
    if (!nextRaw || nextRaw === "-") return;

    setCurrentRaw(input, nextRaw);
    const maxDisplayDecimals = (document.activeElement === input) ? null : displayMaxDecimals(input);
    writeNativeValue(input, formatDisplay(nextRaw, { trimTrailingZeros: true, maxDisplayDecimals }));
    try {
      const len = readNativeValue(input).length;
      input.setSelectionRange(len, len);
    } catch (_) {
      // no-op
    }
    input.dispatchEvent(new Event("input", { bubbles: true }));
  }

  function stepRawByUnits(rawValue, direction, units) {
    const raw = String(rawValue ?? "");
    if (!raw || raw === "-") return "";
    if (direction !== 1 && direction !== -1) return raw;
    const magnitude = Number.isFinite(units) ? Math.trunc(units) : 1;
    if (magnitude <= 0) return raw;

    let sign = 1;
    let numeric = raw;
    if (numeric.startsWith("-")) {
      sign = -1;
      numeric = numeric.slice(1);
    }

    let intPart = numeric;
    let fracPart = "";
    const dot = numeric.indexOf(".");
    if (dot >= 0) {
      intPart = numeric.slice(0, dot);
      fracPart = numeric.slice(dot + 1);
    }

    const scaleLen = fracPart.length;
    const intDigits = (intPart || "0").replace(/^0+(?=\d)/, "") || "0";
    const allDigits = `${intDigits}${fracPart}`.replace(/^0+(?=\d)/, "") || "0";

    try {
      const base = BigInt(allDigits) * BigInt(sign);
      const scale = BigInt(10) ** BigInt(scaleLen);
      const next = base + BigInt(direction) * scale * BigInt(magnitude);
      const isNeg = next < 0n;
      let abs = isNeg ? (-next).toString() : next.toString();

      if (scaleLen === 0) {
        if (abs === "0") return "0";
        return isNeg ? `-${abs}` : abs;
      }

      if (abs.length <= scaleLen) abs = abs.padStart(scaleLen + 1, "0");
      let nextInt = abs.slice(0, -scaleLen) || "0";
      const nextFrac = abs.slice(-scaleLen);
      nextInt = nextInt.replace(/^0+(?=\d)/, "") || "0";

      const composed = `${nextInt}.${nextFrac}`;
      if (nextInt === "0" && /^0+$/.test(nextFrac)) return "0." + nextFrac;
      return isNeg ? `-${composed}` : composed;
    } catch (_) {
      return raw;
    }
  }

  function zeroWithScale(rawValue) {
    const raw = String(rawValue ?? "");
    const dot = raw.indexOf(".");
    if (dot < 0) return "0";

    const scaleLen = raw.length - dot - 1;
    if (scaleLen <= 0) return "0";
    return `0.${"0".repeat(scaleLen)}`;
  }

  function applyArrowStep(input, direction, units) {
    const options = getOptions(input);
    const current = sanitizeRaw(currentRaw(input), options);
    const raw = !current || current === "-" ? "0" : current;

    let stepped = stepRawByUnits(raw, direction, units);
    if (!options.allowNegative && String(stepped).startsWith("-")) {
      stepped = zeroWithScale(raw);
    }
    const nextRaw = sanitizeRaw(stepped, options);
    if (!nextRaw || nextRaw === "-") return;

    setCurrentRaw(input, nextRaw);
    const maxDisplayDecimals = (document.activeElement === input) ? null : displayMaxDecimals(input);
    writeNativeValue(input, formatDisplay(nextRaw, { trimTrailingZeros: true, maxDisplayDecimals }));
    try {
      const len = readNativeValue(input).length;
      input.setSelectionRange(len, len);
    } catch (_) {
      // no-op
    }
    input.dispatchEvent(new Event("input", { bubbles: true }));
  }

  function isShortcutInputTarget(target) {
    return target instanceof HTMLInputElement && target.matches(TARGET_SELECTOR);
  }

  function handleShortcutKeydown(event) {
    const input = event.target;
    if (!isShortcutInputTarget(input)) return;
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.isComposing) return;

    const key = String(event.key || "").toLowerCase();
    if (key === "arrowup" || key === "arrowdown") {
      const units = event.shiftKey ? 1000 : 1;
      event.preventDefault();
      applyArrowStep(input, key === "arrowup" ? 1 : -1, units);
      return;
    }

    let zeros = 0;
    if (key === "k" || key === "ن") zeros = 3;
    if (key === "h" || key === "ا") zeros = 2;
    if (!zeros) return;

    const raw = sanitizeRaw(currentRaw(input), getOptions(input));
    if (!raw || raw === "-") {
      event.preventDefault();
      return;
    }

    event.preventDefault();
    applyZeroShortcut(input, zeros);
  }

  function attachToInput(input) {
    if (!(input instanceof HTMLInputElement)) return;
    if (input.dataset.mathAttached === "1") return;

    const type = (input.type || "").toLowerCase();
    if (type !== "number" && type !== "text") return;

    convertNumberInputToText(input);
    setInputModeIfMissing(input);
    patchValueProperty(input);
    applyFormatting(input, false);

    input.addEventListener("input", () => applyFormatting(input, true));
    input.addEventListener("blur", () => applyFormatting(input, false));

    input.dataset.mathAttached = "1";
  }

  function attachWithin(root) {
    if (!root) return;
    if (root instanceof Element && root.matches(TARGET_SELECTOR)) attachToInput(root);
    if (!(root instanceof Element) && !(root instanceof Document)) return;
    root.querySelectorAll(TARGET_SELECTOR).forEach(attachToInput);
  }

  function unformatFormFields(form) {
    const fields = Array.from(form.querySelectorAll(TARGET_SELECTOR)).filter(
      (el) => el instanceof HTMLInputElement
    );

    fields.forEach((input) => {
      const raw = sanitizeRaw(currentRaw(input), getOptions(input));
      setCurrentRaw(input, raw);
      writeNativeValue(input, raw);
    });

    return fields;
  }

  function reformatFields(fields) {
    fields.forEach((input) => {
      if (!(input instanceof HTMLInputElement)) return;
      applyFormatting(input, false);
    });
  }

  function handleSubmitCapture(event) {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    const touched = unformatFormFields(form);

    setTimeout(() => {
      if (!event.defaultPrevented) return;
      reformatFields(touched);
    }, 0);
  }

  function handleFormData(event) {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;

    const byName = new Map();

    form.querySelectorAll(TARGET_SELECTOR).forEach((input) => {
      if (!(input instanceof HTMLInputElement)) return;
      if (input.disabled) return;
      if (!input.name) return;
      const raw = sanitizeRaw(currentRaw(input), getOptions(input));
      if (!byName.has(input.name)) byName.set(input.name, []);
      byName.get(input.name).push(raw);
    });

    byName.forEach((values, name) => {
      event.formData.delete(name);
      values.forEach((value) => event.formData.append(name, value));
    });
  }

  const observer = new MutationObserver((mutations) => {
    mutations.forEach((mutation) => {
      mutation.addedNodes.forEach((node) => {
        if (!(node instanceof Element)) return;
        attachWithin(node);
      });
    });
  });

  document.addEventListener("submit", handleSubmitCapture, true);
  document.addEventListener("formdata", handleFormData);
  document.addEventListener("keydown", handleShortcutKeydown, true);

  attachWithin(document);

  if (document.documentElement) {
    observer.observe(document.documentElement, { childList: true, subtree: true });
  }
})();
