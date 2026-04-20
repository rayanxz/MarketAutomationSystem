# financials/services.py
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from typing import Optional, Dict, Any

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from django.db import IntegrityError
from django.db.models import Max, IntegerField
from django.db.models.functions import Cast, Substr

from .models import (
    Currency,
    MoneyContainer,
    Counterparty,
    Receipt,
    ReceiptKind,
    ReceiptStatus,
    PostingLine,
    PostingTargetType,
    MoneyContainerCurrency,
    FxSettings,          # new
)
import json
from accounts.models import AccountProfile
from accounts.utils import has_role

DEC0 = Decimal("0")
FX_DECIMALS = 6

REF_WIDTH = 2

TYPE_PREFIX = {
    "drawer": "CASH",
    "safe": "SAFE",
    "bank": "BANK",
}

def alloc_ref_code(*, container_type: str) -> str:
    prefix = TYPE_PREFIX.get(container_type, "MC")
    start = len(prefix) + 2
    qs = (
        MoneyContainer.objects
        .filter(ref_code__startswith=f"{prefix}-")
        .annotate(n=Cast(Substr("ref_code", start), IntegerField()))
    )
    max_n = qs.aggregate(m=Max("n"))["m"] or 0
    return f"{prefix}-{max_n + 1:0{REF_WIDTH}d}"


# ======================
# FX helpers (GLOBAL)
# ======================
def get_current_fx_syp_per_usd() -> Decimal:
    """
    Returns global FX (SYP per 1 USD).
    Raises ValueError if not set (hard block).
    """
    fx = (
        FxSettings.objects
        .filter(is_active=True)
        .order_by("-updated_at", "-id")
        .first()
    )
    if not fx or fx.rate_syp_per_usd is None or fx.rate_syp_per_usd <= 0:
        raise ValueError("FX is not set. Manager must set FX before money movements.")
    return Decimal(fx.rate_syp_per_usd)


@transaction.atomic
def set_current_fx(*, actor, rate_syp_per_usd: Decimal) -> FxSettings:
    """
    Set global FX used by the system until changed.
    """
    r = q_fx(rate_syp_per_usd)
    if r <= 0:
        raise ValueError("FX rate must be > 0")

    # deactivate previous active rows (keep history)
    FxSettings.objects.filter(is_active=True).update(is_active=False)

    fx = FxSettings.objects.create(
        rate_syp_per_usd=r,
        is_active=True,
        updated_by=actor,
    )
    return fx


def _assert_container_usable(container: MoneyContainer) -> None:
    if not container.is_active:
        raise ValueError("Container is inactive / disabled")


def user_has_money_container_access(*, user, container: MoneyContainer) -> bool:
    """
    Access rule:
    - system owner is always allowed
    - otherwise user must be explicitly in allowed_users
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if has_role(user, AccountProfile.Role.OWNER):
        return True
    return container.allowed_users.filter(pk=user.pk).exists()


def _normalize_feature_codes(feature_code: Any) -> list[str]:
    if feature_code in (None, ""):
        return []
    if isinstance(feature_code, (list, tuple, set)):
        out: list[str] = []
        for raw in feature_code:
            code = str(raw or "").strip()
            if code:
                out.append(code)
        return out
    code = str(feature_code or "").strip()
    return [code] if code else []


def container_supports_feature(*, container: MoneyContainer, feature_code: Any = None) -> bool:
    codes = _normalize_feature_codes(feature_code)
    if not codes:
        return True
    return container.features.filter(code__in=codes, is_active=True).exists()


def assert_money_container_access(
    *,
    user,
    container: MoneyContainer,
    feature_code: Any = None,
) -> None:
    if not container.is_active:
        raise ValueError("Container is inactive / disabled")
    if not container_supports_feature(container=container, feature_code=feature_code):
        raise ValueError("money container does not support this operation")
    if not user_has_money_container_access(user=user, container=container):
        raise ValueError("Container access denied for this user")


def money_containers_for_user_qs(*, user, feature_code: Any = None):
    qs = MoneyContainer.objects.filter(is_active=True)
    codes = _normalize_feature_codes(feature_code)
    if codes:
        qs = qs.filter(features__code__in=codes, features__is_active=True)
    if user is None or not getattr(user, "is_authenticated", False):
        return qs.none()
    if has_role(user, AccountProfile.Role.OWNER):
        return qs.distinct()
    return qs.filter(allowed_users=user).distinct()


def resolve_money_container_for_user(
    *,
    user,
    container_id: int | str,
    feature_code: Any = None,
    for_update: bool = False,
) -> MoneyContainer | None:
    try:
        cid = int(container_id)
    except (TypeError, ValueError):
        return None

    qs = money_containers_for_user_qs(user=user, feature_code=feature_code)
    if for_update:
        qs = qs.select_for_update()
    return qs.filter(pk=cid).first()


def require_money_container_for_user(
    *,
    user,
    container_id: int | str,
    feature_code: Any = None,
    for_update: bool = False,
) -> MoneyContainer:
    try:
        cid = int(container_id)
    except (TypeError, ValueError):
        raise ValueError("invalid money container")

    qs = MoneyContainer.objects
    if for_update:
        qs = qs.select_for_update()
    container = qs.filter(pk=cid).first()
    if container is None:
        raise ValueError("invalid money container")
    if not container.is_active:
        raise ValueError("Container is inactive / disabled")
    if not container_supports_feature(container=container, feature_code=feature_code):
        raise ValueError("money container does not support this operation")
    if not user_has_money_container_access(user=user, container=container):
        raise ValueError("money container is not allowed for this operation")
    return container


def ensure_currency_states(*, container: MoneyContainer) -> None:
    currencies = list(Currency.objects.filter(is_active=True))
    existing = set(
        MoneyContainerCurrency.objects
        .filter(container=container)
        .values_list("currency_id", flat=True)
    )
    to_create = [
        MoneyContainerCurrency(container=container, currency=cur, is_enabled=False)
        for cur in currencies
        if cur.id not in existing
    ]
    if to_create:
        MoneyContainerCurrency.objects.bulk_create(to_create)


def _container_currency_enabled(*, container_id: int, currency_code: str) -> bool:
    return MoneyContainerCurrency.objects.filter(
        container_id=container_id,
        currency__code=currency_code,
        is_enabled=True,
    ).exists()


def _opening_exists(*, container_id: int) -> bool:
    return Receipt.objects.filter(
        kind=ReceiptKind.OPENING_BALANCE,
        source_app="financials",
        source_model="MoneyContainer",
        source_id=str(container_id),
        status__in=[ReceiptStatus.POSTED, ReceiptStatus.REVERSED],
    ).exists()


def q_currency(amount: Decimal, *, currency: Currency) -> Decimal:
    if amount is None:
        return DEC0
    places = currency.decimals
    exp = Decimal("1").scaleb(-places)
    return amount.quantize(exp, rounding=ROUND_HALF_UP)


def q_money(*, amount: Decimal, currency_code: str) -> Decimal:
    code = (currency_code or "").upper().strip()
    if not code:
        raise ValueError("currency_code is required")
    currency = Currency.objects.get(code=code)
    return q_currency(Decimal(amount or DEC0), currency=currency)


def q_fx(value: Decimal) -> Decimal:
    try:
        d = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("Invalid FX rate")
    exp = Decimal("1").scaleb(-FX_DECIMALS)
    return d.quantize(exp, rounding=ROUND_HALF_UP)


def _apply_container_balance_delta(*, container: MoneyContainer, currency_code: str, amount: Decimal) -> None:
    code = (currency_code or "").upper()
    delta = Decimal(amount or DEC0)
    if code == "SYP":
        container.balance_syp = q_money(amount=(container.balance_syp or DEC0) + delta, currency_code="SYP")
    elif code == "USD":
        container.balance_usd = q_money(amount=(container.balance_usd or DEC0) + delta, currency_code="USD")
    else:
        raise ValueError("Unsupported currency for container balance update")
    container.save(update_fields=["balance_syp", "balance_usd"])


def _mk_receipt(
    *,
    actor,
    kind: str,
    note: str = "",
    source_app: str = "",
    source_model: str = "",
    source_id: str = "",
    group_key=None,
    reverses: Optional[Receipt] = None,
    fx_syp_per_usd: Optional[Decimal] = None,
    action_key: str | None = None,
) -> Receipt:
    # Hard block: any created receipt must carry FX (except opening balance).
    fx = q_fx(fx_syp_per_usd) if fx_syp_per_usd is not None else None
    if kind != ReceiptKind.OPENING_BALANCE:
        if fx is None or fx <= 0:
            raise ValueError("FX is required for receipts.")

    kwargs = dict(
        serial="",
        kind=kind,
        status=ReceiptStatus.DRAFT,
        actor=actor,
        note=note or "",
        source_app=source_app or "",
        source_model=source_model or "",
        source_id=str(source_id or ""),
        reverses=reverses,
        fx_syp_per_usd=fx,
        action_key=((action_key or "").strip() or None),
    )
    if group_key is not None:
        kwargs["group_key"] = group_key

    r = Receipt.objects.create(**kwargs)
    r.ensure_serial()
    r.save(update_fields=["serial"])
    return r

def _post_receipt(r: Receipt) -> Receipt:
    # extra safety: refuse posting if FX missing
    if r.kind != ReceiptKind.OPENING_BALANCE:
        if r.fx_syp_per_usd is None or Decimal(r.fx_syp_per_usd) <= 0:
            raise ValueError("Cannot post receipt without valid FX.")
    r.status = ReceiptStatus.POSTED
    r.posted_at = timezone.now()
    r.save(update_fields=["status", "posted_at"])
    return r


def _add_line_container(*, receipt: Receipt, container: MoneyContainer, currency: Currency, amount: Decimal, meta: Optional[Dict[str, Any]] = None) -> PostingLine:
    return PostingLine.objects.create(
        receipt=receipt,
        target_type=PostingTargetType.CONTAINER,
        container=container,
        counterparty=None,
        currency=currency,
        amount=amount,
        meta_json=json.dumps(meta or {}, ensure_ascii=False),
    )


def _add_line_counterparty(*, receipt: Receipt, counterparty: Counterparty, currency: Currency, amount: Decimal, meta: Optional[Dict[str, Any]] = None) -> PostingLine:
    return PostingLine.objects.create(
        receipt=receipt,
        target_type=PostingTargetType.COUNTERPARTY,
        container=None,
        counterparty=counterparty,
        currency=currency,
        amount=amount,
        meta_json=json.dumps(meta or {}, ensure_ascii=False),
    )


@transaction.atomic
def post_initial_balance(*, actor, container_id: int, amounts_by_code: Dict[str, Decimal], note: str = "رصيد افتتاحي") -> Receipt:
    try:
        fx = get_current_fx_syp_per_usd()
    except ValueError:
        fx = None
    container = MoneyContainer.objects.select_for_update().get(pk=container_id)
    _assert_container_usable(container)

    if _opening_exists(container_id=container_id):
        raise ValueError("Initial balance already posted for this container")

    cleaned: Dict[str, Decimal] = {}
    for code, raw in (amounts_by_code or {}).items():
        currency = Currency.objects.get(code=code)
        amt = q_currency(Decimal(raw), currency=currency)
        if amt == 0:
            continue
        if not _container_currency_enabled(container_id=container_id, currency_code=code):
            raise ValueError(f"Currency {code} is disabled for this container")
        cleaned[code] = amt

    if not cleaned:
        raise ValueError("Initial balance amounts are all zero")

    r = _mk_receipt(
        actor=actor,
        kind=ReceiptKind.OPENING_BALANCE,
        note=note,
        source_app="financials",
        source_model="MoneyContainer",
        source_id=str(container.id),
        fx_syp_per_usd=fx,
    )

    for code, amt in cleaned.items():
        currency = Currency.objects.get(code=code)
        _add_line_container(receipt=r, container=container, currency=currency, amount=amt, meta={"opening": True})

    _post_receipt(r)
    for code, amt in cleaned.items():
        _apply_container_balance_delta(container=container, currency_code=code, amount=amt)
    return r


@transaction.atomic
def post_cash_add(*, actor, container_id: int, currency_code: str, amount: Decimal, note: str = "", **source) -> Receipt:
    fx = get_current_fx_syp_per_usd()
    container = MoneyContainer.objects.select_for_update().get(pk=container_id)
    _assert_container_usable(container)
    currency = Currency.objects.get(code=currency_code)

    if not _container_currency_enabled(container_id=container_id, currency_code=currency_code):
        raise ValueError(f"Currency {currency_code} is disabled for this container")

    amt = q_currency(Decimal(amount), currency=currency)
    if amt <= 0:
        raise ValueError("Cash add amount must be > 0")

    r = _mk_receipt(actor=actor, kind=ReceiptKind.CASH_ADD, note=note, fx_syp_per_usd=fx, **source)
    _add_line_container(receipt=r, container=container, currency=currency, amount=amt)
    _post_receipt(r)
    _apply_container_balance_delta(container=container, currency_code=currency_code, amount=amt)
    return r


@transaction.atomic
def post_pos_sale_receipt(
    *,
    actor,
    container_id: int,
    amounts_by_code: Dict[str, Decimal],
    fx_syp_per_usd: Optional[Decimal] = None,
    note: str = "",
    **source,
) -> Receipt:
    """
    POS cash-in: post a receipt with container lines for SYP/USD amounts,
    and update container balances in the same transaction.
    """
    fx = q_fx(fx_syp_per_usd) if fx_syp_per_usd is not None else get_current_fx_syp_per_usd()
    container = MoneyContainer.objects.select_for_update().get(pk=container_id)
    _assert_container_usable(container)

    cleaned: Dict[str, Decimal] = {}
    for code, raw in (amounts_by_code or {}).items():
        if raw is None:
            continue
        currency = Currency.objects.get(code=code)
        amt = q_currency(Decimal(raw), currency=currency)
        if amt == 0:
            continue
        if not _container_currency_enabled(container_id=container_id, currency_code=code):
            raise ValueError(f"Currency {code} is disabled for this container")
        cleaned[code] = amt

    if not cleaned:
        raise ValueError("POS receipt amounts are all zero")

    r = _mk_receipt(
        actor=actor,
        kind=ReceiptKind.CASH_ADD,
        note=note,
        fx_syp_per_usd=fx,
        **source,
    )

    for code, amt in cleaned.items():
        currency = Currency.objects.get(code=code)
        _add_line_container(receipt=r, container=container, currency=currency, amount=amt)

    _post_receipt(r)

    delta_syp = cleaned.get("SYP", DEC0)
    delta_usd = cleaned.get("USD", DEC0)
    if delta_syp:
        container.balance_syp = q_money(amount=(container.balance_syp or DEC0) + delta_syp, currency_code="SYP")
    if delta_usd:
        container.balance_usd = q_money(amount=(container.balance_usd or DEC0) + delta_usd, currency_code="USD")
    container.save(update_fields=["balance_syp", "balance_usd"])

    return r


@transaction.atomic
def post_cash_withdraw(*, actor, container_id: int, currency_code: str, amount: Decimal, note: str = "", **source) -> Receipt:
    fx = get_current_fx_syp_per_usd()
    container = MoneyContainer.objects.select_for_update().get(pk=container_id)
    _assert_container_usable(container)
    currency = Currency.objects.get(code=currency_code)

    if not _container_currency_enabled(container_id=container_id, currency_code=currency_code):
        raise ValueError(f"Currency {currency_code} is disabled for this container")

    amt = q_currency(Decimal(amount), currency=currency)
    if amt <= 0:
        raise ValueError("Cash withdraw amount must be > 0")

    r = _mk_receipt(actor=actor, kind=ReceiptKind.CASH_WITHDRAW, note=note, fx_syp_per_usd=fx, **source)
    _add_line_container(receipt=r, container=container, currency=currency, amount=-amt)
    _post_receipt(r)
    _apply_container_balance_delta(container=container, currency_code=currency_code, amount=-amt)
    return r


@transaction.atomic
def post_transfer(*, actor, from_container_id: int, to_container_id: int, currency_code: str, amount: Decimal, note: str = "", **source) -> Receipt:
    fx = get_current_fx_syp_per_usd()
    if from_container_id == to_container_id:
        raise ValueError("from_container and to_container cannot be the same")

    ids = sorted([from_container_id, to_container_id])
    locked = {c.id: c for c in MoneyContainer.objects.select_for_update().filter(id__in=ids)}
    from_c = locked[from_container_id]
    to_c = locked[to_container_id]

    _assert_container_usable(from_c)
    _assert_container_usable(to_c)

    currency = Currency.objects.get(code=currency_code)
    if not _container_currency_enabled(container_id=from_container_id, currency_code=currency_code):
        raise ValueError("Currency disabled for source container")
    if not _container_currency_enabled(container_id=to_container_id, currency_code=currency_code):
        raise ValueError("Currency disabled for destination container")

    amt = q_currency(Decimal(amount), currency=currency)
    if amt <= 0:
        raise ValueError("Transfer amount must be > 0")

    r = _mk_receipt(actor=actor, kind=ReceiptKind.CONTAINER_TRANSFER, note=note, fx_syp_per_usd=fx, **source)
    _add_line_container(receipt=r, container=from_c, currency=currency, amount=-amt, meta={"side": "from"})
    _add_line_container(receipt=r, container=to_c, currency=currency, amount=amt, meta={"side": "to"})
    _post_receipt(r)
    _apply_container_balance_delta(container=from_c, currency_code=currency_code, amount=-amt)
    _apply_container_balance_delta(container=to_c, currency_code=currency_code, amount=amt)
    return r


@transaction.atomic
def post_counterparty_adjust(*, actor, counterparty_id: int, currency_code: str, amount_signed: Decimal, note: str = "", **source) -> Receipt:
    fx = get_current_fx_syp_per_usd()
    cp = Counterparty.objects.select_for_update().get(pk=counterparty_id)
    currency = Currency.objects.get(code=currency_code)
    amt = q_currency(Decimal(amount_signed), currency=currency)
    if amt == 0:
        raise ValueError("Counterparty adjustment cannot be 0")

    r = _mk_receipt(actor=actor, kind=ReceiptKind.COUNTERPARTY_INC, note=note, fx_syp_per_usd=fx, **source)
    _add_line_counterparty(receipt=r, counterparty=cp, currency=currency, amount=amt)
    return _post_receipt(r)


@transaction.atomic
def post_counterparty_adjust_with_fx(
    *,
    actor,
    counterparty_id: int,
    currency_code: str,
    amount_signed: Decimal,
    fx_syp_per_usd: Decimal | None,
    note: str = "",
    **source,
) -> Receipt:
    fx = q_fx(fx_syp_per_usd) if fx_syp_per_usd is not None else get_current_fx_syp_per_usd()
    if fx <= 0:
        raise ValueError("FX is required for receipts.")
    cp = Counterparty.objects.select_for_update().get(pk=counterparty_id)
    currency = Currency.objects.get(code=currency_code)
    amt = q_currency(Decimal(amount_signed), currency=currency)
    if amt == 0:
        raise ValueError("Counterparty adjustment cannot be 0")

    r = _mk_receipt(actor=actor, kind=ReceiptKind.COUNTERPARTY_INC, note=note, fx_syp_per_usd=fx, **source)
    _add_line_counterparty(receipt=r, counterparty=cp, currency=currency, amount=amt)
    return _post_receipt(r)


@transaction.atomic
def post_settlement(*, actor, container_id: int, counterparty_id: int, currency_code: str, cash_amount_signed: Decimal, note: str = "", **source) -> Receipt:
    fx = get_current_fx_syp_per_usd()
    container = MoneyContainer.objects.select_for_update().get(pk=container_id)
    _assert_container_usable(container)

    cp = Counterparty.objects.select_for_update().get(pk=counterparty_id)
    currency = Currency.objects.get(code=currency_code)

    if not _container_currency_enabled(container_id=container_id, currency_code=currency_code):
        raise ValueError(f"Currency {currency_code} is disabled for this container")

    cash_amt = q_currency(Decimal(cash_amount_signed), currency=currency)
    if cash_amt == 0:
        raise ValueError("Settlement cash amount cannot be 0")

    r = _mk_receipt(actor=actor, kind=ReceiptKind.COUNTERPARTY_SETTLE, note=note, fx_syp_per_usd=fx, **source)
    _add_line_container(receipt=r, container=container, currency=currency, amount=cash_amt)
    _add_line_counterparty(receipt=r, counterparty=cp, currency=currency, amount=-cash_amt)
    _post_receipt(r)
    _apply_container_balance_delta(container=container, currency_code=currency_code, amount=cash_amt)
    return r


@transaction.atomic
def post_settlement_with_fx(
    *,
    actor,
    container_id: int,
    counterparty_id: int,
    currency_code: str,
    cash_amount_signed: Decimal,
    fx_syp_per_usd: Decimal | None,
    note: str = "",
    **source,
) -> Receipt:
    fx = q_fx(fx_syp_per_usd) if fx_syp_per_usd is not None else get_current_fx_syp_per_usd()
    if fx <= 0:
        raise ValueError("FX is required for receipts.")
    container = MoneyContainer.objects.select_for_update().get(pk=container_id)
    _assert_container_usable(container)

    cp = Counterparty.objects.select_for_update().get(pk=counterparty_id)
    currency = Currency.objects.get(code=currency_code)

    if not _container_currency_enabled(container_id=container_id, currency_code=currency_code):
        raise ValueError(f"Currency {currency_code} is disabled for this container")

    cash_amt = q_currency(Decimal(cash_amount_signed), currency=currency)
    if cash_amt == 0:
        raise ValueError("Settlement cash amount cannot be 0")

    r = _mk_receipt(actor=actor, kind=ReceiptKind.COUNTERPARTY_SETTLE, note=note, fx_syp_per_usd=fx, **source)
    _add_line_container(receipt=r, container=container, currency=currency, amount=cash_amt)
    _add_line_counterparty(receipt=r, counterparty=cp, currency=currency, amount=-cash_amt)
    _post_receipt(r)
    _apply_container_balance_delta(container=container, currency_code=currency_code, amount=cash_amt)
    return r


@transaction.atomic
def post_settlement_components_with_fx(
    *,
    actor,
    container_id: int,
    counterparty_id: int,
    cash_by_code_signed: Dict[str, Decimal],
    fx_syp_per_usd: Decimal | None,
    note: str = "",
    **source,
) -> Receipt:
    """
    Post one settlement receipt that can include multiple currencies.

    `cash_by_code_signed` values represent container deltas:
    - negative: cash out of container
    - positive: cash into container
    """
    fx = q_fx(fx_syp_per_usd) if fx_syp_per_usd is not None else get_current_fx_syp_per_usd()
    if fx <= 0:
        raise ValueError("FX is required for receipts.")

    container = MoneyContainer.objects.select_for_update().get(pk=container_id)
    _assert_container_usable(container)
    cp = Counterparty.objects.select_for_update().get(pk=counterparty_id)

    cleaned: Dict[str, tuple[Currency, Decimal]] = {}
    for code_raw, raw_amount in (cash_by_code_signed or {}).items():
        code = (code_raw or "").upper().strip()
        if not code:
            raise ValueError("cash_by_code_signed contains an empty currency code")
        currency = Currency.objects.get(code=code)
        if not _container_currency_enabled(container_id=container.id, currency_code=code):
            raise ValueError(f"Currency {code} is disabled for this container")
        amt = q_currency(Decimal(raw_amount or DEC0), currency=currency)
        if amt == 0:
            continue
        cleaned[code] = (currency, amt)

    if not cleaned:
        raise ValueError("Settlement cash amounts cannot all be 0")

    r = _mk_receipt(
        actor=actor,
        kind=ReceiptKind.COUNTERPARTY_SETTLE,
        note=note,
        fx_syp_per_usd=fx,
        **source,
    )

    for code in sorted(cleaned.keys()):
        currency, amt = cleaned[code]
        _add_line_container(
            receipt=r,
            container=container,
            currency=currency,
            amount=amt,
        )
        _add_line_counterparty(
            receipt=r,
            counterparty=cp,
            currency=currency,
            amount=-amt,
        )

    _post_receipt(r)

    for code in sorted(cleaned.keys()):
        _, amt = cleaned[code]
        _apply_container_balance_delta(
            container=container,
            currency_code=code,
            amount=amt,
        )
    return r


@transaction.atomic
def post_counterparty_bill_action_with_fx(
    *,
    actor,
    counterparty_id: int,
    totals_by_code: Dict[str, Decimal],
    settled_counterparty_by_code: Dict[str, Decimal] | None = None,
    container_paid_by_code: Dict[str, Decimal] | None = None,
    container_id: int | None = None,
    fx_syp_per_usd: Decimal | None,
    action_key: str | None = None,
    note: str = "",
    **source,
) -> Receipt:
    """
    Post one canonical receipt for a purchase-bill action.

    - Counterparty total increase: negative counterparty lines (per bill total currency).
    - Counterparty settlement: positive counterparty lines (per paid allocation currency).
    - Cash movement: negative container lines (per actual paid currency).

    This keeps one logical bill action represented by one receipt while preserving
    multi-currency semantics.
    """
    fx = q_fx(fx_syp_per_usd) if fx_syp_per_usd is not None else get_current_fx_syp_per_usd()
    if fx <= 0:
        raise ValueError("FX is required for receipts.")

    action_key_norm = (action_key or "").strip() or None
    if action_key_norm:
        existing = (
            Receipt.objects
            .select_for_update()
            .filter(action_key=action_key_norm)
            .first()
        )
        if existing is not None:
            return existing

    cp = Counterparty.objects.select_for_update().get(pk=counterparty_id)

    def _normalize_amounts(raw_map: Dict[str, Decimal] | None, *, field_name: str) -> Dict[str, Decimal]:
        cleaned: Dict[str, Decimal] = {}
        for code_raw, raw in (raw_map or {}).items():
            code = (code_raw or "").upper().strip()
            if not code:
                raise ValueError(f"{field_name} currency code is required")
            cur = Currency.objects.get(code=code)
            amt = q_currency(Decimal(raw or DEC0), currency=cur)
            if amt < 0:
                raise ValueError(f"{field_name} amount cannot be negative")
            if amt == 0:
                continue
            cleaned[code] = amt
        return cleaned

    total_map = _normalize_amounts(totals_by_code, field_name="totals_by_code")
    if not total_map:
        raise ValueError("Bill totals cannot be all zero")

    settled_cp_map = _normalize_amounts(
        settled_counterparty_by_code,
        field_name="settled_counterparty_by_code",
    )
    container_paid_map = _normalize_amounts(
        container_paid_by_code,
        field_name="container_paid_by_code",
    )

    for code, settled in settled_cp_map.items():
        total_for_code = total_map.get(code, DEC0)
        if settled > total_for_code:
            raise ValueError(f"Counterparty settled amount exceeds bill total in {code}")

    if container_paid_map and not container_id:
        raise ValueError("container_id is required when cash payment exists")

    container = None
    if container_paid_map:
        container = MoneyContainer.objects.select_for_update().get(pk=container_id)
        _assert_container_usable(container)
        for code in container_paid_map.keys():
            if not _container_currency_enabled(container_id=container.id, currency_code=code):
                raise ValueError(f"Currency {code} is disabled for this container")

    kind = ReceiptKind.COUNTERPARTY_SETTLE if container_paid_map else ReceiptKind.COUNTERPARTY_INC
    try:
        r = _mk_receipt(
            actor=actor,
            kind=kind,
            note=note,
            fx_syp_per_usd=fx,
            action_key=action_key_norm,
            **source,
        )
    except IntegrityError:
        if action_key_norm:
            existing = (
                Receipt.objects
                .select_for_update()
                .filter(action_key=action_key_norm)
                .first()
            )
            if existing is not None:
                return existing
        raise

    for code in sorted(total_map.keys()):
        currency = Currency.objects.get(code=code)
        total_amt = total_map[code]
        _add_line_counterparty(
            receipt=r,
            counterparty=cp,
            currency=currency,
            amount=-total_amt,
            meta={"component": "bill_total"},
        )

    for code in sorted(settled_cp_map.keys()):
        currency = Currency.objects.get(code=code)
        settled_amt = settled_cp_map[code]
        _add_line_counterparty(
            receipt=r,
            counterparty=cp,
            currency=currency,
            amount=settled_amt,
            meta={"component": "bill_settlement"},
        )

    if container and container_paid_map:
        for code in sorted(container_paid_map.keys()):
            currency = Currency.objects.get(code=code)
            paid_amt = container_paid_map[code]
            _add_line_container(
                receipt=r,
                container=container,
                currency=currency,
                amount=-paid_amt,
                meta={"component": "bill_cash_out"},
            )

    _post_receipt(r)

    if container and container_paid_map:
        for code in sorted(container_paid_map.keys()):
            _apply_container_balance_delta(
                container=container,
                currency_code=code,
                amount=-container_paid_map[code],
            )

    return r


@transaction.atomic
def post_counterparty_return_action_with_fx(
    *,
    actor,
    counterparty_id: int,
    totals_by_code: Dict[str, Decimal],
    settled_counterparty_by_code: Dict[str, Decimal] | None = None,
    container_collected_by_code: Dict[str, Decimal] | None = None,
    container_id: int | None = None,
    fx_syp_per_usd: Decimal | None,
    action_key: str | None = None,
    note: str = "",
    **source,
) -> Receipt:
    """
    Post one canonical receipt for a provider-return action.

    - Counterparty total increase: positive counterparty lines (per return total currency).
    - Counterparty settlement: negative counterparty lines (per collected allocation currency).
    - Cash movement: positive container lines (per actual collected currency).
    """
    fx = q_fx(fx_syp_per_usd) if fx_syp_per_usd is not None else get_current_fx_syp_per_usd()
    if fx <= 0:
        raise ValueError("FX is required for receipts.")

    action_key_norm = (action_key or "").strip() or None
    if action_key_norm:
        existing = (
            Receipt.objects
            .select_for_update()
            .filter(action_key=action_key_norm)
            .first()
        )
        if existing is not None:
            return existing

    cp = Counterparty.objects.select_for_update().get(pk=counterparty_id)

    def _normalize_amounts(raw_map: Dict[str, Decimal] | None, *, field_name: str) -> Dict[str, Decimal]:
        cleaned: Dict[str, Decimal] = {}
        for code_raw, raw in (raw_map or {}).items():
            code = (code_raw or "").upper().strip()
            if not code:
                raise ValueError(f"{field_name} currency code is required")
            cur = Currency.objects.get(code=code)
            amt = q_currency(Decimal(raw or DEC0), currency=cur)
            if amt < 0:
                raise ValueError(f"{field_name} amount cannot be negative")
            if amt == 0:
                continue
            cleaned[code] = amt
        return cleaned

    total_map = _normalize_amounts(totals_by_code, field_name="totals_by_code")
    if not total_map:
        raise ValueError("Return totals cannot be all zero")

    settled_cp_map = _normalize_amounts(
        settled_counterparty_by_code,
        field_name="settled_counterparty_by_code",
    )
    container_collected_map = _normalize_amounts(
        container_collected_by_code,
        field_name="container_collected_by_code",
    )

    for code, settled in settled_cp_map.items():
        total_for_code = total_map.get(code, DEC0)
        if settled > total_for_code:
            raise ValueError(f"Counterparty settled amount exceeds return total in {code}")

    if container_collected_map and not container_id:
        raise ValueError("container_id is required when cash collection exists")

    container = None
    if container_collected_map:
        container = MoneyContainer.objects.select_for_update().get(pk=container_id)
        _assert_container_usable(container)
        for code in container_collected_map.keys():
            if not _container_currency_enabled(container_id=container.id, currency_code=code):
                raise ValueError(f"Currency {code} is disabled for this container")

    kind = ReceiptKind.COUNTERPARTY_SETTLE if container_collected_map else ReceiptKind.COUNTERPARTY_INC
    try:
        r = _mk_receipt(
            actor=actor,
            kind=kind,
            note=note,
            fx_syp_per_usd=fx,
            action_key=action_key_norm,
            **source,
        )
    except IntegrityError:
        if action_key_norm:
            existing = (
                Receipt.objects
                .select_for_update()
                .filter(action_key=action_key_norm)
                .first()
            )
            if existing is not None:
                return existing
        raise

    for code in sorted(total_map.keys()):
        currency = Currency.objects.get(code=code)
        total_amt = total_map[code]
        _add_line_counterparty(
            receipt=r,
            counterparty=cp,
            currency=currency,
            amount=total_amt,
            meta={"component": "return_total"},
        )

    for code in sorted(settled_cp_map.keys()):
        currency = Currency.objects.get(code=code)
        settled_amt = settled_cp_map[code]
        _add_line_counterparty(
            receipt=r,
            counterparty=cp,
            currency=currency,
            amount=-settled_amt,
            meta={"component": "return_settlement"},
        )

    if container and container_collected_map:
        for code in sorted(container_collected_map.keys()):
            currency = Currency.objects.get(code=code)
            collected_amt = container_collected_map[code]
            _add_line_container(
                receipt=r,
                container=container,
                currency=currency,
                amount=collected_amt,
                meta={"component": "return_cash_in"},
            )

    _post_receipt(r)

    if container and container_collected_map:
        for code in sorted(container_collected_map.keys()):
            _apply_container_balance_delta(
                container=container,
                currency_code=code,
                amount=container_collected_map[code],
            )

    return r


@transaction.atomic
def post_counterparty_sale_action_with_fx(
    *,
    actor,
    counterparty_id: int,
    totals_by_code: Dict[str, Decimal],
    settled_counterparty_by_code: Dict[str, Decimal] | None = None,
    container_collected_by_code: Dict[str, Decimal] | None = None,
    container_id: int | None = None,
    fx_syp_per_usd: Decimal | None,
    action_key: str | None = None,
    note: str = "",
    **source,
) -> Receipt:
    """
    Canonical POS sale posting.
    Sign semantics are equivalent to provider-return action:
    counterparty total increases, settlement reduces it, cash moves into container.
    """
    return post_counterparty_return_action_with_fx(
        actor=actor,
        counterparty_id=counterparty_id,
        totals_by_code=totals_by_code,
        settled_counterparty_by_code=settled_counterparty_by_code,
        container_collected_by_code=container_collected_by_code,
        container_id=container_id,
        fx_syp_per_usd=fx_syp_per_usd,
        action_key=action_key,
        note=note,
        **source,
    )


@transaction.atomic
def post_counterparty_sale_return_action_with_fx(
    *,
    actor,
    counterparty_id: int,
    totals_by_code: Dict[str, Decimal],
    settled_counterparty_by_code: Dict[str, Decimal] | None = None,
    container_paid_by_code: Dict[str, Decimal] | None = None,
    container_id: int | None = None,
    fx_syp_per_usd: Decimal | None,
    action_key: str | None = None,
    note: str = "",
    **source,
) -> Receipt:
    """
    Canonical POS sales-return posting.
    Sign semantics are equivalent to provider-bill action:
    counterparty total decreases, settlement offsets it, cash moves out of container.
    """
    return post_counterparty_bill_action_with_fx(
        actor=actor,
        counterparty_id=counterparty_id,
        totals_by_code=totals_by_code,
        settled_counterparty_by_code=settled_counterparty_by_code,
        container_paid_by_code=container_paid_by_code,
        container_id=container_id,
        fx_syp_per_usd=fx_syp_per_usd,
        action_key=action_key,
        note=note,
        **source,
    )


@transaction.atomic
def reverse_receipt(*, actor, receipt_id: int, reason_note: str = "") -> Receipt:
    orig = (
        Receipt.objects.select_for_update()
        .prefetch_related("lines")
        .get(pk=receipt_id)
    )
    if orig.status != ReceiptStatus.POSTED:
        raise ValueError("Only POSTED receipts can be reversed")
    if orig.reversed_by.exists():
        raise ValueError("Receipt is already reversed")

    # reversal copies FX snapshot from original
    if orig.fx_syp_per_usd is None or Decimal(orig.fx_syp_per_usd) <= 0:
        raise ValueError("Original receipt has no FX; cannot reverse safely.")

    r = _mk_receipt(
        actor=actor,
        kind=ReceiptKind.REVERSAL,
        note=reason_note,
        reverses=orig,
        fx_syp_per_usd=q_fx(orig.fx_syp_per_usd),
    )

    for ln in orig.lines.all():
        if ln.target_type == PostingTargetType.CONTAINER:
            _add_line_container(
                receipt=r,
                container=ln.container,
                currency=ln.currency,
                amount=-ln.amount,
                meta={"reversal_of_line": ln.id},
            )
        else:
            _add_line_counterparty(
                receipt=r,
                counterparty=ln.counterparty,
                currency=ln.currency,
                amount=-ln.amount,
                meta={"reversal_of_line": ln.id},
            )

    _post_receipt(r)

    # apply container balance deltas for reversal lines
    for ln in r.lines.all():
        if ln.target_type == PostingTargetType.CONTAINER and ln.container_id:
            _apply_container_balance_delta(
                container=ln.container,
                currency_code=ln.currency.code,
                amount=ln.amount,
            )

    orig.status = ReceiptStatus.REVERSED
    orig.save(update_fields=["status"])
    return r


def container_balance(*, container_id: int) -> dict[str, Decimal]:
    qs = (
        PostingLine.objects
        .filter(
            target_type=PostingTargetType.CONTAINER,
            container_id=container_id,
            receipt__status__in=[ReceiptStatus.POSTED, ReceiptStatus.REVERSED],
        )
        .values("currency__code")
        .annotate(s=Sum("amount"))
        .order_by()
    )
    return {row["currency__code"]: (row["s"] or DEC0) for row in qs}


def counterparty_balance(*, counterparty_id: int) -> dict[str, Decimal]:
    qs = (
        PostingLine.objects
        .filter(
            target_type=PostingTargetType.COUNTERPARTY,
            counterparty_id=counterparty_id,
            receipt__status__in=[ReceiptStatus.POSTED, ReceiptStatus.REVERSED],
        )
        .values("currency__code")
        .annotate(s=Sum("amount"))
        .order_by()
    )
    return {row["currency__code"]: (row["s"] or DEC0) for row in qs}

