# app/debts/services.py
from __future__ import annotations
from decimal import Decimal
from datetime import date
from typing import Optional
import logging
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
from audit_log.services import log_update_safe as log_update
from billing.models import Provider
from financials import services as FinSV
from financials.models import (
    Counterparty,
    CounterpartyType,
    MoneyContainer,
    MoneyContainerCurrency,
    Receipt,
)
from accounts.models import AccountProfile
from accounts.utils import has_role

from inventory.models import DEC0

from django.db.models import Q
from debts.source_identity import (
    canonical_source_identity as _canonical_identity,
    source_identity_variants as _identity_variants,
)

logger = logging.getLogger(__name__)


def _q_money(*, amount: Decimal, currency_code: str) -> Decimal:
    return FinSV.q_money(amount=Decimal(amount or DEC0), currency_code=(currency_code or "SYP").upper())


def _canonical_source_identity(*, source_id: str, currency_code: str) -> tuple[str, str, str]:
    return _canonical_identity(source_id=source_id, currency_code=currency_code)


def _source_identity_variants(*, source_id: str) -> tuple[str, str]:
    return _identity_variants(source_id=source_id)


def _entry_currency_bucket(entry, *, currency_code: Optional[str]) -> int:
    if not currency_code:
        return 0
    desired = (currency_code or "").upper()
    cur = ((getattr(entry, "currency_code", None) or "") or "").upper()
    if cur == desired:
        return 0
    if not cur:
        return 1
    return 2


def _entry_source_bucket(entry, *, canonical_source_id: str, legacy_usd_source_id: str) -> int:
    sid = str(getattr(entry, "source_id", "") or "").strip()
    legacy = str(getattr(entry, "legacy_source_id", "") or "").strip()
    if sid == canonical_source_id:
        return 0
    if legacy == canonical_source_id:
        return 1
    if sid == legacy_usd_source_id:
        return 2
    if legacy == legacy_usd_source_id:
        return 3
    return 4


def _warn_source_collision(
    *,
    entries: list,
    source_app: str,
    source_model: str,
    canonical_source_id: str,
    legacy_usd_source_id: str,
    currency_code: Optional[str],
) -> None:
    desired = (currency_code or "").upper()
    canonical_rows = []
    legacy_rows = []
    for e in entries:
        sid = str(getattr(e, "source_id", "") or "").strip()
        cur = ((getattr(e, "currency_code", None) or "") or "").upper()
        if desired and cur not in {desired, ""}:
            continue
        if sid == canonical_source_id:
            canonical_rows.append(e)
        elif sid == legacy_usd_source_id:
            legacy_rows.append(e)
    if canonical_rows and legacy_rows:
        logger.warning(
            "Detected canonical/legacy source-id collision for %s.%s source_id=%s currency=%s "
            "(canonical_ids=%s legacy_ids=%s). Canonical rows are preferred deterministically.",
            source_app,
            source_model,
            canonical_source_id,
            desired or "*",
            [e.id for e in canonical_rows],
            [e.id for e in legacy_rows],
        )


def _sorted_entries_for_source(
    *,
    Model,
    source_app: str,
    source_model: str,
    source_id: str,
    currency_code: Optional[str] = None,
    for_update: bool = False,
) -> list:
    canonical_source_id, legacy_usd_source_id = _source_identity_variants(source_id=source_id)
    qs = Model.objects.filter(
        source_app=source_app,
        source_model=source_model,
    ).filter(
        Q(source_id=canonical_source_id)
        | Q(legacy_source_id=canonical_source_id)
        | Q(source_id=legacy_usd_source_id)
        | Q(legacy_source_id=legacy_usd_source_id)
    )
    if currency_code:
        cur = (currency_code or "").upper()
        qs = qs.filter(Q(currency_code=cur) | Q(currency_code__isnull=True) | Q(currency_code=""))
    if for_update:
        qs = qs.select_for_update()

    rows = list(qs.order_by("id"))
    if not rows:
        return []

    _warn_source_collision(
        entries=rows,
        source_app=source_app,
        source_model=source_model,
        canonical_source_id=canonical_source_id,
        legacy_usd_source_id=legacy_usd_source_id,
        currency_code=currency_code,
    )

    rows.sort(
        key=lambda e: (
            _entry_currency_bucket(e, currency_code=currency_code),
            _entry_source_bucket(
                e,
                canonical_source_id=canonical_source_id,
                legacy_usd_source_id=legacy_usd_source_id,
            ),
            int(getattr(e, "id", 0) or 0),
        )
    )
    return rows


def list_debtor_entries_for_source(
    *,
    source_app: str,
    source_model: str,
    source_id: str,
    currency_code: Optional[str] = None,
    for_update: bool = False,
) -> list[DebtorDebt]:
    return _sorted_entries_for_source(
        Model=DebtorDebt,
        source_app=source_app,
        source_model=source_model,
        source_id=source_id,
        currency_code=currency_code,
        for_update=for_update,
    )


def resolve_debtor_entry_for_source(
    *,
    source_app: str,
    source_model: str,
    source_id: str,
    currency_code: Optional[str] = None,
    for_update: bool = False,
) -> Optional[DebtorDebt]:
    rows = list_debtor_entries_for_source(
        source_app=source_app,
        source_model=source_model,
        source_id=source_id,
        currency_code=currency_code,
        for_update=for_update,
    )
    return rows[0] if rows else None


def list_creditor_entries_for_source(
    *,
    source_app: str,
    source_model: str,
    source_id: str,
    currency_code: Optional[str] = None,
    for_update: bool = False,
) -> list[CreditorDebt]:
    return _sorted_entries_for_source(
        Model=CreditorDebt,
        source_app=source_app,
        source_model=source_model,
        source_id=source_id,
        currency_code=currency_code,
        for_update=for_update,
    )


def resolve_creditor_entry_for_source(
    *,
    source_app: str,
    source_model: str,
    source_id: str,
    currency_code: Optional[str] = None,
    for_update: bool = False,
) -> Optional[CreditorDebt]:
    rows = list_creditor_entries_for_source(
        source_app=source_app,
        source_model=source_model,
        source_id=source_id,
        currency_code=currency_code,
        for_update=for_update,
    )
    return rows[0] if rows else None


def _assert_container_access(*, actor, container: MoneyContainer) -> None:
    if not container.is_active:
        raise ValueError("Container is inactive / disabled")
    if not has_role(actor, AccountProfile.Role.MANAGER):
        if container.allowed_users.exists() and not container.allowed_users.filter(pk=actor.pk).exists():
            raise ValueError("Container access denied for this user")


def _ensure_provider_counterparty(*, provider: Provider) -> Counterparty:
    name = (provider.name or "").strip()
    cp, created = Counterparty.objects.get_or_create(
        type=CounterpartyType.PROVIDER,
        provider_id=provider.id,
        defaults={
            "name": name,
            "is_active": True,
        },
    )
    if not created and (cp.name or "").strip() != name:
        cp.name = name
        cp.save(update_fields=["name"])
    return cp


def _ensure_customer_counterparty(*, customer) -> Counterparty:
    name = (customer.name or "").strip()
    cp, created = Counterparty.objects.get_or_create(
        type=CounterpartyType.CUSTOMER,
        customer_id=customer.id,
        defaults={
            "name": name,
            "is_active": True,
        },
    )
    if not created and (cp.name or "").strip() != name:
        cp.name = name
        cp.save(update_fields=["name"])
    return cp

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

    src_id, legacy_src, cur = _canonical_source_identity(
        source_id=source_id,
        currency_code=currency_code,
    )
    total = _q_money(amount=total, currency_code=cur)
    paid = _q_money(amount=paid_amount or DEC0, currency_code=cur)
    if paid > total:
        paid = total
    remaining = total - paid
    status = DebtorDebt.Status.CLOSED if remaining <= DEC0 else DebtorDebt.Status.OPEN

    party_label = party_name or (provider.name if provider else "")
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
    if legacy_src:
        defaults["legacy_source_id"] = legacy_src

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
    src_id, legacy_src, cur = _canonical_source_identity(
        source_id=source_id,
        currency_code=currency_code,
    )
    total = _q_money(amount=total, currency_code=cur)
    collected = _q_money(amount=collected or DEC0, currency_code=cur)
    if collected > total:
        collected = total
    remaining = total - collected
    status = CreditorDebt.Status.CLOSED if remaining <= DEC0 else CreditorDebt.Status.OPEN

    party_label = party_name or (provider.name if provider else "")
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
    if legacy_src:
        defaults["legacy_source_id"] = legacy_src

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

    currency_code = (currency_code or "SYP").upper()
    if currency_code not in {"SYP", "USD"}:
        raise ValueError("invalid currency")
    amt = _q_money(amount=amount or DEC0, currency_code=currency_code)
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
                amount_signed=-amt,
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
            amount_signed=+amt,
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
    cur = (currency_code or entry.currency_code or "SYP").upper()
    if cur not in {"SYP", "USD"}:
        raise ValueError("invalid currency")
    if (entry.currency_code or "SYP").upper() != cur:
        raise ValueError("currency mismatch for this debt")
    rem = _q_money(amount=entry.remaining, currency_code=cur)
    if rem <= 0:
        return entry

    amt = rem if full else _q_money(amount=amount or DEC0, currency_code=cur)
    if amt <= 0:
        raise ValueError("amount must be positive")
    if amt > rem:
        raise ValueError("amount exceeds remaining")

    if not money_container_id:
        raise ValueError("money_container_id is required")

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
        cash_amount_signed=-amt,
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

    entry.paid_amount = _q_money(amount=(entry.paid_amount or DEC0) + amt, currency_code=cur)
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
    cur = (currency_code or entry.currency_code or "SYP").upper()
    if cur not in {"SYP", "USD"}:
        raise ValueError("invalid currency")
    if (entry.currency_code or "SYP").upper() != cur:
        raise ValueError("currency mismatch for this debt")
    rem = _q_money(amount=entry.remaining, currency_code=cur)
    if rem <= 0:
        return entry

    amt = rem if full else _q_money(amount=amount or DEC0, currency_code=cur)
    if amt <= 0:
        raise ValueError("amount must be positive")
    if amt > rem:
        raise ValueError("amount exceeds remaining")

    if not money_container_id:
        raise ValueError("money_container_id is required")

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
        cash_amount_signed=+amt,
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

    entry.collected = _q_money(amount=(entry.collected or DEC0) + amt, currency_code=cur)
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

