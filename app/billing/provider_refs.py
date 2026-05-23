from __future__ import annotations

import re

from billing.models import Provider, PROVIDER_PUBLIC_ID_PREFIX


def normalize_provider_ref(value: str | int | None) -> str:
    return str(value or "").strip()


def is_valid_provider_public_ref(*, token: str) -> bool:
    normalized = str(token or "").strip().upper()
    prefix = str(PROVIDER_PUBLIC_ID_PREFIX or "").strip().upper()
    if not normalized or not prefix:
        return False
    return bool(re.fullmatch(rf"^{re.escape(prefix)}\d+$", normalized))


def _int_or_none(raw: str | int | None) -> int | None:
    try:
        token = normalize_provider_ref(raw)
        if token == "":
            return None
        value = int(token)
        return value if value > 0 else None
    except Exception:
        return None


def resolve_provider_from_ref(
    *,
    ref: str | int,
    for_update: bool = False,
    allow_numeric_fallback: bool = True,
) -> Provider | None:
    token = normalize_provider_ref(ref)
    if not token:
        return None

    qs = Provider.objects
    if for_update:
        qs = qs.select_for_update()

    if is_valid_provider_public_ref(token=token):
        return qs.filter(public_id__iexact=token).first()

    if allow_numeric_fallback:
        provider_id = _int_or_none(token)
        if provider_id is not None:
            return qs.filter(id=provider_id).first()

    return None
