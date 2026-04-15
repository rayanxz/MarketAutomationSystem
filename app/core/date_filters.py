from __future__ import annotations

from datetime import date


def parse_filter_date(value: str | None) -> date | None:
    """
    Parse user-facing filter dates.
    Primary format is DD/MM/YYYY, with backward-compatible ISO YYYY-MM-DD.
    """
    raw = str(value or "").strip()
    if not raw:
        return None

    if len(raw) >= 10 and raw[4] == "-" and raw[7] == "-":
        token = raw[:10]
        try:
            return date.fromisoformat(token)
        except Exception:
            return None

    sep = "/" if "/" in raw else ("-" if "-" in raw else "")
    if sep:
        parts = [p.strip() for p in raw.split(sep)]
        if len(parts) >= 3:
            try:
                if len(parts[0]) == 4:
                    y = int(parts[0])
                    m = int(parts[1])
                    d = int(parts[2])
                else:
                    d = int(parts[0])
                    m = int(parts[1])
                    y = int(parts[2])
                return date(y, m, d)
            except Exception:
                return None

    return None


def format_filter_date(value: date | None) -> str:
    if value is None:
        return ""
    try:
        return value.strftime("%d/%m/%Y")
    except Exception:
        return ""
