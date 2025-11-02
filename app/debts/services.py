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
)

from debts.models import PartyType
from billing.models import Provider

from ledger import services as LSV

# ====== Decimals / helpers ======
DEC0 = Decimal("0")
DEC3 = Decimal("0.001")
DEC4 = Decimal("0.0001")

def q3(x: Decimal) -> Decimal:
    return (x or DEC0).quantize(DEC3)

def q4(x: Decimal) -> Decimal:
    return (x or DEC0).quantize(DEC4)

def minor3(x: Decimal) -> int:
    return LSV.to_minor(q3(x or DEC0), 3)


# =======================================================================
# CREATE DEBTS
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
    """
    Create or update a DebtorDebt record.
    """
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
    """
    Create or update a CreditorDebt record.
    """
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
    Create a manual debt without commercial document.
    """
    from billing.services import _next_bill_serial_locked, _next_return_serial_locked

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
        serial = _next_bill_serial_locked()
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

        with LSV.suppress_exceptions():
            LSV.post_manual_debtor_created(
                actor=actor,
                amount_minor=minor3(amt),
                provider_id=provider.id if provider else None,
                source=("debts", "ManualDebt", f"D-{entry.id}"),
            )
        return entry

    else:
        serial = _next_return_serial_locked()
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

        with LSV.suppress_exceptions():
            LSV.post_manual_creditor_created(
                actor=actor,
                amount_minor=minor3(amt),
                provider_id=provider.id if provider else None,
                source=("debts", "ManualDebt", f"C-{entry.id}"),
            )
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
        LSV.post_provider_payment_from_safe(
            actor=actor,
            amount_minor=minor3(amt),
            provider_id=entry.provider_id,
            source=("debts", "DebtorDebt", entry.id),
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
        LSV.collect_from_provider(
            actor=actor,
            amount_minor=minor3(amt),
            provider_id=entry.provider_id,
            source=("debts", "CreditorDebt", entry.id),
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
    """
    Create or update a reminder snapshot for a given debt entry.
    Uses (direction, debtor) or (direction, creditor) as the identity.
    """
    dirn = (direction or "").lower().strip()
    if dirn not in {"debtor", "creditor"}:
        raise ValueError("direction must be 'debtor' or 'creditor'")

    if dirn == "debtor":
        obj = get_object_or_404(DebtorDebt.objects.select_for_update(), pk=debt_id)
        rem, _ = DebtReminder.objects.update_or_create(
            direction="debtor",
            debtor=obj,
            defaults=dict(
                creditor=None,
                due_date=reminder_date,
                set_at=timezone.now(),
            ),
        )
        return rem
    else:
        obj = get_object_or_404(CreditorDebt.objects.select_for_update(), pk=debt_id)
        rem, _ = DebtReminder.objects.update_or_create(
            direction="creditor",
            creditor=obj,
            defaults=dict(
                debtor=None,
                due_date=reminder_date,
                set_at=timezone.now(),
            ),
        )
        return rem
    
def get_reminders_due(
    *,
    as_of: Optional[date] = None,
    include_closed: bool = False,
):
    """
    Return reminders due on/before as_of.
    """
    as_of = as_of or timezone.now().date()
    qs = DebtReminder.objects.filter(due_date__lte=as_of)

    if not include_closed:
        qs = qs.exclude(
            direction="debtor",
            debtor__status=DebtorDebt.Status.CLOSED,
        ).exclude(
            direction="creditor",
            creditor__status=CreditorDebt.Status.CLOSED,
        )

    # we don't need heavy select_related; keep it light
    return qs

