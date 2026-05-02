from __future__ import annotations

from decimal import Decimal, InvalidOperation


def format_quantity(value, unit=None) -> str:
    """
    Format quantity values for UI display.

    Rules:
    - Integer values show no decimal part.
    - Decimal values keep meaningful fractional digits only.
    - Thousands are grouped with commas.
    - If `unit` is provided, append it with one space.
    """
    if value is None:
        return "—"

    raw = str(value).strip()
    if raw == "":
        return "—"

    try:
        dec = Decimal(raw.replace(",", ""))
    except (InvalidOperation, ValueError, TypeError):
        return "—"

    if not dec.is_finite():
        return "—"

    if dec == 0:
        base = "0"
    else:
        s = format(dec.normalize(), "f")
        sign = ""
        if s.startswith("-"):
            sign = "-"
            s = s[1:]

        if "." in s:
            int_part, frac = s.split(".", 1)
        else:
            int_part, frac = s, ""

        int_fmt = f"{int(int_part or '0'):,}"
        frac = frac.rstrip("0")
        base = f"{sign}{int_fmt}" if not frac else f"{sign}{int_fmt}.{frac}"

    unit_text = "" if unit is None else str(unit).strip()
    if unit_text:
        return f"{base} {unit_text}".strip()
    return str(base)
