from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Type


DEC0 = Decimal("0")
MONEY_2DP = Decimal("0.01")


def round_money(value) -> Decimal:
    """
    Normalize monetary values to strict 2 decimals using HALF_UP.
    Returns Decimal (never string).
    """
    try:
        dec = Decimal(str(value if value is not None else DEC0))
    except (InvalidOperation, TypeError, ValueError):
        dec = DEC0
    if not dec.is_finite():
        dec = DEC0
    return dec.quantize(MONEY_2DP, rounding=ROUND_HALF_UP)


def money_has_more_than_2_decimals(value) -> bool:
    try:
        dec = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return False
    if not dec.is_finite():
        return False
    return dec.as_tuple().exponent < -2


def parse_money_strict(
    value,
    *,
    field_name: str = "amount",
    error_cls: Type[Exception] = ValueError,
) -> Decimal:
    text = "" if value is None else str(value).strip()
    if text == "":
        raise error_cls(f"{field_name} is required")
    try:
        dec = Decimal(text)
    except (InvalidOperation, TypeError, ValueError):
        raise error_cls(f"Invalid {field_name}")
    if not dec.is_finite():
        raise error_cls(f"Invalid {field_name}")
    if dec.as_tuple().exponent < -2:
        raise error_cls(f"{field_name} supports at most 2 decimal digits")
    return round_money(dec)


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
