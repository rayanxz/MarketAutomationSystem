# app/debts/services.py
from __future__ import annotations
from decimal import Decimal
from datetime import date
from typing import Optional
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone

from debts.models import (
    DebtorDebt,
    DebtorPayment,
    CreditorDebt,
    CreditorReceipt,
    DebtReminder,
    PartyType,
)
from billing.models import Provider
from ledger import services as LSV

from inventory.models import DEC0 , q3 , q4

from django.db.models import Q

def minor(x: Decimal) -> int:
    return LSV.to_minor(q3(x or DEC0))

# =======================================================================
# CREATE / UPSERT ENTRIES MIRRORED FROM COMMERCIAL DOCS
# =======================================================================

@transaction.atomic
def create_debtor_entry(
    *,
    provider: Provider,
    total: Decimal,
    paid_amount: Decimal,
    source_app: str,
    source_model: str,
    source_id: str,
    doc_serial: Optional[int] = None,
    party_type: str = PartyType.PROVIDER,
    party_name: Optional[str] = None,
    due_date: Optional[date] = None,
) -> DebtorDebt:
    """Upsert a DebtorDebt snapshot driven by a commercial document."""
    total = q3(total)
    paid = q3(paid_amount or DEC0)
    remaining = total - paid
    status = DebtorDebt.Status.CLOSED if remaining <= DEC0 else DebtorDebt.Status.OPEN

    entry, _ = DebtorDebt.objects.update_or_create(
        provider=provider,
        source_app=source_app,
        source_model=source_model,
        source_id=str(source_id),
        defaults=dict(
            total=total,
            paid_amount=paid,
            status=status,
            party_type=party_type,
            party_name=(party_name or provider.name),
            doc_serial=doc_serial,
            due_date=due_date,
        ),
    )
    return entry


@transaction.atomic
def create_creditor_entry(
    *,
    provider: Provider,
    total: Decimal,
    collected: Decimal,
    source_app: str,
    source_model: str,
    source_id: str,
    doc_serial: Optional[int] = None,
    party_type: str = PartyType.PROVIDER,
    party_name: Optional[str] = None,
    due_date: Optional[date] = None,
) -> CreditorDebt:
    """Upsert a CreditorDebt snapshot driven by a commercial document."""
    total = q3(total)
    collected = q3(collected or DEC0)
    remaining = total - collected
    status = CreditorDebt.Status.CLOSED if remaining <= DEC0 else CreditorDebt.Status.OPEN

    entry, _ = CreditorDebt.objects.update_or_create(
        provider=provider,
        source_app=source_app,
        source_model=source_model,
        source_id=str(source_id),
        defaults=dict(
            total=total,
            collected=collected,
            status=status,
            party_type=party_type,
            party_name=(party_name or provider.name),
            doc_serial=doc_serial,
            due_date=due_date,
        ),
    )
    return entry

# =======================================================================
# MANUAL DEBTS (no commercial doc)
# =======================================================================

def _next_bill_serial_locked_local() -> int:
    from django.db.models import Max
    from billing.models import Bill
    from debts.models import DebtorDebt

    m_bill = Bill.objects.select_for_update().aggregate(m=Max("serial"))["m"] or 0
    m_debt = DebtorDebt.objects.select_for_update().aggregate(m=Max("doc_serial"))["m"] or 0
    return int(max(int(m_bill or 0), int(m_debt or 0))) + 1


def _next_return_serial_locked_local() -> int:
    from django.db.models import Max
    from billing.models import ProviderReturn
    from debts.models import CreditorDebt

    m_ret = ProviderReturn.objects.select_for_update().aggregate(m=Max("serial"))["m"] or 0
    m_debt = CreditorDebt.objects.select_for_update().aggregate(m=Max("doc_serial"))["m"] or 0
    return int(max(int(m_ret or 0), int(m_debt or 0))) + 1



@transaction.atomic
def create_manual_debt(
    *,
    actor,
    direction: str,         # "debtor" | "creditor"
    party_type: str,
    provider_id: Optional[int],
    party_name: str,
    amount: Decimal,
    due_date: Optional[date] = None,
) -> DebtorDebt | CreditorDebt:
    """
    Create a manual debt without a commercial document.
    NOTE: For manual debts we DO touch SAFE immediately:
      - direction='debtor'   → SAFE IN   (volt up)   because we took cash and now we owe
      - direction='creditor' → SAFE OUT  (volt down) because we gave cash and they now owe
    Settlement later uses pay/collect as before.
    """

    amt = q3(amount or DEC0)
    if amt <= 0:
        raise ValueError("amount must be positive")

    ptype = (party_type or PartyType.PROVIDER).lower().strip()
    provider = None
    if ptype == PartyType.PROVIDER:
        if not provider_id:
            raise ValueError("provider must be selected")
        provider = get_object_or_404(Provider.objects.select_for_update(), pk=int(provider_id))

    dirn = (direction or "").lower().strip()
    if dirn not in {"debtor", "creditor"}:
        raise ValueError("direction must be 'debtor' or 'creditor'")

    if dirn == "debtor":
        serial = _next_bill_serial_locked_local()
        entry = DebtorDebt.objects.create(
            provider=provider,
            source_app="debts",
            source_model="ManualDebt",
            source_id=f"manual:{serial}",
            total=amt,
            paid_amount=DEC0,
            status=DebtorDebt.Status.OPEN,
            party_type=ptype,
            party_name=party_name or (provider.name if provider else ""),
            doc_serial=serial,
            due_date=due_date,
        )

        # SAFE goes UP at creation (we took cash, now we owe)
        with LSV.suppress_exceptions():
            LSV.post_safe_in(
                actor=actor,
                amount_minor=minor(amt),
                description=f"Manual debt created: we owe {entry.party_name} (#{entry.doc_serial})",
                source=("debts", "DebtorDebt", str(entry.id)),
            )
            # If you also model the payable increase in ledger, call your payable-increase helper here.

        return entry


    else:
        serial = _next_return_serial_locked_local()
        entry = CreditorDebt.objects.create(
            provider=provider,
            source_app="debts",
            source_model="ManualDebt",
            source_id=f"manual:{serial}",
            total=amt,
            collected=DEC0,
            status=CreditorDebt.Status.OPEN,
            party_type=ptype,
            party_name=party_name or (provider.name if provider else ""),
            doc_serial=serial,
            due_date=due_date,
        )

        # SAFE goes DOWN at creation (we gave cash, they owe us)
        with LSV.suppress_exceptions():
            LSV.post_safe_out(
                actor=actor,
                amount_minor=minor(amt),
                description=f"Manual debt created: {entry.party_name} owes us (#{entry.doc_serial})",
                source=("debts", "CreditorDebt", str(entry.id)),
            )
            # If you also model the receivable increase, call that helper here.

        return entry


# =======================================================================
# PAY / COLLECT
# =======================================================================

@transaction.atomic
def pay_debt(
    *,
    actor,
    entry_id: int,
    amount: Optional[Decimal] = None,
    full: bool = False,
) -> DebtorDebt:
    """
    Pay a debtor debt (we owe provider) → SAFE goes DOWN.
    Posts:
      - Dr PROVIDER_PAYABLE / Cr SAFE  (reduce payable, cash out)
    """
    entry = DebtorDebt.objects.select_for_update().get(pk=entry_id)
    rem = q3(entry.remaining)
    if rem <= 0:
        return entry

    amt = q3(rem if full else (amount or DEC0))
    if amt <= 0:
        raise ValueError("amount must be positive")
    if amt > rem:
        raise ValueError("amount exceeds remaining")

    DebtorPayment.objects.create(entry=entry, amount=amt)
    entry.paid_amount = q3((entry.paid_amount or DEC0) + amt)
    entry.status = DebtorDebt.Status.CLOSED if entry.remaining <= DEC0 else DebtorDebt.Status.OPEN
    entry.save(update_fields=["paid_amount", "status"])

    with LSV.suppress_exceptions():

        # SAFE goes DOWN
        LSV.post_safe_out(
            actor=actor,
            amount_minor=minor(amt),
            description=f"Pay debt to {entry.party_name} (#{entry.doc_serial or entry.id})",
            source=("debts", "DebtorDebt", str(entry.id)),
        )

    return entry


@transaction.atomic
def collect_debt(
    *,
    actor,
    entry_id: int,
    amount: Optional[Decimal] = None,
    full: bool = False,
) -> CreditorDebt:
    """
    Collect a creditor debt (provider owes us) → SAFE goes UP.
    Posts:
      - Dr SAFE / Cr PROVIDER_RECEIVABLE  (reduce receivable, cash in)
    """
    entry = CreditorDebt.objects.select_for_update().get(pk=entry_id)
    rem = q3(entry.remaining)
    if rem <= 0:
        return entry

    amt = q3(rem if full else (amount or DEC0))
    if amt <= 0:
        raise ValueError("amount must be positive")
    if amt > rem:
        raise ValueError("amount exceeds remaining")

    CreditorReceipt.objects.create(entry=entry, amount=amt)
    entry.collected = q3((entry.collected or DEC0) + amt)
    entry.status = CreditorDebt.Status.CLOSED if entry.remaining <= DEC0 else CreditorDebt.Status.OPEN
    entry.save(update_fields=["collected", "status"])

    with LSV.suppress_exceptions():
       
        # SAFE goes UP
        LSV.post_safe_in(
            actor=actor,
            amount_minor=minor(amt),
            description=f"Collect debt from {entry.party_name} (#{entry.doc_serial or entry.id})",
            source=("debts", "CreditorDebt", str(entry.id)),
        )

    return entry

# =======================================================================
# REMINDERS
# =======================================================================

@transaction.atomic
def set_reminder(
    *,
    debt_id: int,
    direction: str,            # "debtor" | "creditor"
    reminder_date: date,
) -> DebtReminder:
    dirn = (direction or "").lower().strip()
    if dirn not in {"debtor", "creditor"}:
        raise ValueError("direction must be 'debtor' or 'creditor'")

    now = timezone.now()

    if dirn == "debtor":
        obj = get_object_or_404(DebtorDebt.objects.select_for_update(), pk=debt_id)

        # keep only ONE reminder per debt (replace old one)
        DebtReminder.objects.filter(direction="debtor", debtor=obj).delete()

        return DebtReminder.objects.create(
            direction="debtor",
            debtor=obj,
            creditor=None,
            due_date=reminder_date,
            set_at=now,
        )

    obj = get_object_or_404(CreditorDebt.objects.select_for_update(), pk=debt_id)

    DebtReminder.objects.filter(direction="creditor", creditor=obj).delete()

    return DebtReminder.objects.create(
        direction="creditor",
        creditor=obj,
        debtor=None,
        due_date=reminder_date,
        set_at=now,
    )



def get_reminders_due(
    *,
    as_of: Optional[date] = None,
    include_closed: bool = False,
):
    as_of = as_of or timezone.now().date()
    qs = DebtReminder.objects.filter(due_date__lte=as_of)

    if not include_closed:
        qs = qs.filter(
            Q(direction="debtor", debtor__status=DebtorDebt.Status.OPEN) |
            Q(direction="creditor", creditor__status=CreditorDebt.Status.OPEN)
        )

    return qs

