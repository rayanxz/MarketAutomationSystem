# app/debts/services.py
from __future__ import annotations
from decimal import Decimal
from datetime import date
from typing import Optional, Any
import logging
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone

from debts.models import (
    DebtorDebt,
    DebtorPayment,
    CreditorDebt,
    CreditorReceipt,
    DebtRecord,
    DebtSettlement,
    DebtDirection,
    DebtCauseType,
    DebtStatus,
    OtherPartyType,
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


def _assert_container_access(
    *,
    actor,
    container: MoneyContainer,
    required_feature_code: Any = None,
) -> None:
    FinSV.assert_money_container_access(
        user=actor,
        container=container,
        feature_code=required_feature_code,
    )


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


def _q_syp(amount: Decimal) -> Decimal:
    return _q_money(amount=amount, currency_code="SYP")


def _q_usd(amount: Decimal) -> Decimal:
    return _q_money(amount=amount, currency_code="USD")


def _q_fx(value: Decimal) -> Decimal:
    return FinSV.q_fx(Decimal(value or DEC0))


def _debt_status_from_remaining(*, remaining_syp: Decimal, remaining_usd: Decimal) -> str:
    rem_syp = _q_syp(remaining_syp or DEC0)
    rem_usd = _q_usd(remaining_usd or DEC0)
    if rem_syp <= DEC0 and rem_usd <= DEC0:
        return DebtStatus.CLOSED
    return DebtStatus.OPEN


def _debt_remaining_settlement_syp(*, remaining_syp: Decimal, remaining_usd: Decimal, fx_syp_per_usd: Decimal) -> Decimal:
    return _q_syp((remaining_syp or DEC0) + ((remaining_usd or DEC0) * fx_syp_per_usd))


@transaction.atomic
def upsert_central_debt(
    *,
    direction: str,
    cause_type: str,
    cause_id: str,
    source_app: str,
    other_party_type: str,
    other_party_id: str = "",
    provider: Optional[Provider] = None,
    customer_id: Optional[int] = None,
    actor_username: str = "",
    total_syp: Decimal = DEC0,
    total_usd: Decimal = DEC0,
    remaining_syp: Decimal = DEC0,
    remaining_usd: Decimal = DEC0,
    fx_syp_per_usd_at_creation: Decimal | None = None,
    note: str = "",
) -> DebtRecord:
    total_syp_q = _q_syp(total_syp or DEC0)
    total_usd_q = _q_usd(total_usd or DEC0)
    remaining_syp_q = _q_syp(remaining_syp or DEC0)
    remaining_usd_q = _q_usd(remaining_usd or DEC0)
    if remaining_syp_q > total_syp_q:
        raise ValueError("remaining_syp cannot exceed total_syp")
    if remaining_usd_q > total_usd_q:
        raise ValueError("remaining_usd cannot exceed total_usd")
    status = _debt_status_from_remaining(remaining_syp=remaining_syp_q, remaining_usd=remaining_usd_q)

    defaults = {
        "source_app": (source_app or "").strip(),
        "other_party_type": (other_party_type or OtherPartyType.OTHER),
        "other_party_id": str(other_party_id or ""),
        "provider": provider,
        "customer_id": customer_id,
        "actor_username": (actor_username or "").strip(),
        "total_syp": total_syp_q,
        "total_usd": total_usd_q,
        "remaining_syp": remaining_syp_q,
        "remaining_usd": remaining_usd_q,
        "status": status,
        "note": note or "",
    }
    lookup = {
        "direction": direction,
        "cause_type": cause_type,
        "cause_id": str(cause_id),
    }
    debt = (
        DebtRecord.objects
        .select_for_update()
        .filter(**lookup)
        .order_by("id")
        .first()
    )
    if debt is None:
        create_fx = fx_syp_per_usd_at_creation
        if create_fx is None:
            try:
                create_fx = FinSV.get_current_fx_syp_per_usd()
            except Exception:
                create_fx = None
        if create_fx is not None:
            create_fx = _q_fx(create_fx)
            if create_fx <= DEC0:
                raise ValueError("fx_syp_per_usd_at_creation must be > 0")
        create_payload = dict(defaults)
        create_payload.update(lookup)
        create_payload["fx_syp_per_usd_at_creation"] = create_fx
        try:
            return DebtRecord.objects.create(**create_payload)
        except IntegrityError:
            debt = (
                DebtRecord.objects
                .select_for_update()
                .filter(**lookup)
                .order_by("id")
                .first()
            )
            if debt is None:
                raise

    for field, value in defaults.items():
        setattr(debt, field, value)
    debt.save(update_fields=list(defaults.keys()))
    return debt


def resolve_central_debt_for_cause(
    *,
    direction: str,
    cause_type: str,
    cause_id: str,
    for_update: bool = False,
) -> Optional[DebtRecord]:
    qs = DebtRecord.objects.filter(
        direction=direction,
        cause_type=cause_type,
        cause_id=str(cause_id),
    )
    if for_update:
        qs = qs.select_for_update()
    return qs.order_by("id").first()


def resolve_purchase_bill_debt(*, bill_id: int | str, for_update: bool = False) -> Optional[DebtRecord]:
    token = str(bill_id or "").strip()
    if not token:
        return None

    cause_refs: list[str] = [token]
    if token.isdigit():
        try:
            from billing.models import Bill

            bill = Bill.objects.filter(pk=int(token)).only("public_id").first()
            if bill and (bill.public_id or "").strip():
                cause_refs.insert(0, bill.public_id.strip())
        except Exception:
            pass
    elif token.upper().startswith("PB-"):
        try:
            from billing.models import Bill

            bill = Bill.objects.filter(public_id__iexact=token).only("id").first()
            if bill:
                cause_refs.append(str(bill.id))
        except Exception:
            pass

    qs = DebtRecord.objects.filter(
        direction=DebtDirection.PAYABLE,
        cause_type=DebtCauseType.PURCHASE_BILL,
        cause_id__in=cause_refs,
    )
    if for_update:
        qs = qs.select_for_update()
    return qs.order_by("id").first()


def _ensure_debt_counterparty(*, debt: DebtRecord) -> Counterparty:
    ptype = (debt.other_party_type or "").lower().strip()
    if ptype == OtherPartyType.CUSTOMER:
        if not debt.customer_id:
            raise ValueError("customer is required for customer debt")
        return _ensure_customer_counterparty(customer=debt.customer)
    if not debt.provider_id:
        raise ValueError("provider is required for provider debt")
    return _ensure_provider_counterparty(provider=debt.provider)


def _apply_payment_to_central_remaining(
    *,
    remaining_syp: Decimal,
    remaining_usd: Decimal,
    pay_syp: Decimal,
    pay_usd: Decimal,
    fx_syp_per_usd: Decimal,
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    rem_syp_before = _q_syp(remaining_syp or DEC0)
    rem_usd_before = _q_usd(remaining_usd or DEC0)

    rem_syp = Decimal(rem_syp_before)
    rem_usd = Decimal(rem_usd_before)
    pool_syp = Decimal(_q_syp(pay_syp or DEC0))
    pool_usd = Decimal(_q_usd(pay_usd or DEC0))

    pay_syp_native = min(pool_syp, rem_syp)
    rem_syp -= pay_syp_native
    pool_syp -= pay_syp_native

    pay_usd_native = min(pool_usd, rem_usd)
    rem_usd -= pay_usd_native
    pool_usd -= pay_usd_native

    if pool_syp > DEC0 and rem_usd > DEC0:
        usd_extra = min(rem_usd, (pool_syp / fx_syp_per_usd))
        rem_usd -= usd_extra
        pool_syp -= (usd_extra * fx_syp_per_usd)

    if pool_usd > DEC0 and rem_syp > DEC0:
        syp_extra = min(rem_syp, (pool_usd * fx_syp_per_usd))
        rem_syp -= syp_extra
        pool_usd -= (syp_extra / fx_syp_per_usd)

    rem_syp_after = _q_syp(max(DEC0, rem_syp))
    rem_usd_after = _q_usd(max(DEC0, rem_usd))
    applied_syp = _q_syp(rem_syp_before - rem_syp_after)
    applied_usd = _q_usd(rem_usd_before - rem_usd_after)
    return rem_syp_after, rem_usd_after, applied_syp, applied_usd


def _resolve_component_payment_for_settlement(
    *,
    full: bool,
    payment_method: str | None,
    rem_syp: Decimal,
    rem_usd: Decimal,
    fx_syp_per_usd: Decimal,
    payment_syp: Decimal | None,
    payment_usd: Decimal | None,
) -> tuple[Decimal, Decimal, str]:
    method = (payment_method or "").strip().lower()
    if method and method not in {"syp_only", "usd_only", "mixed", "separate"}:
        raise ValueError("invalid payment method")

    pay_syp = _q_syp(payment_syp or DEC0)
    pay_usd = _q_usd(payment_usd or DEC0)

    if pay_syp < DEC0 or pay_usd < DEC0:
        raise ValueError("payment amounts cannot be negative")

    if full:
        if method == "syp_only":
            pay_syp = _q_syp(rem_syp + (rem_usd * fx_syp_per_usd))
            pay_usd = DEC0
        elif method == "usd_only":
            pay_syp = DEC0
            pay_usd = _q_usd(rem_usd + (rem_syp / fx_syp_per_usd))
        elif method == "separate":
            pay_syp = rem_syp
            pay_usd = rem_usd
        elif pay_syp <= DEC0 and pay_usd <= DEC0:
            # Mixed full (or unspecified method): if UI did not send values,
            # use exact remaining legs by default.
            pay_syp = rem_syp
            pay_usd = rem_usd
    else:
        if method == "separate":
            raise ValueError("separate payment mode is only allowed for full settlement")
        if method == "syp_only" and pay_usd > DEC0:
            raise ValueError("USD amount must be 0 for SYP-only payment mode")
        if method == "usd_only" and pay_syp > DEC0:
            raise ValueError("SYP amount must be 0 for USD-only payment mode")
        if method == "mixed" and (pay_syp <= DEC0 or pay_usd <= DEC0):
            raise ValueError("mixed partial payment requires both SYP and USD amounts")

    if pay_syp <= DEC0 and pay_usd <= DEC0:
        raise ValueError("payment amount must be positive")

    return pay_syp, pay_usd, method


@transaction.atomic
def settle_central_debt(
    *,
    actor,
    debt_id: int,
    amount: Optional[Decimal] = None,
    full: bool = False,
    money_container_id: int,
    currency_code: str | None = None,
    payment_syp: Optional[Decimal] = None,
    payment_usd: Optional[Decimal] = None,
    payment_method: str | None = None,
    required_feature_code: Any = None,
    note: str = "",
) -> tuple[DebtRecord, DebtSettlement]:
    debt = DebtRecord.objects.select_for_update().get(pk=debt_id)
    if debt.direction not in {DebtDirection.PAYABLE, DebtDirection.RECEIVABLE}:
        raise ValueError("invalid debt direction")

    rem_syp_before = _q_syp(debt.remaining_syp or DEC0)
    rem_usd_before = _q_usd(debt.remaining_usd or DEC0)
    if rem_syp_before <= DEC0 and rem_usd_before <= DEC0:
        raise ValueError("debt is already closed")

    fx = _q_fx(FinSV.get_current_fx_syp_per_usd())
    rem_settlement_syp = _debt_remaining_settlement_syp(
        remaining_syp=rem_syp_before,
        remaining_usd=rem_usd_before,
        fx_syp_per_usd=fx,
    )
    uses_component_payload = (payment_syp is not None) or (payment_usd is not None)
    if uses_component_payload:
        pay_syp, pay_usd, _ = _resolve_component_payment_for_settlement(
            full=full,
            payment_method=payment_method,
            rem_syp=rem_syp_before,
            rem_usd=rem_usd_before,
            fx_syp_per_usd=fx,
            payment_syp=payment_syp,
            payment_usd=payment_usd,
        )
    else:
        cur = (currency_code or "SYP").upper().strip()
        if cur not in {"SYP", "USD"}:
            raise ValueError("invalid currency")
        if full:
            pay_syp = _q_syp(rem_syp_before + (rem_usd_before * fx)) if cur == "SYP" else DEC0
            pay_usd = _q_usd(rem_usd_before + (rem_syp_before / fx)) if cur == "USD" else DEC0
        else:
            amt = _q_money(amount=amount or DEC0, currency_code=cur)
            if amt <= DEC0:
                raise ValueError("amount must be positive")
            pay_syp = amt if cur == "SYP" else DEC0
            pay_usd = amt if cur == "USD" else DEC0

    pay_settlement_syp = _q_syp(pay_syp + (pay_usd * fx))
    if pay_settlement_syp <= DEC0:
        raise ValueError("payment amount must be positive")

    if pay_settlement_syp > rem_settlement_syp:
        raise ValueError("amount exceeds remaining")
    if (not full) and (pay_settlement_syp == rem_settlement_syp):
        raise ValueError("partial settlement cannot equal full remaining")

    rem_syp_after, rem_usd_after, applied_syp, applied_usd = _apply_payment_to_central_remaining(
        remaining_syp=rem_syp_before,
        remaining_usd=rem_usd_before,
        pay_syp=pay_syp,
        pay_usd=pay_usd,
        fx_syp_per_usd=fx,
    )

    if full:
        if rem_syp_after > DEC0 or rem_usd_after > DEC0:
            raise ValueError("full settlement must clear remaining")
        rem_syp_after = DEC0
        rem_usd_after = DEC0
        applied_syp = rem_syp_before
        applied_usd = rem_usd_before
    elif rem_syp_after <= DEC0 and rem_usd_after <= DEC0:
        raise ValueError("partial settlement cannot equal full remaining")

    if money_container_id is None:
        raise ValueError("money_container_id is required")
    container = MoneyContainer.objects.select_for_update().get(pk=money_container_id)
    _assert_container_access(
        actor=actor,
        container=container,
        required_feature_code=required_feature_code,
    )
    if pay_syp > DEC0 and not MoneyContainerCurrency.objects.filter(
        container_id=container.id,
        currency__code="SYP",
        is_enabled=True,
    ).exists():
        raise ValueError("Currency SYP is disabled for this container")
    if pay_usd > DEC0 and not MoneyContainerCurrency.objects.filter(
        container_id=container.id,
        currency__code="USD",
        is_enabled=True,
    ).exists():
        raise ValueError("Currency USD is disabled for this container")

    cp = _ensure_debt_counterparty(debt=debt)
    sign = -1 if debt.direction == DebtDirection.PAYABLE else 1
    cash_by_code_signed: dict[str, Decimal] = {}
    if pay_syp > DEC0:
        cash_by_code_signed["SYP"] = _q_syp(Decimal(sign) * pay_syp)
    if pay_usd > DEC0:
        cash_by_code_signed["USD"] = _q_usd(Decimal(sign) * pay_usd)

    receipt = FinSV.post_settlement_components_with_fx(
        actor=actor,
        container_id=container.id,
        counterparty_id=cp.id,
        cash_by_code_signed=cash_by_code_signed,
        fx_syp_per_usd=fx,
        note=(note or f"Central debt settlement {debt.public_id}"),
        source_app="debts",
        source_model="DebtRecord",
        source_id=str(debt.id),
    )

    debt.remaining_syp = rem_syp_after
    debt.remaining_usd = rem_usd_after
    debt.status = _debt_status_from_remaining(
        remaining_syp=debt.remaining_syp,
        remaining_usd=debt.remaining_usd,
    )
    debt.save(update_fields=["remaining_syp", "remaining_usd", "status"])

    settlement = DebtSettlement.objects.create(
        debt=debt,
        actor_username=getattr(actor, "username", "") or "",
        payment_syp=pay_syp,
        payment_usd=pay_usd,
        applied_syp=applied_syp,
        applied_usd=applied_usd,
        fx_syp_per_usd_used=fx,
        receipt=receipt,
        money_container=container,
        note=note or "",
    )

    return debt, settlement

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

def _manual_other_party_type_from_entry(entry) -> str:
    ptype = (getattr(entry, "party_type", "") or "").lower().strip()
    if ptype == PartyType.PROVIDER:
        return OtherPartyType.PROVIDER
    if ptype == PartyType.CUSTOMER:
        return OtherPartyType.CUSTOMER
    return OtherPartyType.OTHER


def _manual_other_party_id_from_entry(entry) -> str:
    if getattr(entry, "provider_id", None):
        return str(entry.provider_id)
    if getattr(entry, "customer_id", None):
        return str(entry.customer_id)
    return (getattr(entry, "party_name", "") or "").strip()


def _sync_manual_debtor_central_debt(*, entry: DebtorDebt, actor_username: str = "") -> None:
    if (entry.source_app, entry.source_model) != ("debts", "ManualDebt"):
        return
    cur = (entry.currency_code or "SYP").upper()
    total = _q_money(amount=entry.total or DEC0, currency_code=cur)
    remaining = _q_money(amount=entry.remaining, currency_code=cur)
    upsert_central_debt(
        direction=DebtDirection.PAYABLE,
        cause_type=DebtCauseType.MANUAL,
        cause_id=str(entry.id),
        source_app="debts",
        other_party_type=_manual_other_party_type_from_entry(entry),
        other_party_id=_manual_other_party_id_from_entry(entry),
        provider=(entry.provider if entry.provider_id else None),
        customer_id=getattr(entry, "customer_id", None),
        actor_username=actor_username or "",
        total_syp=(total if cur == "SYP" else DEC0),
        total_usd=(total if cur == "USD" else DEC0),
        remaining_syp=(remaining if cur == "SYP" else DEC0),
        remaining_usd=(remaining if cur == "USD" else DEC0),
        note=f"Manual debtor debt #{entry.id}",
    )


def _sync_manual_creditor_central_debt(*, entry: CreditorDebt, actor_username: str = "") -> None:
    if (entry.source_app, entry.source_model) != ("debts", "ManualDebt"):
        return
    cur = (entry.currency_code or "SYP").upper()
    total = _q_money(amount=entry.total or DEC0, currency_code=cur)
    remaining = _q_money(amount=entry.remaining, currency_code=cur)
    upsert_central_debt(
        direction=DebtDirection.RECEIVABLE,
        cause_type=DebtCauseType.MANUAL,
        cause_id=str(entry.id),
        source_app="debts",
        other_party_type=_manual_other_party_type_from_entry(entry),
        other_party_id=_manual_other_party_id_from_entry(entry),
        provider=(entry.provider if entry.provider_id else None),
        customer_id=getattr(entry, "customer_id", None),
        actor_username=actor_username or "",
        total_syp=(total if cur == "SYP" else DEC0),
        total_usd=(total if cur == "USD" else DEC0),
        remaining_syp=(remaining if cur == "SYP" else DEC0),
        remaining_usd=(remaining if cur == "USD" else DEC0),
        note=f"Manual creditor debt #{entry.id}",
    )


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
        _sync_manual_debtor_central_debt(
            entry=entry,
            actor_username=(getattr(actor, "username", "") or ""),
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
            entry = pay_debt(
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
    _sync_manual_creditor_central_debt(
        entry=entry,
        actor_username=(getattr(actor, "username", "") or ""),
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
        entry = collect_debt(
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
    required_feature_code: Any = None,
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
    _assert_container_access(
        actor=actor,
        container=container,
        required_feature_code=required_feature_code,
    )

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

    _sync_manual_debtor_central_debt(
        entry=entry,
        actor_username=(getattr(actor, "username", "") or ""),
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
    required_feature_code: Any = None,
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
    _assert_container_access(
        actor=actor,
        container=container,
        required_feature_code=required_feature_code,
    )

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

    _sync_manual_creditor_central_debt(
        entry=entry,
        actor_username=(getattr(actor, "username", "") or ""),
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

