from __future__ import annotations

from decimal import Decimal
from typing import Any

from billing.models import Bill, ProviderReturn
from debts.models import (
    CreditorDebt,
    DebtCauseType,
    DebtDirection,
    DebtRecord,
    DebtStatus,
    DebtorDebt,
)
from debts.source_identity import source_identity_base


DEC0 = Decimal("0.00")
SUPPORTED_CURRENCIES = ("SYP", "USD")
BILL_PUBLIC_PREFIX = "PB-"
RETURN_PUBLIC_PREFIX = "PR-"


def _new_state() -> dict[str, dict[str, Decimal | int]]:
    return {
        code: {
            "receivable": DEC0,
            "payable": DEC0,
            "open_receivable_count": 0,
            "open_payable_count": 0,
        }
        for code in SUPPORTED_CURRENCIES
    }


def _safe_decimal(raw) -> Decimal:
    if raw is None:
        return DEC0
    if isinstance(raw, Decimal):
        return raw
    return Decimal(str(raw))


def _zero_amount_dict() -> dict[str, Decimal]:
    return {"SYP": DEC0, "USD": DEC0}


def _amount_dict_from_row(*, syp: Decimal, usd: Decimal) -> dict[str, Decimal]:
    return {"SYP": _safe_decimal(syp), "USD": _safe_decimal(usd)}


def _public_token_kind(*, token: str, prefix: str) -> str:
    """
    Returns:
    - "valid_public" for well-formed prefixed public id (e.g. PB-001)
    - "prefixed_malformed" for prefixed but malformed
    - "non_public" otherwise
    """
    t = str(token or "").strip().upper()
    if not t.startswith(prefix):
        return "non_public"
    tail = t[len(prefix):].strip()
    if tail.isdigit():
        return "valid_public"
    return "prefixed_malformed"


def _legacy_identity_keys(
    *,
    source_id: str,
    legacy_source_id: str,
    public_prefix: str,
) -> tuple[str, str]:
    """
    Returns (normalized_base, normalized_public_token_or_empty).
    """
    base = source_identity_base(source_id=source_id, legacy_source_id=legacy_source_id)
    token = str(base or "").strip()
    token_upper = token.upper()
    if _public_token_kind(token=token_upper, prefix=public_prefix) == "valid_public":
        return token_upper, token_upper
    return token, ""


def _append_warning_unique(*, target_list: list[dict[str, Any]], payload: dict[str, Any], seen_keys: set[tuple]) -> None:
    key = (
        payload.get("warning_type"),
        str(payload.get("cause_type", "")),
        str(payload.get("cause_id", "")),
        str(payload.get("source_app", "")),
        str(payload.get("source_model", "")),
        str(payload.get("source_id", "")),
        str(payload.get("direction", "")),
    )
    if key in seen_keys:
        return
    seen_keys.add(key)
    target_list.append(payload)


def _resolve_source_map(
    *,
    model_cls,
    tokens: set[str],
    public_prefix: str,
    cause_type: str,
    direction: str,
    unresolved_identities: list[dict[str, Any]],
    unresolved_seen: set[tuple],
) -> tuple[set[str], set[str], dict[str, str], set[str]]:
    """
    Build central coverage identity sets and preferred source-id mapping.

    Returns:
    - resolved numeric ids (as str)
    - resolved public refs (uppercase)
    - preferred source id by key (numeric and public keys -> numeric id if resolved else public)
    - tokens that should be treated as unresolved for hard dedup
    """
    numeric_tokens: set[str] = set()
    public_tokens: set[str] = set()
    unresolved_tokens: set[str] = set()

    for raw in tokens:
        token = str(raw or "").strip()
        if not token:
            continue
        if token.isdigit():
            numeric_tokens.add(token)
            continue
        kind = _public_token_kind(token=token, prefix=public_prefix)
        if kind == "valid_public":
            public_tokens.add(token.upper())
            continue
        if kind == "prefixed_malformed":
            _append_warning_unique(
                target_list=unresolved_identities,
                payload={
                    "warning_type": "unresolved_central_identity",
                    "cause_type": cause_type,
                    "cause_id": token,
                    "direction": direction,
                },
                seen_keys=unresolved_seen,
            )
            unresolved_tokens.add(token)
            continue
        _append_warning_unique(
            target_list=unresolved_identities,
            payload={
                "warning_type": "unresolved_central_identity",
                "cause_type": cause_type,
                "cause_id": token,
                "direction": direction,
            },
            seen_keys=unresolved_seen,
        )
        unresolved_tokens.add(token)

    id_ints = [int(v) for v in sorted(numeric_tokens) if v.isdigit()]
    qs = model_cls.objects.none()
    has_q = False
    if id_ints:
        qs = model_cls.objects.filter(id__in=id_ints)
        has_q = True
    if public_tokens:
        q_public = model_cls.objects.filter(public_id__in=sorted(public_tokens))
        qs = (qs | q_public) if has_q else q_public
        has_q = True

    rows = list(qs.values_list("id", "public_id")) if has_q else []
    id_to_public: dict[str, str] = {}
    public_to_id: dict[str, str] = {}
    for raw_id, raw_public in rows:
        sid = str(int(raw_id))
        pref = str(raw_public or "").strip().upper()
        if not pref:
            continue
        id_to_public[sid] = pref
        public_to_id[pref] = sid

    resolved_numeric: set[str] = set()
    resolved_public: set[str] = set()
    preferred_by_key: dict[str, str] = {}

    for sid in numeric_tokens:
        resolved_numeric.add(sid)
        preferred_by_key[sid] = sid
        pref = id_to_public.get(sid, "")
        if pref:
            resolved_public.add(pref)
            preferred_by_key[pref] = sid

    for pref in public_tokens:
        sid = public_to_id.get(pref, "")
        if sid:
            resolved_numeric.add(sid)
            resolved_public.add(pref)
            preferred_by_key[sid] = sid
            preferred_by_key[pref] = sid
        else:
            # Keep unresolved public ref as dedup key (public/public comparison still possible).
            resolved_public.add(pref)
            preferred_by_key[pref] = pref
            _append_warning_unique(
                target_list=unresolved_identities,
                payload={
                    "warning_type": "missing_source_object",
                    "cause_type": cause_type,
                    "cause_id": pref,
                    "direction": direction,
                },
                seen_keys=unresolved_seen,
            )

    return resolved_numeric, resolved_public, preferred_by_key, unresolved_tokens


def _add_amount(
    *,
    state: dict[str, dict[str, Decimal | int]],
    currency_code: str,
    direction: str,
    amount: Decimal,
) -> None:
    code = (currency_code or "").upper()
    if code not in SUPPORTED_CURRENCIES:
        return
    amt = _safe_decimal(amount)
    if amt <= DEC0:
        return

    bucket = state[code]
    if direction == DebtDirection.RECEIVABLE:
        bucket["receivable"] = _safe_decimal(bucket["receivable"]) + amt
        bucket["open_receivable_count"] = int(bucket["open_receivable_count"]) + 1
    elif direction == DebtDirection.PAYABLE:
        bucket["payable"] = _safe_decimal(bucket["payable"]) + amt
        bucket["open_payable_count"] = int(bucket["open_payable_count"]) + 1


def _legacy_cause_type_from_source(*, source_app: str, source_model: str) -> str:
    app = (source_app or "").strip().lower()
    model = (source_model or "").strip().lower()
    if app == "billing" and model == "bill":
        return DebtCauseType.PURCHASE_BILL
    if app == "billing" and model == "providerreturn":
        return DebtCauseType.PROVIDER_RETURN
    if model == "manualdebt":
        return DebtCauseType.MANUAL
    return ""


def collect_provider_open_obligations(*, provider_id: int) -> dict[str, Any]:
    """
    Read-only coexistence-aware obligation collector.

    Returns:
    {
      "provider_id": int,
      "obligations": [
         {
           "debt_source": "central" | "legacy",
           "debt_id": int,
           "public_id": str | None,
           "direction": "payable" | "receivable",
           "cause_type": str,
           "cause_id": str,
           "created_at": datetime,
           "remaining_syp": Decimal,
           "remaining_usd": Decimal,
         }
      ],
      "diagnostics": {
        "dedup_warnings": [...],
        "unresolved_identities": [...],
      },
    }
    """
    obligations: list[dict[str, Any]] = []
    diagnostics: dict[str, list[dict[str, Any]]] = {
        "dedup_warnings": [],
        "unresolved_identities": [],
    }
    dedup_seen: set[tuple] = set()
    unresolved_seen: set[tuple] = set()

    central_rows = list(
        DebtRecord.objects.filter(
            provider_id=provider_id,
            status=DebtStatus.OPEN,
            direction__in=[DebtDirection.PAYABLE, DebtDirection.RECEIVABLE],
        ).only(
            "id",
            "public_id",
            "created_at",
            "direction",
            "cause_type",
            "cause_id",
            "remaining_syp",
            "remaining_usd",
        )
    )

    covered_payable_bill_cause_tokens: set[str] = set()
    covered_receivable_return_cause_tokens: set[str] = set()
    covered_payable_manual_entry_ids: set[int] = set()
    covered_receivable_manual_entry_ids: set[int] = set()
    has_payable_manual_nonnumeric = False
    has_receivable_manual_nonnumeric = False
    central_payable_amount_by_key: dict[str, dict[str, Decimal]] = {}
    central_receivable_amount_by_key: dict[str, dict[str, Decimal]] = {}

    for debt in central_rows:
        obligations.append(
            {
                "debt_source": "central",
                "debt_id": int(debt.id),
                "public_id": str(debt.public_id or ""),
                "direction": debt.direction,
                "cause_type": (debt.cause_type or "").strip().lower(),
                "cause_id": str(debt.cause_id or "").strip(),
                "created_at": debt.created_at,
                "remaining_syp": _safe_decimal(debt.remaining_syp),
                "remaining_usd": _safe_decimal(debt.remaining_usd),
            }
        )

        cause_type = (debt.cause_type or "").strip().lower()
        cause_id = str(debt.cause_id or "").strip()
        if not cause_id:
            continue

        if debt.direction == DebtDirection.PAYABLE and cause_type == DebtCauseType.PURCHASE_BILL:
            covered_payable_bill_cause_tokens.add(cause_id)
        elif debt.direction == DebtDirection.RECEIVABLE and cause_type == DebtCauseType.PROVIDER_RETURN:
            covered_receivable_return_cause_tokens.add(cause_id)
        elif cause_type == DebtCauseType.MANUAL and cause_id.isdigit():
            if debt.direction == DebtDirection.PAYABLE:
                covered_payable_manual_entry_ids.add(int(cause_id))
            elif debt.direction == DebtDirection.RECEIVABLE:
                covered_receivable_manual_entry_ids.add(int(cause_id))
        elif cause_type == DebtCauseType.MANUAL:
            if debt.direction == DebtDirection.PAYABLE:
                has_payable_manual_nonnumeric = True
            elif debt.direction == DebtDirection.RECEIVABLE:
                has_receivable_manual_nonnumeric = True

    (
        covered_payable_bill_ids,
        covered_payable_bill_public,
        bill_preferred_key,
        unresolved_bill_tokens,
    ) = _resolve_source_map(
        model_cls=Bill,
        tokens=covered_payable_bill_cause_tokens,
        public_prefix=BILL_PUBLIC_PREFIX,
        cause_type=DebtCauseType.PURCHASE_BILL,
        direction=DebtDirection.PAYABLE,
        unresolved_identities=diagnostics["unresolved_identities"],
        unresolved_seen=unresolved_seen,
    )
    (
        covered_receivable_return_ids,
        covered_receivable_return_public,
        return_preferred_key,
        unresolved_return_tokens,
    ) = _resolve_source_map(
        model_cls=ProviderReturn,
        tokens=covered_receivable_return_cause_tokens,
        public_prefix=RETURN_PUBLIC_PREFIX,
        cause_type=DebtCauseType.PROVIDER_RETURN,
        direction=DebtDirection.RECEIVABLE,
        unresolved_identities=diagnostics["unresolved_identities"],
        unresolved_seen=unresolved_seen,
    )

    for debt in central_rows:
        cause_type = (debt.cause_type or "").strip().lower()
        cause_id = str(debt.cause_id or "").strip()
        amount = _amount_dict_from_row(
            syp=_safe_decimal(debt.remaining_syp),
            usd=_safe_decimal(debt.remaining_usd),
        )
        if debt.direction == DebtDirection.PAYABLE and cause_type == DebtCauseType.PURCHASE_BILL:
            if cause_id in unresolved_bill_tokens:
                continue
            key = bill_preferred_key.get(cause_id, cause_id)
            central_payable_amount_by_key[key] = amount
        elif debt.direction == DebtDirection.RECEIVABLE and cause_type == DebtCauseType.PROVIDER_RETURN:
            if cause_id in unresolved_return_tokens:
                continue
            key = return_preferred_key.get(cause_id, cause_id)
            central_receivable_amount_by_key[key] = amount

    legacy_payables = DebtorDebt.objects.filter(provider_id=provider_id, status=DebtorDebt.Status.OPEN).only(
        "id",
        "created_at",
        "source_app",
        "source_model",
        "source_id",
        "legacy_source_id",
        "currency_code",
        "total",
        "paid_amount",
    )
    for entry in legacy_payables:
        source_app = (entry.source_app or "").strip().lower()
        source_model = (entry.source_model or "").strip().lower()
        base_source_id, public_token = _legacy_identity_keys(
            source_id=entry.source_id,
            legacy_source_id=entry.legacy_source_id,
            public_prefix=BILL_PUBLIC_PREFIX,
        )
        dedup_key = ""
        is_duplicate = False

        if source_app == "billing" and source_model == "bill":
            if base_source_id in covered_payable_bill_ids:
                is_duplicate = True
                dedup_key = bill_preferred_key.get(base_source_id, base_source_id)
            elif public_token and public_token in covered_payable_bill_public:
                is_duplicate = True
                dedup_key = bill_preferred_key.get(public_token, public_token)
        if is_duplicate:
            legacy_remaining = _amount_dict_from_row(
                syp=_safe_decimal(entry.total) - _safe_decimal(entry.paid_amount),
                usd=DEC0,
            )
            central_amount = central_payable_amount_by_key.get(dedup_key, _zero_amount_dict())
            if legacy_remaining != central_amount:
                _append_warning_unique(
                    target_list=diagnostics["dedup_warnings"],
                    payload={
                        "warning_type": "central_legacy_amount_mismatch",
                        "source_identity": {
                            "source_app": "billing",
                            "source_model": "Bill",
                            "source_id": str(dedup_key),
                        },
                        "central_amount": central_amount,
                        "legacy_amount": legacy_remaining,
                    },
                    seen_keys=dedup_seen,
                )
            continue
        if source_app == "debts" and source_model == "manualdebt" and entry.id in covered_payable_manual_entry_ids:
            continue
        if source_app == "debts" and source_model == "manualdebt" and has_payable_manual_nonnumeric:
            _append_warning_unique(
                target_list=diagnostics["dedup_warnings"],
                payload={
                    "warning_type": "manual_identity_desync",
                    "direction": DebtDirection.PAYABLE,
                    "source_app": "debts",
                    "source_model": "ManualDebt",
                    "source_id": str(base_source_id),
                },
                seen_keys=dedup_seen,
            )

        remaining = _safe_decimal(entry.total) - _safe_decimal(entry.paid_amount)
        currency_code = (entry.currency_code or "SYP").upper()
        obligations.append(
            {
                "debt_source": "legacy",
                "debt_id": int(entry.id),
                "public_id": None,
                "direction": DebtDirection.PAYABLE,
                "cause_type": _legacy_cause_type_from_source(source_app=entry.source_app, source_model=entry.source_model),
                "cause_id": str(base_source_id or entry.source_id or ""),
                "created_at": entry.created_at,
                "remaining_syp": remaining if currency_code == "SYP" else DEC0,
                "remaining_usd": remaining if currency_code == "USD" else DEC0,
            }
        )

    legacy_receivables = CreditorDebt.objects.filter(provider_id=provider_id, status=CreditorDebt.Status.OPEN).only(
        "id",
        "created_at",
        "source_app",
        "source_model",
        "source_id",
        "legacy_source_id",
        "currency_code",
        "total",
        "collected",
    )
    for entry in legacy_receivables:
        source_app = (entry.source_app or "").strip().lower()
        source_model = (entry.source_model or "").strip().lower()
        base_source_id, public_token = _legacy_identity_keys(
            source_id=entry.source_id,
            legacy_source_id=entry.legacy_source_id,
            public_prefix=RETURN_PUBLIC_PREFIX,
        )
        dedup_key = ""
        is_duplicate = False

        if source_app == "billing" and source_model == "providerreturn":
            if base_source_id in covered_receivable_return_ids:
                is_duplicate = True
                dedup_key = return_preferred_key.get(base_source_id, base_source_id)
            elif public_token and public_token in covered_receivable_return_public:
                is_duplicate = True
                dedup_key = return_preferred_key.get(public_token, public_token)
        if is_duplicate:
            legacy_remaining = _amount_dict_from_row(
                syp=_safe_decimal(entry.total) - _safe_decimal(entry.collected),
                usd=DEC0,
            )
            central_amount = central_receivable_amount_by_key.get(dedup_key, _zero_amount_dict())
            if legacy_remaining != central_amount:
                _append_warning_unique(
                    target_list=diagnostics["dedup_warnings"],
                    payload={
                        "warning_type": "central_legacy_amount_mismatch",
                        "source_identity": {
                            "source_app": "billing",
                            "source_model": "ProviderReturn",
                            "source_id": str(dedup_key),
                        },
                        "central_amount": central_amount,
                        "legacy_amount": legacy_remaining,
                    },
                    seen_keys=dedup_seen,
                )
            continue
        if source_app == "debts" and source_model == "manualdebt" and entry.id in covered_receivable_manual_entry_ids:
            continue
        if source_app == "debts" and source_model == "manualdebt" and has_receivable_manual_nonnumeric:
            _append_warning_unique(
                target_list=diagnostics["dedup_warnings"],
                payload={
                    "warning_type": "manual_identity_desync",
                    "direction": DebtDirection.RECEIVABLE,
                    "source_app": "debts",
                    "source_model": "ManualDebt",
                    "source_id": str(base_source_id),
                },
                seen_keys=dedup_seen,
            )

        remaining = _safe_decimal(entry.total) - _safe_decimal(entry.collected)
        currency_code = (entry.currency_code or "SYP").upper()
        obligations.append(
            {
                "debt_source": "legacy",
                "debt_id": int(entry.id),
                "public_id": None,
                "direction": DebtDirection.RECEIVABLE,
                "cause_type": _legacy_cause_type_from_source(source_app=entry.source_app, source_model=entry.source_model),
                "cause_id": str(base_source_id or entry.source_id or ""),
                "created_at": entry.created_at,
                "remaining_syp": remaining if currency_code == "SYP" else DEC0,
                "remaining_usd": remaining if currency_code == "USD" else DEC0,
            }
        )

    return {
        "provider_id": int(provider_id),
        "obligations": obligations,
        "diagnostics": diagnostics,
    }


def get_provider_net_position(*, provider_id: int) -> dict:
    """
    Read-only provider net position projection.

    Contract:
    - net = receivable - payable
    - positive means provider owes store
    - negative means store owes provider
    - per-currency only (no FX conversion)
    - open obligations only
    - central obligations take precedence over legacy duplicates
    """

    collected = collect_provider_open_obligations(provider_id=provider_id)
    state = _new_state()
    diagnostics = collected["diagnostics"]
    for item in collected["obligations"]:
        _add_amount(
            state=state,
            currency_code="SYP",
            direction=str(item.get("direction") or ""),
            amount=_safe_decimal(item.get("remaining_syp")),
        )
        _add_amount(
            state=state,
            currency_code="USD",
            direction=str(item.get("direction") or ""),
            amount=_safe_decimal(item.get("remaining_usd")),
        )

    out = {
        "provider_id": int(provider_id),
        "currencies": {},
        "diagnostics": diagnostics,
    }
    for code in SUPPORTED_CURRENCIES:
        bucket = state[code]
        receivable = _safe_decimal(bucket["receivable"])
        payable = _safe_decimal(bucket["payable"])
        out["currencies"][code] = {
            "receivable": receivable,
            "payable": payable,
            "net": receivable - payable,
            "open_receivable_count": int(bucket["open_receivable_count"]),
            "open_payable_count": int(bucket["open_payable_count"]),
        }
    return out
