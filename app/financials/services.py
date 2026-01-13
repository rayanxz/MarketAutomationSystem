# financials/services.py
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
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
    FxSettings,          # ✅ new
)
import json

DEC0 = Decimal("0")

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
    r = Decimal(rate_syp_per_usd)
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
    fx_syp_per_usd: Optional[Decimal] = None,  # ✅ new
) -> Receipt:
    # ✅ hard block: any created receipt must carry FX
    fx = Decimal(fx_syp_per_usd) if fx_syp_per_usd is not None else None
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
        fx_syp_per_usd=fx,  # ✅ stored snapshot
    )
    if group_key is not None:
        kwargs["group_key"] = group_key

    r = Receipt.objects.create(**kwargs)
    r.ensure_serial()
    r.save(update_fields=["serial"])
    return r


def _post_receipt(r: Receipt) -> Receipt:
    # extra safety: refuse posting if FX missing
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
    fx = get_current_fx_syp_per_usd()  # ✅ required
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

    return _post_receipt(r)


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
    return _post_receipt(r)


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
    fx = Decimal(fx_syp_per_usd) if fx_syp_per_usd is not None else get_current_fx_syp_per_usd()
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
        container.balance_syp = q_currency(container.balance_syp + delta_syp, currency=Currency.objects.get(code="SYP"))
    if delta_usd:
        container.balance_usd = q_currency(container.balance_usd + delta_usd, currency=Currency.objects.get(code="USD"))
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
    return _post_receipt(r)


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
    return _post_receipt(r)


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
    fx = Decimal(fx_syp_per_usd) if fx_syp_per_usd is not None else get_current_fx_syp_per_usd()
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
    return _post_receipt(r)


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
    fx = Decimal(fx_syp_per_usd) if fx_syp_per_usd is not None else get_current_fx_syp_per_usd()
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
    return _post_receipt(r)


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

    # ✅ reversal copies FX snapshot from original
    if orig.fx_syp_per_usd is None or Decimal(orig.fx_syp_per_usd) <= 0:
        raise ValueError("Original receipt has no FX; cannot reverse safely.")

    r = _mk_receipt(
        actor=actor,
        kind=ReceiptKind.REVERSAL,
        note=reason_note,
        reverses=orig,
        fx_syp_per_usd=Decimal(orig.fx_syp_per_usd),
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
