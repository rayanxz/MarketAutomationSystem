// static/js/id_prefix_lock.js
// Reusable locked-prefix helper for public-ID inputs (PB-/PR-/PS-/D-).
(function (global) {
  "use strict";

  function toStr(v) {
    return String(v == null ? "" : v);
  }

  function startsWithCI(value, prefix) {
    return toStr(value).toUpperCase().startsWith(toStr(prefix).toUpperCase());
  }

  function extractDigits(value) {
    const m = toStr(value).match(/\d+/g);
    return m ? m.join("") : "";
  }

  function attach(input, options) {
    if (!input) return null;

    const opts = options || {};
    let prefix = toStr(opts.prefix || "");
    const digitsOnly = opts.digitsOnly !== false;
    const keepPrefixOnEmpty = opts.keepPrefixOnEmpty !== false;
    let locked = !!prefix;

    function minPos() {
      return locked ? prefix.length : 0;
    }

    function valueDigits(raw) {
      let text = toStr(raw).trim();
      if (locked && startsWithCI(text, prefix)) {
        text = text.slice(prefix.length);
      }
      return extractDigits(text);
    }

    function setCaret(start, end) {
      try {
        input.setSelectionRange(start, end);
      } catch (_) {
        // no-op
      }
    }

    function enforceCaret() {
      if (!locked) return;
      const min = minPos();
      const s = input.selectionStart;
      const e = input.selectionEnd;
      if (s == null || e == null) return;
      if (s < min || e < min) {
        setCaret(Math.max(min, s), Math.max(min, e));
      }
    }

    function syncFromRaw(raw, moveCaretToEnd) {
      if (!locked) return;
      const prevStart = input.selectionStart == null ? minPos() : input.selectionStart;
      const prevEnd = input.selectionEnd == null ? prevStart : input.selectionEnd;
      const next = prefix + valueDigits(raw);
      input.value = next;
      if (moveCaretToEnd) {
        setCaret(next.length, next.length);
        return;
      }
      let s = Math.max(minPos(), prevStart);
      let e = Math.max(minPos(), prevEnd);
      if (s > next.length) s = next.length;
      if (e > next.length) e = next.length;
      setCaret(s, e);
    }

    function ensurePrefix() {
      if (!locked) return;
      const cur = toStr(input.value);
      if (!startsWithCI(cur, prefix)) {
        syncFromRaw(cur, false);
      } else if (digitsOnly) {
        const normalized = prefix + valueDigits(cur);
        if (cur !== normalized) syncFromRaw(cur, false);
      }
      if (keepPrefixOnEmpty && !input.value) {
        input.value = prefix;
        setCaret(prefix.length, prefix.length);
      }
      enforceCaret();
    }

    function isNavKey(key) {
      return (
        key === "Tab" ||
        key === "Enter" ||
        key === "Escape" ||
        key === "ArrowLeft" ||
        key === "ArrowRight" ||
        key === "ArrowUp" ||
        key === "ArrowDown" ||
        key === "PageUp" ||
        key === "PageDown" ||
        key === "End"
      );
    }

    function onKeyDown(e) {
      if (!locked) return;

      const key = e.key;
      const min = minPos();
      const start = input.selectionStart == null ? min : input.selectionStart;
      const end = input.selectionEnd == null ? start : input.selectionEnd;
      const hasSel = end > start;

      if (key === "Home") {
        e.preventDefault();
        setCaret(min, min);
        return;
      }

      if (key === "Backspace") {
        if ((hasSel && start < min) || (!hasSel && start <= min)) {
          e.preventDefault();
          setCaret(min, Math.max(min, end));
        }
        return;
      }

      if (key === "Delete") {
        if ((hasSel && start < min) || (!hasSel && start < min)) {
          e.preventDefault();
          setCaret(min, Math.max(min, end));
        }
        return;
      }

      if (isNavKey(key)) {
        setTimeout(enforceCaret, 0);
        return;
      }

      if (e.ctrlKey || e.metaKey || e.altKey) return;

      if (digitsOnly && key.length === 1 && !/[0-9]/.test(key)) {
        e.preventDefault();
      }
    }

    function onPaste(e) {
      if (!locked) return;
      const txt = e.clipboardData ? e.clipboardData.getData("text") : "";
      e.preventDefault();

      const min = minPos();
      const start = Math.max(min, input.selectionStart == null ? min : input.selectionStart);
      const end = Math.max(min, input.selectionEnd == null ? start : input.selectionEnd);

      const currentDigits = valueDigits(input.value);
      const pasteDigits = valueDigits(txt);

      const from = start - min;
      const to = end - min;
      const merged = currentDigits.slice(0, from) + pasteDigits + currentDigits.slice(to);

      input.value = prefix + merged;
      const caret = min + from + pasteDigits.length;
      setCaret(caret, caret);

      input.dispatchEvent(new Event("input", { bubbles: true }));
    }

    function onInput() {
      if (!locked) return;
      ensurePrefix();
    }

    function onPointer() {
      if (!locked) return;
      setTimeout(enforceCaret, 0);
    }

    function onFocus() {
      if (!locked) return;
      if (!toStr(input.value)) input.value = prefix;
      enforceCaret();
    }

    function onBlur() {
      if (!locked) return;
      ensurePrefix();
    }

    input.addEventListener("keydown", onKeyDown);
    input.addEventListener("paste", onPaste);
    input.addEventListener("input", onInput);
    input.addEventListener("focus", onFocus);
    input.addEventListener("blur", onBlur);
    input.addEventListener("click", onPointer);
    input.addEventListener("mouseup", onPointer);

    function setPrefix(nextPrefix, cfg) {
      const conf = cfg || {};
      const preserveNumeric = conf.preserveNumeric !== false;
      const keepRawWhenUnlock = conf.keepRawWhenUnlock === true;
      const prevRaw = toStr(input.value);
      const prevDigits = valueDigits(prevRaw);

      prefix = toStr(nextPrefix || "");
      locked = !!prefix;

      if (locked) {
        input.value = prefix + (preserveNumeric ? prevDigits : "");
        enforceCaret();
      } else if (keepRawWhenUnlock) {
        input.value = prevRaw;
      } else if (preserveNumeric) {
        input.value = prevDigits;
      } else {
        input.value = "";
      }
    }

    function getValue(cfg) {
      const conf = cfg || {};
      const emptyIfNoDigits = conf.emptyIfNoDigits !== false;
      const raw = toStr(input.value).trim();

      if (!locked) return raw;

      const digits = valueDigits(raw);
      if (!digits && emptyIfNoDigits) return "";
      return prefix + digits;
    }

    function getDigits() {
      return valueDigits(input.value);
    }

    function getPrefix() {
      return locked ? prefix : "";
    }

    function isLocked() {
      return locked;
    }

    // Initial normalization.
    if (locked) ensurePrefix();

    return {
      setPrefix,
      getValue,
      getDigits,
      getPrefix,
      isLocked,
      ensurePrefix,
    };
  }

  global.IdPrefixLock = {
    attach,
  };
})(window);

