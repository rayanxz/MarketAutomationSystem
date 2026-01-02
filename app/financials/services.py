from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Dict, Any

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import (
    Currency,
    MoneyContainer,
    Counterparty,
    Receipt,
    ReceiptKind,
    ReceiptStatus,
    PostingLine,
    PostingTargetType,
)
import json

DEC0 = Decimal("0")


def q_currency(amount: Decimal, *, currency: Currency) -> Decimal:
    """
    Quantize amount according to currency.decimals.
    """
    if amount is None:
        return DEC0
    places = currency.decimals
    exp = Decimal("1").scaleb(-places)  # 10^-places
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
) -> Receipt:
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
    )
    # IMPORTANT: don't override model default uuid4 with NULL
    if group_key is not None:
        kwargs["group_key"] = group_key

    r = Receipt.objects.create(**kwargs)
    r.ensure_serial()
    r.save(update_fields=["serial"])
    return r



def _post_receipt(r: Receipt) -> Receipt:
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
def post_cash_add(*, actor, container_id: int, currency_code: str, amount: Decimal, note: str = "", **source) -> Receipt:
    container = MoneyContainer.objects.select_for_update().get(pk=container_id)
    currency = Currency.objects.get(code=currency_code)

    amt = q_currency(Decimal(amount), currency=currency)
    if amt <= 0:
        raise ValueError("Cash add amount must be > 0")

    r = _mk_receipt(actor=actor, kind=ReceiptKind.CASH_ADD, note=note, **source)
    _add_line_container(receipt=r, container=container, currency=currency, amount=amt)
    return _post_receipt(r)


@transaction.atomic
def post_cash_withdraw(*, actor, container_id: int, currency_code: str, amount: Decimal, note: str = "", **source) -> Receipt:
    container = MoneyContainer.objects.select_for_update().get(pk=container_id)
    currency = Currency.objects.get(code=currency_code)

    amt = q_currency(Decimal(amount), currency=currency)
    if amt <= 0:
        raise ValueError("Cash withdraw amount must be > 0")

    r = _mk_receipt(actor=actor, kind=ReceiptKind.CASH_WITHDRAW, note=note, **source)
    _add_line_container(receipt=r, container=container, currency=currency, amount=-amt)
    return _post_receipt(r)


@transaction.atomic
def post_transfer(*, actor, from_container_id: int, to_container_id: int, currency_code: str, amount: Decimal, note: str = "", **source) -> Receipt:
    if from_container_id == to_container_id:
        raise ValueError("from_container and to_container cannot be the same")

    # lock both containers (consistent order)
    ids = sorted([from_container_id, to_container_id])
    locked = {c.id: c for c in MoneyContainer.objects.select_for_update().filter(id__in=ids)}
    from_c = locked[from_container_id]
    to_c = locked[to_container_id]

    currency = Currency.objects.get(code=currency_code)
    amt = q_currency(Decimal(amount), currency=currency)
    if amt <= 0:
        raise ValueError("Transfer amount must be > 0")

    r = _mk_receipt(actor=actor, kind=ReceiptKind.CONTAINER_TRANSFER, note=note, **source)
    _add_line_container(receipt=r, container=from_c, currency=currency, amount=-amt, meta={"side": "from"})
    _add_line_container(receipt=r, container=to_c, currency=currency, amount=amt, meta={"side": "to"})
    return _post_receipt(r)


@transaction.atomic
def post_counterparty_adjust(*, actor, counterparty_id: int, currency_code: str, amount_signed: Decimal, note: str = "", **source) -> Receipt:
    """
    Create/adjust an obligation without cash movement.
    Convention:
      + => counterparty owes store (receivable)
      - => store owes counterparty (payable)
    """
    cp = Counterparty.objects.select_for_update().get(pk=counterparty_id)
    currency = Currency.objects.get(code=currency_code)
    amt = q_currency(Decimal(amount_signed), currency=currency)
    if amt == 0:
        raise ValueError("Counterparty adjustment cannot be 0")

    r = _mk_receipt(actor=actor, kind=ReceiptKind.COUNTERPARTY_INC, note=note, **source)
    _add_line_counterparty(receipt=r, counterparty=cp, currency=currency, amount=amt)
    return _post_receipt(r)


@transaction.atomic
def post_settlement(*, actor, container_id: int, counterparty_id: int, currency_code: str, cash_amount_signed: Decimal, note: str = "", **source) -> Receipt:
    """
    Cash settlement between container and counterparty.
    cash_amount_signed:
      + => container increases (we received cash)
      - => container decreases (we paid cash)
    Counterparty line is opposite sign (moves balance toward 0).
    """
    container = MoneyContainer.objects.select_for_update().get(pk=container_id)
    cp = Counterparty.objects.select_for_update().get(pk=counterparty_id)
    currency = Currency.objects.get(code=currency_code)

    cash_amt = q_currency(Decimal(cash_amount_signed), currency=currency)
    if cash_amt == 0:
        raise ValueError("Settlement cash amount cannot be 0")

    r = _mk_receipt(actor=actor, kind=ReceiptKind.COUNTERPARTY_SETTLE, note=note, **source)
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

    r = _mk_receipt(actor=actor, kind=ReceiptKind.REVERSAL, note=reason_note, reverses=orig)

    # negate all lines
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

    # mark original as reversed
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
