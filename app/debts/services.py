# app/debts/services.py
from __future__ import annotations
from decimal import Decimal
from datetime import date
from typing import Optional
from django.db import transaction, connection
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
from audit_log.services import log_update
from billing.models import Provider
from financials import services as FinSV
from financials.models import (
    Counterparty,
    CounterpartyType,
    MoneyContainer,
    MoneyContainerCurrency,
    Receipt,
)

from inventory.models import DEC0 , q3 , q4

from django.db.models import Q

def _assert_container_access(*, actor, container: MoneyContainer) -> None:
    if not container.is_active:
        raise ValueError("Container is inactive / disabled")
    if not (getattr(actor, "is_superuser", False) or getattr(actor, "is_staff", False)):
        if container.allowed_users.exists() and not container.allowed_users.filter(pk=actor.pk).exists():
            raise ValueError("Container access denied for this user")


def _ensure_provider_counterparty(*, provider: Provider) -> Counterparty:
    cp = Counterparty.objects.filter(type=CounterpartyType.PROVIDER, provider_id=provider.id).first()
    if cp:
        if (cp.name or "").strip() != (provider.name or "").strip():
            cp.name = (provider.name or "").strip()
            cp.save(update_fields=["name"])
        return cp
    return Counterparty.objects.create(
        type=CounterpartyType.PROVIDER,
        name=(provider.name or "").strip(),
        provider_id=provider.id,
        is_active=True,
    )


def _ensure_customer_counterparty(*, customer) -> Counterparty:
    cp = Counterparty.objects.filter(type=CounterpartyType.CUSTOMER, customer_id=customer.id).first()
    if cp:
        if (cp.name or "").strip() != (customer.name or "").strip():
            cp.name = (customer.name or "").strip()
            cp.save(update_fields=["name"])
        return cp
    return Counterparty.objects.create(
        type=CounterpartyType.CUSTOMER,
        name=(customer.name or "").strip(),
        customer_id=customer.id,
        is_active=True,
    )

# =======================================================================
# CREATE / UPSERT ENTRIES MIRRORED FROM COMMERCIAL DOCS
# =======================================================================

@transaction.atomic
def create_debtor_entry(
    *,
    provider: Optional[Provider],
    total: Decimal,
    paid_amount: Decimal,
    source_app: str,
    source_model: str,
    source_id: str,
    currency_code: str = "SYP",
    doc_serial: Optional[int] = None,
    party_type: str = PartyType.PROVIDER,
    party_name: Optional[str] = None,
    customer_id: Optional[int] = None,
    due_date: Optional[date] = None,
) -> DebtorDebt:
    """Upsert a DebtorDebt snapshot driven by a commercial document."""
    ptype = (party_type or PartyType.PROVIDER).lower().strip()
    if provider or ptype == PartyType.PROVIDER:
        if not provider:
            raise ValueError("provider is required for provider debts")
        if customer_id:
            raise ValueError("customer must be null for provider debts")
    else:
        if not customer_id and not (party_name or "").strip():
            raise ValueError("customer or party_name is required for customer debts")
        if provider:
            raise ValueError("provider must be null for customer debts")

    total = q3(total)
    paid = q3(paid_amount or DEC0)
    remaining = total - paid
    status = DebtorDebt.Status.CLOSED if remaining <= DEC0 else DebtorDebt.Status.OPEN

    party_label = party_name or (provider.name if provider else "")
    cur = (currency_code or "SYP").upper()
    src_id = str(source_id)
    defaults = dict(
        total=total,
        paid_amount=paid,
        status=status,
        party_type=ptype,
        party_name=party_label,
        doc_serial=doc_serial,
        customer_id=customer_id,
        due_date=due_date,
    )
    if connection.vendor == "sqlite" and cur == "USD":
        # SQLite keeps legacy unique constraint on source_id; avoid conflict.
        if DebtorDebt.objects.filter(source_app=source_app, source_model=source_model, source_id=src_id).exists():
            defaults["legacy_source_id"] = src_id
            src_id = f"{src_id}:USD"

    entry, _ = DebtorDebt.objects.update_or_create(
        provider=provider,
        source_app=source_app,
        source_model=source_model,
        source_id=str(src_id),
        currency_code=cur,
        defaults=defaults,
    )
    return entry


@transaction.atomic
def create_creditor_entry(
    *,
    provider: Optional[Provider],
    total: Decimal,
    collected: Decimal,
    source_app: str,
    source_model: str,
    source_id: str,
    currency_code: str = "SYP",
    doc_serial: Optional[int] = None,
    party_type: str = PartyType.PROVIDER,
    party_name: Optional[str] = None,
    customer_id: Optional[int] = None,
    due_date: Optional[date] = None,
) -> CreditorDebt:
    """Upsert a CreditorDebt snapshot driven by a commercial document."""
    total = q3(total)
    collected = q3(collected or DEC0)
    remaining = total - collected
    status = CreditorDebt.Status.CLOSED if remaining <= DEC0 else CreditorDebt.Status.OPEN

    party_label = party_name or (provider.name if provider else "")
    cur = (currency_code or "SYP").upper()
    src_id = str(source_id)
    defaults = dict(
        total=total,
        collected=collected,
        status=status,
        party_type=party_type,
        party_name=party_label,
        doc_serial=doc_serial,
        customer_id=customer_id,
        due_date=due_date,
    )
    if connection.vendor == "sqlite" and cur == "USD":
        if CreditorDebt.objects.filter(source_app=source_app, source_model=source_model, source_id=src_id).exists():
            defaults["legacy_source_id"] = src_id
            src_id = f"{src_id}:USD"

    entry, _ = CreditorDebt.objects.update_or_create(
        provider=provider,
        source_app=source_app,
        source_model=source_model,
        source_id=str(src_id),
        currency_code=cur,
        defaults=defaults,
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
    currency_code: str = "SYP",
    initial_payment: Optional[Decimal] = None,
    money_container_id: Optional[int] = None,
    due_date: Optional[date] = None,
) -> DebtorDebt | CreditorDebt:
    """
    Create a manual debt without a commercial document.
    NOTE: No money movement unless initial_payment is provided.
    """

    amt = q3(amount or DEC0)
    if amt <= 0:
        raise ValueError("amount must be positive")

    currency_code = (currency_code or "SYP").upper()
    if currency_code not in {"SYP", "USD"}:
        raise ValueError("invalid currency")

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
            currency_code=currency_code,
            due_date=due_date,
        )
        if provider:
            cp = _ensure_provider_counterparty(provider=provider)
            fx = FinSV.get_current_fx_syp_per_usd()
            FinSV.post_counterparty_adjust_with_fx(
                actor=actor,
                counterparty_id=cp.id,
                currency_code=currency_code,
                amount_signed=-q3(amt),
                fx_syp_per_usd=fx,
                note=f"Manual debtor debt #{entry.id}",
                source_app="debts",
                source_model="ManualDebt",
                source_id=str(entry.source_id),
            )
        if initial_payment:
            if not money_container_id:
                raise ValueError("money_container_id required for initial payment")
            pay_debt(
                actor=actor,
                entry_id=entry.id,
                amount=initial_payment,
                full=False,
                money_container_id=money_container_id,
                currency_code=currency_code,
            )
        return entry

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
        currency_code=currency_code,
        due_date=due_date,
    )
    if provider:
        cp = _ensure_provider_counterparty(provider=provider)
        fx = FinSV.get_current_fx_syp_per_usd()
        FinSV.post_counterparty_adjust_with_fx(
            actor=actor,
            counterparty_id=cp.id,
            currency_code=currency_code,
            amount_signed=+q3(amt),
            fx_syp_per_usd=fx,
            note=f"Manual creditor debt #{entry.id}",
            source_app="debts",
            source_model="ManualDebt",
            source_id=str(entry.source_id),
        )
    if initial_payment:
        if not money_container_id:
            raise ValueError("money_container_id required for initial collection")
        collect_debt(
            actor=actor,
            entry_id=entry.id,
            amount=initial_payment,
            full=False,
            money_container_id=money_container_id,
            currency_code=currency_code,
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
    money_container_id: Optional[int] = None,
    currency_code: Optional[str] = None,
) -> DebtorDebt:
    """
    Pay a debtor debt (we owe provider) -> cash OUT.
    Posts a Financials settlement receipt (container line negative, counterparty line positive).
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

    if not money_container_id:
        raise ValueError("money_container_id is required")

    cur = (currency_code or entry.currency_code or "SYP").upper()
    if cur not in {"SYP", "USD"}:
        raise ValueError("invalid currency")
    if (entry.currency_code or "SYP").upper() != cur:
        raise ValueError("currency mismatch for this debt")

    container = MoneyContainer.objects.select_for_update().get(pk=money_container_id)
    _assert_container_access(actor=actor, container=container)

    if not MoneyContainerCurrency.objects.filter(container_id=container.id, currency__code=cur, is_enabled=True).exists():
        raise ValueError(f"Currency {cur} is disabled for this container")

    # Resolve counterparty (provider or customer)
    cp = None
    if (entry.party_type or "provider").lower() == PartyType.CUSTOMER:
        if not entry.customer_id:
            raise ValueError("customer is required for customer debts")
        cp = _ensure_customer_counterparty(customer=entry.customer)
    else:
        if not entry.provider_id:
            raise ValueError("provider is required for provider debts")
        cp = _ensure_provider_counterparty(provider=entry.provider)

    fx = FinSV.get_current_fx_syp_per_usd()
    receipt = FinSV.post_settlement_with_fx(
        actor=actor,
        container_id=container.id,
        counterparty_id=cp.id,
        currency_code=cur,
        cash_amount_signed=-q3(amt),
        fx_syp_per_usd=fx,
        note=f"Debt payment #{entry.id}",
        source_app="debts",
        source_model="DebtorDebt",
        source_id=str(entry.id),
    )

    DebtorPayment.objects.create(
        entry=entry,
        amount=amt,
        currency_code=cur,
        receipt=receipt,
        money_container=container,
        fx_syp_per_usd_used=fx,
    )

    entry.paid_amount = q3((entry.paid_amount or DEC0) + amt)
    entry.status = DebtorDebt.Status.CLOSED if entry.remaining <= DEC0 else DebtorDebt.Status.OPEN
    entry.save(update_fields=["paid_amount", "status"])

    log_update(
        actor=actor,
        target=entry,
        title="Pay debt",
        message=f"Debt payment entry_id={entry.id} amount={amt} {cur}",
        meta={
            "entry_id": entry.id,
            "amount": str(amt),
            "currency": cur,
            "receipt_id": receipt.id,
            "container_id": container.id,
        },
    )

    return entry

def collect_debt(
    *,
    actor,
    entry_id: int,
    amount: Optional[Decimal] = None,
    full: bool = False,
    money_container_id: Optional[int] = None,
    currency_code: Optional[str] = None,
) -> CreditorDebt:
    """
    Collect a creditor debt (provider owes us) -> cash IN.
    Posts a Financials settlement receipt (container line positive, counterparty line negative).
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

    if not money_container_id:
        raise ValueError("money_container_id is required")

    cur = (currency_code or entry.currency_code or "SYP").upper()
    if cur not in {"SYP", "USD"}:
        raise ValueError("invalid currency")
    if (entry.currency_code or "SYP").upper() != cur:
        raise ValueError("currency mismatch for this debt")

    container = MoneyContainer.objects.select_for_update().get(pk=money_container_id)
    _assert_container_access(actor=actor, container=container)

    if not MoneyContainerCurrency.objects.filter(container_id=container.id, currency__code=cur, is_enabled=True).exists():
        raise ValueError(f"Currency {cur} is disabled for this container")

    # Resolve counterparty (provider or customer)
    cp = None
    if (entry.party_type or "provider").lower() == PartyType.CUSTOMER:
        if not entry.customer_id:
            raise ValueError("customer is required for customer debts")
        cp = _ensure_customer_counterparty(customer=entry.customer)
    else:
        if not entry.provider_id:
            raise ValueError("provider is required for provider debts")
        cp = _ensure_provider_counterparty(provider=entry.provider)

    fx = FinSV.get_current_fx_syp_per_usd()
    receipt = FinSV.post_settlement_with_fx(
        actor=actor,
        container_id=container.id,
        counterparty_id=cp.id,
        currency_code=cur,
        cash_amount_signed=+q3(amt),
        fx_syp_per_usd=fx,
        note=f"Debt collection #{entry.id}",
        source_app="debts",
        source_model="CreditorDebt",
        source_id=str(entry.id),
    )

    CreditorReceipt.objects.create(
        entry=entry,
        amount=amt,
        currency_code=cur,
        receipt=receipt,
        money_container=container,
        fx_syp_per_usd_used=fx,
    )

    entry.collected = q3((entry.collected or DEC0) + amt)
    entry.status = CreditorDebt.Status.CLOSED if entry.remaining <= DEC0 else CreditorDebt.Status.OPEN
    entry.save(update_fields=["collected", "status"])

    log_update(
        actor=actor,
        target=entry,
        title="Collect debt",
        message=f"Debt collection entry_id={entry.id} amount={amt} {cur}",
        meta={
            "entry_id": entry.id,
            "amount": str(amt),
            "currency": cur,
            "receipt_id": receipt.id,
            "container_id": container.id,
        },
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

