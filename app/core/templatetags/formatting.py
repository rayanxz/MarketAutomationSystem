from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


@register.filter
def human_number(val):
    """
    Global formatter:
    - Decimal-like values keep fixed precision and thousand separators.
    - Minimum displayed fractional digits is 2.
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
        return "0.00"
    s = format(d, "f")

    sign = ""
    if s.startswith("-"):
        sign = "-"
        s = s[1:]

    if "." in s:
        int_part, frac = s.split(".", 1)
    else:
        int_part, frac = s, ""

    int_part_fmt = f"{int(int_part or '0'):,}"
    if not frac:
        frac = "00"
    elif len(frac) == 1:
        frac = f"{frac}0"
    return f"{sign}{int_part_fmt}.{frac}"


@register.filter
def div(a, b):
    """Template-safe division."""
    try:
        return float(a) / float(b)
    except Exception:
        return ""
