# app/financials/manual_events.py
from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from django.db import transaction

from financials import services as FSV
from financials.models import MoneyContainer, MoneyContainerCurrency, Currency, Receipt, ReceiptKind


def _currency_enabled(*, container_id: int, currency_code: str) -> bool:
    return MoneyContainerCurrency.objects.filter(
        container_id=container_id,
        currency__code=currency_code,
        is_enabled=True,
    ).exists()


def _balance_for(container: MoneyContainer, currency_code: str) -> Decimal:
    if currency_code == "USD":
        return container.balance_usd
    return container.balance_syp


def _quantize(currency_code: str, amount: Decimal) -> Decimal:
    return FSV.q_money(amount=Decimal(amount or 0), currency_code=currency_code)


def _assert_sufficient(*, container: MoneyContainer, currency_code: str, amount: Decimal) -> None:
    bal = _balance_for(container, currency_code)
    amt = _quantize(currency_code, amount)
    if amt > _quantize(currency_code, bal):
        raise ValueError("INSUFFICIENT_FUNDS")


@transaction.atomic
def post_manual_add(*, actor, container_id: int, currency_code: str, amount: Decimal, note: str = "") -> Receipt:
    FSV.require_money_container_for_user(user=actor, container_id=container_id)
    if not _currency_enabled(container_id=container_id, currency_code=currency_code):
        raise ValueError("CURRENCY_DISABLED")
    src = f"ADD:{uuid4().hex}"
    return FSV.post_cash_add(
        actor=actor,
        container_id=container_id,
        currency_code=currency_code,
        amount=amount,
        note=note or "Manual add",
        source_app="financials",
        source_model="ManualContainerEvent",
        source_id=src,
    )


@transaction.atomic
def post_manual_withdraw(*, actor, container_id: int, currency_code: str, amount: Decimal, note: str = "") -> Receipt:
    FSV.require_money_container_for_user(user=actor, container_id=container_id)
    if not _currency_enabled(container_id=container_id, currency_code=currency_code):
        raise ValueError("CURRENCY_DISABLED")
    container = MoneyContainer.objects.select_for_update().get(pk=container_id)
    FSV.assert_money_container_access(user=actor, container=container)
    _assert_sufficient(container=container, currency_code=currency_code, amount=amount)
    src = f"WITHDRAW:{uuid4().hex}"
    return FSV.post_cash_withdraw(
        actor=actor,
        container_id=container_id,
        currency_code=currency_code,
        amount=amount,
        note=note or "Manual withdraw",
        source_app="financials",
        source_model="ManualContainerEvent",
        source_id=src,
    )


@transaction.atomic
def post_manual_transfer(
    *,
    actor,
    from_container_id: int,
    to_container_id: int,
    currency_code: str,
    amount: Decimal,
    note: str = "",
) -> Receipt:
    FSV.require_money_container_for_user(user=actor, container_id=from_container_id)
    FSV.require_money_container_for_user(user=actor, container_id=to_container_id)
    if not _currency_enabled(container_id=from_container_id, currency_code=currency_code):
        raise ValueError("CURRENCY_DISABLED_FROM")
    if not _currency_enabled(container_id=to_container_id, currency_code=currency_code):
        raise ValueError("CURRENCY_DISABLED_TO")
    from_container = MoneyContainer.objects.select_for_update().get(pk=from_container_id)
    to_container = MoneyContainer.objects.select_for_update().get(pk=to_container_id)
    FSV.assert_money_container_access(user=actor, container=from_container)
    FSV.assert_money_container_access(user=actor, container=to_container)
    _assert_sufficient(container=from_container, currency_code=currency_code, amount=amount)
    src = f"TRANSFER:{uuid4().hex}"
    return FSV.post_transfer(
        actor=actor,
        from_container_id=from_container_id,
        to_container_id=to_container_id,
        currency_code=currency_code,
        amount=amount,
        note=note or "Manual transfer",
        source_app="financials",
        source_model="ManualContainerEvent",
        source_id=src,
    )


@transaction.atomic
def post_manual_exchange(
    *,
    actor,
    from_container_id: int,
    to_container_id: int | None,
    currency_from: str,
    currency_to: str,
    amount_from: Decimal,
    fx_syp_per_usd: Decimal | None,
    note: str = "",
) -> Receipt:
    cur_from = (currency_from or "").upper()
    cur_to = (currency_to or "").upper()
    if cur_from == cur_to:
        raise ValueError("CURRENCY_SAME")
    if cur_from not in {"SYP", "USD"} or cur_to not in {"SYP", "USD"}:
        raise ValueError("INVALID_CURRENCY")

    target_container_id = to_container_id or from_container_id
    FSV.require_money_container_for_user(user=actor, container_id=from_container_id)
    FSV.require_money_container_for_user(user=actor, container_id=target_container_id)

    if not _currency_enabled(container_id=from_container_id, currency_code=cur_from):
        raise ValueError("CURRENCY_DISABLED_FROM")
    if not _currency_enabled(container_id=target_container_id, currency_code=cur_to):
        raise ValueError("CURRENCY_DISABLED_TO")

    fx = FSV.q_fx(fx_syp_per_usd) if fx_syp_per_usd is not None else FSV.get_current_fx_syp_per_usd()
    if fx <= 0:
        raise ValueError("FX_REQUIRED")

    amt_from = _quantize(cur_from, amount_from)
    if amt_from <= 0:
        raise ValueError("AMOUNT_REQUIRED")

    if cur_from == "USD" and cur_to == "SYP":
        amt_to = _quantize("SYP", amt_from * fx)
    else:
        amt_to = _quantize("USD", amt_from / fx)

    from_container = MoneyContainer.objects.select_for_update().get(pk=from_container_id)
    if target_container_id == from_container_id:
        to_container = from_container
    else:
        to_container = MoneyContainer.objects.select_for_update().get(pk=target_container_id)
    FSV.assert_money_container_access(user=actor, container=from_container)
    FSV.assert_money_container_access(user=actor, container=to_container)
    _assert_sufficient(container=from_container, currency_code=cur_from, amount=amt_from)

    src = f"EXCH:{uuid4().hex}"
    receipt = FSV._mk_receipt(
        actor=actor,
        kind=ReceiptKind.EXCHANGE,
        note=note or "Manual exchange",
        source_app="financials",
        source_model="ManualContainerEvent",
        source_id=src,
        fx_syp_per_usd=fx,
    )

    FSV._add_line_container(
        receipt=receipt,
        container=from_container,
        currency=Currency.objects.get(code=cur_from),
        amount=-amt_from,
    )
    FSV._add_line_container(
        receipt=receipt,
        container=to_container,
        currency=Currency.objects.get(code=cur_to),
        amount=amt_to,
    )

    FSV._post_receipt(receipt)
    FSV._apply_container_balance_delta(container=from_container, currency_code=cur_from, amount=-amt_from)
    FSV._apply_container_balance_delta(container=to_container, currency_code=cur_to, amount=amt_to)
    return receipt
