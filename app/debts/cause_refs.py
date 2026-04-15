from __future__ import annotations

from typing import Iterable
from functools import lru_cache

from core.public_ids import extract_public_id_number, format_public_id
from debts.models import DebtCauseType


_CAUSE_PREFIX_BY_TYPE: dict[str, str] = {
    DebtCauseType.PURCHASE_BILL: "PB-",
    DebtCauseType.PROVIDER_RETURN: "PR-",
    DebtCauseType.POS_BILL: "PS-",
}


def normalize_cause_type(value: str | None) -> str:
    return str(value or "").strip().lower()


def public_prefix_for_cause_type(cause_type: str | None) -> str:
    return _CAUSE_PREFIX_BY_TYPE.get(normalize_cause_type(cause_type), "")


def infer_cause_type_from_public_ref(ref: str | None) -> str:
    token = str(ref or "").strip().upper()
    if not token:
        return ""
    for cause_type, prefix in _CAUSE_PREFIX_BY_TYPE.items():
        if token.startswith(prefix):
            return cause_type
    return ""


def _target_model_for_cause_type(cause_type: str):
    ct = normalize_cause_type(cause_type)
    if ct == DebtCauseType.PURCHASE_BILL:
        from billing.models import Bill

        return Bill
    if ct == DebtCauseType.PROVIDER_RETURN:
        from billing.models import ProviderReturn

        return ProviderReturn
    if ct == DebtCauseType.POS_BILL:
        from pos.models import SalesBill

        return SalesBill
    return None


@lru_cache(maxsize=2048)
def _public_ref_from_internal_id(*, cause_type: str, internal_id: int) -> str:
    model_cls = _target_model_for_cause_type(cause_type)
    if model_cls is None:
        return ""
    obj = model_cls.objects.filter(pk=int(internal_id)).only("public_id").first()
    return str(getattr(obj, "public_id", "") or "").strip().upper()


@lru_cache(maxsize=2048)
def _internal_id_from_public_ref(*, cause_type: str, public_ref: str) -> str:
    model_cls = _target_model_for_cause_type(cause_type)
    if model_cls is None:
        return ""
    obj = model_cls.objects.filter(public_id__iexact=public_ref).only("id").first()
    return str(getattr(obj, "id", "") or "").strip()


def cause_ref_for_ui(*, cause_type: str, cause_id: str) -> str:
    """
    Return user-facing cause reference.
    For purchase/return/POS causes this always prefers PB-/PR-/PS- public IDs.
    """
    ct = normalize_cause_type(cause_type)
    raw = str(cause_id or "").strip()
    if not raw:
        return ""

    prefix = public_prefix_for_cause_type(ct)
    if not prefix:
        return raw

    num = extract_public_id_number(value=raw, prefix=prefix)
    if num is not None:
        return format_public_id(prefix=prefix, number=num)

    if raw.isdigit():
        mapped = _public_ref_from_internal_id(cause_type=ct, internal_id=int(raw))
        if mapped:
            return mapped
        return format_public_id(prefix=prefix, number=int(raw))

    token_upper = raw.upper()
    if token_upper.startswith(prefix):
        return token_upper
    return raw


def cause_filter_variants(*, cause_type: str, cause_ref: str) -> list[str]:
    """
    Build compatible lookup variants for cause_id filter.
    Includes both public refs and legacy numeric IDs where possible.
    """
    ct = normalize_cause_type(cause_type)
    raw = str(cause_ref or "").strip()
    if not raw:
        return []

    prefix = public_prefix_for_cause_type(ct)
    if not prefix:
        return [raw]

    seen: set[str] = set()
    out: list[str] = []

    def add(v: str) -> None:
        vv = str(v or "").strip()
        if not vv or vv in seen:
            return
        seen.add(vv)
        out.append(vv)

    token_upper = raw.upper()
    add(raw)
    add(token_upper)

    num = extract_public_id_number(value=raw, prefix=prefix)
    if num is not None:
        canonical = format_public_id(prefix=prefix, number=num)
        add(canonical)
        add(str(num))
        tail = token_upper[len(prefix):].strip()
        if tail:
            add(tail)
        mapped_id = _internal_id_from_public_ref(cause_type=ct, public_ref=canonical)
        if mapped_id:
            add(mapped_id)
        return out

    if raw.isdigit():
        n = int(raw)
        canonical = format_public_id(prefix=prefix, number=n)
        add(canonical)
        add(str(n))
        mapped_ref = _public_ref_from_internal_id(cause_type=ct, internal_id=n)
        if mapped_ref:
            add(mapped_ref)
        return out

    return out


def any_public_cause_prefixes() -> Iterable[str]:
    return tuple(_CAUSE_PREFIX_BY_TYPE.values())
