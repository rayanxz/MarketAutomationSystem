from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


@register.filter
def human_number(val):
    """
    Global formatter:
    - 5000.000  -> 5,000
    - 20500.5   -> 20,500.5
    - 5.50      -> 5.5
    - 0.000     -> 0
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

    # Fixed-point text and trim trailing zeros in the fraction.
    s = format(d, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")

    if s in {"", "-0"}:
        return "0"

    sign = ""
    if s.startswith("-"):
        sign = "-"
        s = s[1:]

    if "." in s:
        int_part, frac = s.split(".", 1)
    else:
        int_part, frac = s, ""

    int_part_fmt = f"{int(int_part or '0'):,}"
    if frac:
        return f"{sign}{int_part_fmt}.{frac}"
    return f"{sign}{int_part_fmt}"


@register.filter
def div(a, b):
    """Template-safe division."""
    try:
        return float(a) / float(b)
    except Exception:
        return ""
