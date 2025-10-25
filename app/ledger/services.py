# app/ledger/services.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Iterable, Optional
from django.db import transaction
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.db.models import Sum
from .models import (
    Account, JournalEntry, JournalLine,
    CashSession, CashDrawerEvent
)
from .choices import DC
from decimal import Decimal, ROUND_HALF_UP


@dataclass(frozen=True)
class LineSpec:
    account_code: str
    dc: str   # "D" or "C"
    amount_minor: int
    extra: dict | None = None

def _get_account(code: str) -> Account:
    try:
        return Account.objects.get(code=code, is_active=True)
    except Account.DoesNotExist:
        raise ValidationError(f"Unknown or inactive account: {code}")

def _sum_side(lines: Iterable[LineSpec], dc: str) -> int:
    return sum(ls.amount_minor for ls in lines if ls.dc == dc)

@transaction.atomic
def post_journal(*, actor, session: Optional[CashSession], lines: list[LineSpec],
                 memo: str = "", source_app: str = "", source_model: str = "",
                 source_id: str = "", idempotency_key: Optional[str] = None) -> JournalEntry:
    if not lines or len(lines) < 2:
        raise ValidationError("A journal needs at least two lines.")
    for ls in lines:
        if ls.amount_minor <= 0:
            raise ValidationError("Amounts must be positive integers.")

    deb = _sum_side(lines, DC.DEBIT)
    cre = _sum_side(lines, DC.CREDIT)
    if deb != cre:
        raise ValidationError(f"Unbalanced entry (debits {deb} != credits {cre}).")

    # idempotency: return existing if key collides
    if idempotency_key:
        existing = JournalEntry.objects.filter(idempotency_key=idempotency_key).first()
        if existing:
            return existing

    entry = JournalEntry.objects.create(
        posted_at=timezone.now(), actor=actor, session=session,
        source_app=source_app, source_model=source_model, source_id=source_id,
        idempotency_key=idempotency_key, memo=memo
    )
    for ls in lines:
        JournalLine.objects.create(
            entry=entry, account=_get_account(ls.account_code),
            dc=ls.dc, amount_minor=ls.amount_minor, extra=ls.extra or {}
        )
    return entry


# === Provider Returns (new) ===============================================

def post_provider_return(
    *,
    actor,
    total_minor: int,
    paid_minor: int,
    provider_id: int,
    source=("billing", "ProviderReturn", ""),
    inventory_account_code="INVENTORY",
    receivable_account_code="PROVIDER_RECEIVABLE",
    cash_account_code="SAFE",
):
    """
    Provider return (store sends goods back to provider).
      Cr INVENTORY .................. total
      Dr SAFE (cash received now) ... paid
      Dr PROVIDER_RECEIVABLE ........ total - paid
    """
    if total_minor <= 0:
        raise ValidationError("total_minor must be > 0")

    debit_recv = max(total_minor - max(paid_minor, 0), 0)
    debit_cash = max(min(paid_minor, total_minor), 0)

    lines = [LineSpec(inventory_account_code, DC.CREDIT, total_minor)]
    if debit_cash:
        lines.append(LineSpec(cash_account_code, DC.DEBIT, debit_cash))
    if debit_recv:
        lines.append(LineSpec(receivable_account_code, DC.DEBIT, debit_recv))

    app, model, sid = source
    return post_journal(
        actor=actor, session=None, lines=lines,
        memo=f"Provider return #{provider_id}",
        source_app=app, source_model=model, source_id=str(sid),
        idempotency_key=f"{app}:{model}:{sid}:provider_return:v1"
    )


def post_provider_return_reversal(
    *,
    actor,
    total_minor: int,
    paid_minor: int,
    provider_id: int,
    source=("billing", "ProviderReturn", ""),
    inventory_account_code="INVENTORY",
    receivable_account_code="PROVIDER_RECEIVABLE",
    cash_account_code="SAFE",
):
    """
    Reverse provider return:
      Dr INVENTORY .................. total
      Cr SAFE (reverse cash) ........ paid
      Cr PROVIDER_RECEIVABLE ........ total - paid
    """
    if total_minor <= 0:
        return None

    credit_recv = max(total_minor - max(paid_minor, 0), 0)
    credit_cash = max(min(paid_minor, total_minor), 0)

    lines = [LineSpec(inventory_account_code, DC.DEBIT, total_minor)]
    if credit_cash:
        lines.append(LineSpec(cash_account_code, DC.CREDIT, credit_cash))
    if credit_recv:
        lines.append(LineSpec(receivable_account_code, DC.CREDIT, credit_recv))

    app, model, sid = source
    return post_journal(
        actor=actor, session=None, lines=lines,
        memo=f"Reversal of provider return #{provider_id}",
        source_app=app, source_model=model, source_id=str(sid),
        idempotency_key=f"{app}:{model}:{sid}:provider_return:reverse:v1"
    )


def collect_from_provider(
    *,
    actor,
    amount_minor: int,
    provider_id: int,
    cash_account_code="SAFE",
    receivable_account_code="PROVIDER_RECEIVABLE",
    source=("billing", "ProviderReturn", ""),
):
    """
    Provider pays outstanding receivable for prior returns.
      Dr SAFE ........................ amount
      Cr PROVIDER_RECEIVABLE ......... amount
    """
    if amount_minor <= 0:
        raise ValidationError("amount must be positive")

    lines = [
        LineSpec(cash_account_code, DC.DEBIT, amount_minor),
        LineSpec(receivable_account_code, DC.CREDIT, amount_minor),
    ]
    app, model, sid = source
    return post_journal(
        actor=actor, session=None, lines=lines,
        memo=f"Collection from provider #{provider_id}",
        source_app=app, source_model=model, source_id=str(sid),
        idempotency_key=f"{app}:{model}:{sid}:provider_collect:{amount_minor}:v1"
    )

# ---- Convenience wrappers ----

def post_opening_float(*, actor, session: CashSession):
    """Mirror the opening float into the ledger."""
    amt = session.opening_float_minor
    lines = [
        LineSpec(session.register.cash_account.code, DC.DEBIT, amt),
        LineSpec("OPENING_FLOAT_EQUITY", DC.CREDIT, amt),
    ]
    return post_journal(actor=actor, session=session, lines=lines,
                        memo=f"Opening float for {session.register.code}",
                        source_app="ledger", source_model="CashSession", source_id=str(session.id),
                        idempotency_key=f"session:{session.id}:open")

def post_cash_in(*, actor, session: CashSession, amount_minor: int, note: str = ""):
    lines = [
        LineSpec(session.register.cash_account.code, DC.DEBIT, amount_minor),
        LineSpec("PETTY_CASH_EXPENSE", DC.CREDIT, amount_minor),
    ]
    entry = post_journal(actor=actor, session=session, lines=lines,
                         memo=note or "Cash In", source_app="ledger", source_model="CashDrawerEvent",
                         idempotency_key=f"session:{session.id}:cashin:{amount_minor}:{note}")
    CashDrawerEvent.objects.create(entry=entry, kind="CASH_IN", register=session.register,
                                   amount_minor=amount_minor, note=note)
    return entry

def post_cash_out(*, actor, session: CashSession, amount_minor: int, note: str = ""):
    lines = [
        LineSpec("PETTY_CASH_EXPENSE", DC.DEBIT, amount_minor),
        LineSpec(session.register.cash_account.code, DC.CREDIT, amount_minor),
    ]
    entry = post_journal(actor=actor, session=session, lines=lines,
                         memo=note or "Cash Out", source_app="ledger", source_model="CashDrawerEvent",
                         idempotency_key=f"session:{session.id}:cashout:{amount_minor}:{note}")
    CashDrawerEvent.objects.create(entry=entry, kind="CASH_OUT", register=session.register,
                                   amount_minor=amount_minor, note=note)
    return entry

def to_minor(amount: Decimal, places: int = 3) -> int:
    if amount is None:
        return 0
    q = Decimal(10) ** -places
    return int((amount.quantize(q, rounding=ROUND_HALF_UP) * (10 ** places)).to_integral_value())

def post_safe_drop(*, actor, session: CashSession, amount_minor: int, safe_account_code="SAFE", note: str = ""):
    lines = [
        LineSpec(safe_account_code, DC.DEBIT, amount_minor),
        LineSpec(session.register.cash_account.code, DC.CREDIT, amount_minor),
    ]
    entry = post_journal(actor=actor, session=session, lines=lines,
                         memo=note or "Safe Drop", source_app="ledger", source_model="CashDrawerEvent",
                         idempotency_key=f"session:{session.id}:safedrop:{amount_minor}:{note}")
    CashDrawerEvent.objects.create(entry=entry, kind="SAFE_DROP", register=session.register,
                                   amount_minor=amount_minor, note=note)
    return entry

def post_bank_deposit(*, actor, amount_minor: int, note: str = "",
                      from_account_code="SAFE", bank_account_code="BANK_MAIN",
                      idempotency_key: Optional[str] = None):
    """
    Move money from SAFE (or another source) to BANK_MAIN.
    Provide idempotency_key if the caller has a real external reference; otherwise we
    fall back to a stable-ish key including actor id to reduce accidental collisions.
    """
    lines = [
        LineSpec(bank_account_code, DC.DEBIT, amount_minor),
        LineSpec(from_account_code, DC.CREDIT, amount_minor),
    ]
    key = idempotency_key or f"bankdeposit:{amount_minor}:{note}:{actor.id}"
    entry = post_journal(actor=actor, session=None, lines=lines,
                         memo=note or "Bank Deposit", source_app="ledger", source_model="CashDrawerEvent",
                         idempotency_key=key)
    CashDrawerEvent.objects.create(entry=entry, kind="BANK_DEPOSIT", register=None,
                                   amount_minor=amount_minor, note=note)
    return entry

def post_sale_cash(*, actor, session: CashSession, total_minor: int, net_sales_minor: int,
                   vat_minor: int, discount_minor: int = 0,
                   source=("billing","Bill","")):
    app, model, sid = source
    lines = [
        LineSpec(session.register.cash_account.code, DC.DEBIT, total_minor),
        LineSpec("SALES", DC.CREDIT, net_sales_minor),
    ]
    if vat_minor:
        lines.append(LineSpec("VAT_PAYABLE", DC.CREDIT, vat_minor))
    if discount_minor:
        lines.append(LineSpec("DISCOUNT_EXPENSE", DC.DEBIT, discount_minor))

    return post_journal(actor=actor, session=session, lines=lines,
                        memo="Cash Sale", source_app=app, source_model=model, source_id=str(sid),
                        idempotency_key=f"{app}:{model}:{sid}:sale:v1")

def post_sale_receivable(*, actor, total_minor: int, net_sales_minor: int,
                         vat_minor: int, discount_minor: int = 0,
                         source=("billing","Bill","")):
    lines = [
        LineSpec("CUSTOMER_RECEIVABLE", DC.DEBIT, total_minor),
        LineSpec("SALES", DC.CREDIT, net_sales_minor),
    ]
    if vat_minor:
        lines.append(LineSpec("VAT_PAYABLE", DC.CREDIT, vat_minor))
    if discount_minor:
        lines.append(LineSpec("DISCOUNT_EXPENSE", DC.DEBIT, discount_minor))

    app, model, sid = source
    return post_journal(actor=actor, session=None, lines=lines,
                        memo="Credit Sale", source_app=app, source_model=model, source_id=str(sid),
                        idempotency_key=f"{app}:{model}:{sid}:sale_credit:v1")

def post_provider_payment_cash(*, actor, session: CashSession, amount_minor: int, provider_id: int):
    lines = [
        LineSpec("PROVIDER_PAYABLE", DC.DEBIT, amount_minor),
        LineSpec(session.register.cash_account.code, DC.CREDIT, amount_minor),
    ]
    return post_journal(actor=actor, session=session, lines=lines,
                        memo=f"Provider payment #{provider_id}",
                        source_app="billing", source_model="ProviderPayment", source_id=str(provider_id),
                        idempotency_key=f"providerpay:{provider_id}:cash")

def post_over_short(*, actor, session: CashSession, diff_minor: int):
    """Positive diff = over (gain), negative = short (loss)."""
    if diff_minor == 0:
        return None
    cash = session.register.cash_account.code
    if diff_minor > 0:
        lines = [
            LineSpec(cash, DC.DEBIT, diff_minor),
            LineSpec("ROUNDING_GAIN_LOSS", DC.CREDIT, diff_minor),
        ]
    else:
        diff = -diff_minor
        lines = [
            LineSpec("ROUNDING_GAIN_LOSS", DC.DEBIT, diff),
            LineSpec(cash, DC.CREDIT, diff),
        ]
    return post_journal(actor=actor, session=session, lines=lines,
                        memo="Over/Short adjustment",
                        source_app="ledger", source_model="CashSession", source_id=str(session.id),
                        idempotency_key=f"session:{session.id}:over_short")

def post_purchase(*, actor, total_minor: int, paid_minor: int, provider_id: int,
                  source=("billing","Bill",""), inventory_account_code="INVENTORY",
                  payable_account_code="PROVIDER_PAYABLE", cash_account_code="SAFE"):
    """
    Purchase from provider.
      Dr INVENTORY .................. total
        Cr SAFE (or cash source) .... paid
        Cr PROVIDER_PAYABLE ......... total - paid
    """
    if total_minor <= 0:
        raise ValidationError("total_minor must be > 0")

    credit_payable = max(total_minor - max(paid_minor, 0), 0)
    credit_cash    = max(min(paid_minor, total_minor), 0)

    lines = [ LineSpec(inventory_account_code, DC.DEBIT, total_minor) ]
    if credit_cash:
        lines.append(LineSpec(cash_account_code, DC.CREDIT, credit_cash))
    if credit_payable:
        lines.append(LineSpec(payable_account_code, DC.CREDIT, credit_payable))

    app, model, sid = source
    return post_journal(
        actor=actor, session=None, lines=lines, memo=f"Provider purchase #{provider_id}",
        source_app=app, source_model=model, source_id=str(sid),
        idempotency_key=f"{app}:{model}:{sid}:purchase:v1"
    )

def post_purchase_reversal(*, actor, total_minor: int, paid_minor: int, provider_id: int,
                           source=("billing","Bill",""), inventory_account_code="INVENTORY",
                           payable_account_code="PROVIDER_PAYABLE", cash_account_code="SAFE"):
    """
    Reverse the above:
      Cr INVENTORY .................. total
      Dr SAFE ....................... paid
      Dr PROVIDER_PAYABLE ........... total - paid
    """
    if total_minor <= 0:
        return None
    debit_payable = max(total_minor - max(paid_minor, 0), 0)
    debit_cash    = max(min(paid_minor, total_minor), 0)

    lines = [ LineSpec(inventory_account_code, DC.CREDIT, total_minor) ]
    if debit_cash:
        lines.append(LineSpec(cash_account_code, DC.DEBIT, debit_cash))
    if debit_payable:
        lines.append(LineSpec(payable_account_code, DC.DEBIT, debit_payable))

    app, model, sid = source
    return post_journal(
        actor=actor, session=None, lines=lines, memo=f"Reversal of provider purchase #{provider_id}",
        source_app=app, source_model=model, source_id=str(sid),
        idempotency_key=f"{app}:{model}:{sid}:purchase:reverse:v1"
    )

def post_provider_payment_from_safe(*, actor, amount_minor: int, provider_id: int,
                                    cash_account_code="SAFE", payable_account_code="PROVIDER_PAYABLE",
                                    source=("billing","Bill","")):
    """
    Pay a provider from the SAFE (manager vault):
      Dr PROVIDER_PAYABLE ........... amount
        Cr SAFE ..................... amount
    """
    if amount_minor <= 0:
        raise ValidationError("amount must be positive")

    lines = [
        LineSpec(payable_account_code, DC.DEBIT, amount_minor),
        LineSpec(cash_account_code, DC.CREDIT, amount_minor),
    ]
    app, model, sid = source
    return post_journal(
        actor=actor, session=None, lines=lines, memo=f"Provider payment from SAFE #{provider_id}",
        source_app=app, source_model=model, source_id=str(sid),
        idempotency_key=f"{app}:{model}:{sid}:providerpay:safe:{amount_minor}"
    )



def expected_cash_for_session(session: CashSession) -> int:
    """
    Compute expected cash for a session from the ledger:
    opening + debits - credits on the register's cash account, within this session.
    """
    cash_acc = session.register.cash_account
    qs = cash_acc.journal_lines.filter(entry__session=session)
    d = qs.filter(dc=DC.DEBIT).aggregate(Sum("amount_minor"))["amount_minor__sum"] or 0
    c = qs.filter(dc=DC.CREDIT).aggregate(Sum("amount_minor"))["amount_minor__sum"] or 0
    return d - c

@transaction.atomic
def close_session(*, actor, session: CashSession, counted_minor: int):
    """
    Settle a session: compute expected, post over/short if needed, and mark closed.
    """
    if session.is_closed:
        return session
    expected = expected_cash_for_session(session)
    diff = counted_minor - expected
    # Post over/short (no-op if diff==0)
    post_over_short(actor=actor, session=session, diff_minor=diff)
    # Persist close fields
    session.expected_minor = expected
    session.counted_minor = counted_minor
    session.over_short_minor = diff
    session.closed_at = timezone.now()
    session.closed_by = actor
    session.is_closed = True
    session.save(update_fields=[
        "expected_minor", "counted_minor", "over_short_minor",
        "closed_at", "closed_by", "is_closed"
    ])
    return session