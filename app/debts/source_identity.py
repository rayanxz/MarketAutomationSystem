from __future__ import annotations

from typing import Iterable, Optional

from django.db.models import Q


def _norm(value) -> str:
    return str(value or "").strip()


def source_identity_variants(*, source_id: str) -> tuple[str, str]:
    """
    Return canonical + legacy-USD variants for lookup.
    """
    src = _norm(source_id)
    if src.endswith(":USD"):
        base = src[:-4].strip()
        if base:
            src = base
    return src, f"{src}:USD"


def canonical_source_identity(*, source_id: str, currency_code: str) -> tuple[str, str, str]:
    """
    Canonicalize source identity across DB vendors.
    Returns: (canonical_source_id, legacy_source_id, canonical_currency_code)
    """
    cur = _norm(currency_code or "SYP").upper() or "SYP"
    src = _norm(source_id)
    legacy_src = ""

    if src.endswith(":USD"):
        legacy_src = src
        base = src[:-4].strip()
        if base:
            src = base
        cur = "USD"

    return src, legacy_src, cur


def source_identity_base(*, source_id: str, legacy_source_id: str = "") -> str:
    """
    Resolve canonical base source id from source/legacy values.
    Prefers the direct source_id when available.
    """
    for raw in (_norm(source_id), _norm(legacy_source_id)):
        if not raw:
            continue
        if raw.endswith(":USD"):
            raw = raw[:-4].strip()
        if raw:
            return raw
    return ""


def source_identity_numeric_base(*, source_id: str, legacy_source_id: str = "") -> Optional[int]:
    base = source_identity_base(source_id=source_id, legacy_source_id=legacy_source_id)
    if base.isdigit():
        return int(base)
    return None


def source_identity_lookup_q(
    *,
    source_ids: Iterable[str | int],
    source_field: str = "source_id",
    legacy_field: str = "legacy_source_id",
) -> Q:
    """
    Build a canonical + legacy compatibility lookup Q for source identity lists.
    """
    canonical_ids: set[str] = set()
    legacy_usd_ids: set[str] = set()

    for raw in source_ids:
        canonical, legacy_usd = source_identity_variants(source_id=str(raw))
        if not canonical:
            continue
        canonical_ids.add(canonical)
        legacy_usd_ids.add(legacy_usd)

    if not canonical_ids:
        return Q(pk__in=[])

    canonical_list = sorted(canonical_ids)
    legacy_usd_list = sorted(legacy_usd_ids)
    return (
        Q(**{f"{source_field}__in": canonical_list})
        | Q(**{f"{legacy_field}__in": canonical_list})
        | Q(**{f"{source_field}__in": legacy_usd_list})
        | Q(**{f"{legacy_field}__in": legacy_usd_list})
    )
