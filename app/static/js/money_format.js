(function (global) {
  "use strict";

  function addThousands(intPart) {
    return String(intPart).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  }

  function parseMoneyInput(value) {
    let raw = String(value ?? "").trim();
    if (!raw) return null;
    raw = raw.replace(/,/g, "");

    const m = raw.match(/^([+-]?)(?:(\d+)(?:\.(\d*))?|\.(\d+))$/);
    if (!m) return null;

    const sign = m[1] === "-" ? "-" : "";
    const intPart = (m[2] || "0").replace(/^0+(?=\d)/, "");
    const fracPart = m[3] != null ? m[3] : (m[4] || "");
    return { sign, intPart: intPart || "0", fracPart };
  }

  function roundHalfUp2(parsed) {
    const d1 = parsed.fracPart[0] || "0";
    const d2 = parsed.fracPart[1] || "0";
    const d3 = parsed.fracPart[2] || "0";

    let frac2 = Number(d1 + d2);
    if (d3 >= "5") frac2 += 1;

    let intBig = BigInt(parsed.intPart || "0");
    if (frac2 >= 100) {
      intBig += 1n;
      frac2 -= 100;
    }

    const frac2Str = String(frac2).padStart(2, "0");
    const isZero = intBig === 0n && frac2Str === "00";
    const sign = isZero ? "" : parsed.sign;
    return { sign, intPart: intBig.toString(), frac2: frac2Str };
  }

  function formatRoundedParts(rounded) {
    const intFmt = addThousands(rounded.intPart);
    const fracTrimmed = rounded.frac2.replace(/0+$/, "");
    if (!fracTrimmed) return `${rounded.sign}${intFmt}`;
    return `${rounded.sign}${intFmt}.${fracTrimmed}`;
  }

  function formatMoney(v) {
    if (v === null || v === undefined || v === "") return "";

    const parsed = parseMoneyInput(v);
    if (parsed) {
      return formatRoundedParts(roundHalfUp2(parsed));
    }

    const num = Number(v);
    if (!Number.isFinite(num)) return v;

    // Fallback for uncommon non-decimal numeric strings.
    const fromNumber = parseMoneyInput(num.toString());
    if (!fromNumber) return num.toString();
    return formatRoundedParts(roundHalfUp2(fromNumber));
  }

  global.formatMoney = formatMoney;
})(typeof window !== "undefined" ? window : globalThis);
