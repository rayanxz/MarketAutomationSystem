from decimal import Decimal, InvalidOperation

from django import template
from core.formatters import round_money

register = template.Library()


@register.filter
def human_number(val):
    """
    Global formatter:
    - Decimal-like values use thousand separators.
    - Fractional trailing zeros are trimmed.
    - Integer inputs remain integer-style with thousand separators.
    """
    if val is None:
        return ""
    if isinstance(val, bool):
        return val
    if isinstance(val, int):
        return f"{val:,}"

    try:
        raw = str(val).strip()
        if raw == "":
            return ""
        d = Decimal(raw.replace(",", ""))
    except (InvalidOperation, ValueError, TypeError):
        return val  # not a number

    if d == 0:
        return "0"
    s = format(d.normalize(), "f")

    sign = ""
    if s.startswith("-"):
        sign = "-"
        s = s[1:]

    if "." in s:
        int_part, frac = s.split(".", 1)
    else:
        int_part, frac = s, ""

    int_part_fmt = f"{int(int_part or '0'):,}"
    frac = frac.rstrip("0")
    if not frac:
        return f"{sign}{int_part_fmt}"
    return f"{sign}{int_part_fmt}.{frac}"


@register.filter
def money_number(val):
    """
    Money display formatter:
    - 12.00 -> 12
    - 12.50 -> 12.5
    - 12.25 -> 12.25
    - 1000.00 -> 1,000
    - 1000.80 -> 1,000.8
    """
    if val is None:
        return ""
    if isinstance(val, bool):
        return val

    try:
        raw = str(val).strip()
        if raw == "":
            return ""
        d = Decimal(raw.replace(",", ""))
    except (InvalidOperation, ValueError, TypeError):
        return val  # not a number

    # Normalize money display to 2dp with explicit HALF_UP rounding.
    d = round_money(d)
    if d == 0:
        return "0"

    s = format(d, "f")  # avoid scientific notation

    sign = ""
    if s.startswith("-"):
        sign = "-"
        s = s[1:]

    if "." in s:
        int_part, frac = s.split(".", 1)
    else:
        int_part, frac = s, ""

    int_part_fmt = f"{int(int_part or '0'):,}"
    frac = frac.rstrip("0")

    if not frac:
        return f"{sign}{int_part_fmt}"
    return f"{sign}{int_part_fmt}.{frac}"


@register.filter
def div(a, b):
    """Template-safe division."""
    try:
        return float(a) / float(b)
    except Exception:
        return ""
