from django import template

register = template.Library()

@register.filter
def human_number(val):
    """
    Global formatter:
    - 5000.000 → 5,000
    - 20500.5 → 20,500.5
    - 5.50 → 5.5
    - 0.000 → 0
    """
    if val is None:
        return ""

    try:
        val = float(val)
    except:
        return val  # not a number

    # Integer
    if val.is_integer():
        return f"{int(val):,}"

    # Float → trim trailing zeros
    s = f"{val:.10f}".rstrip("0").rstrip(".")
    if "." in s:
        int_part, frac = s.split(".")
        return f"{int(int_part):,}.{frac}"
    return f"{val:,}"


@register.filter
def div(a, b):
    """Template-safe division."""
    try:
        return float(a) / float(b)
    except:
        return ""
