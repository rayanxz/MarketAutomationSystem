# app/debts/selectors.py
from __future__ import annotations
from typing import Optional
from django.db.models import Q
from debts.models import (
    DebtorDebt as DebtorEntry,
    CreditorDebt as CreditorEntry,
    DebtRecord,
    DebtDirection,
    DebtCauseType,
    OtherPartyType,
)

def debtors_list(
    *,
    q: str,
    status: str,
    cursor: Optional[int],
    page_size: int,
    serial: Optional[int] = None,
    date_from=None,   # date | None
    date_to=None,     # date | None
):
    qs = DebtorEntry.objects.select_related("provider").order_by("-id")

    if q:
        qs = qs.filter(Q(provider__name__icontains=q) | Q(party_name__icontains=q))

    if status in {"open", "closed"}:
        qs = qs.filter(status=status)

    # ✅ serial filter (works ONLY if doc_serial is filled for all entries)
    if serial is not None:
        qs = qs.filter(doc_serial=serial)

    # ✅ date range filter
    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)

    if cursor:
        qs = qs.filter(id__lt=cursor)

    return qs[:page_size]


def creditors_list(
    *,
    q: str,
    status: str,
    cursor: Optional[int],
    page_size: int,
    serial: Optional[int] = None,
    date_from=None,
    date_to=None,
):
    qs = CreditorEntry.objects.select_related("provider").order_by("-id")

    if q:
        qs = qs.filter(Q(provider__name__icontains=q) | Q(party_name__icontains=q))

    if status in {"open", "closed"}:
        qs = qs.filter(status=status)

    if serial is not None:
        qs = qs.filter(doc_serial=serial)

    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)

    if cursor:
        qs = qs.filter(id__lt=cursor)

    return qs[:page_size]


def central_debts_list(
    *,
    cursor: Optional[int],
    page_size: int,
    status: str = "",
    debt_type: str = "",
    cause_type: str = "",
    cause_id: str = "",
    other_party_type: str = "",
    other_party_name: str = "",
    other_party_id: str = "",
    debt_id: str = "",
    date_from=None,
    date_to=None,
):
    qs = DebtRecord.objects.select_related("provider", "customer").order_by("-id")

    if status in {"open", "closed"}:
        qs = qs.filter(status=status)

    debt_type_norm = (debt_type or "").strip().lower()
    if debt_type_norm == "debtor":
        qs = qs.filter(direction=DebtDirection.PAYABLE)
    elif debt_type_norm == "creditor":
        qs = qs.filter(direction=DebtDirection.RECEIVABLE)

    cause_type_norm = (cause_type or "").strip().lower()
    if cause_type_norm in {
        DebtCauseType.PURCHASE_BILL,
        DebtCauseType.POS_BILL,
        DebtCauseType.PROVIDER_RETURN,
        DebtCauseType.MANUAL,
    }:
        qs = qs.filter(cause_type=cause_type_norm)
        cause_id_norm = (cause_id or "").strip()
        if cause_id_norm:
            qs = qs.filter(cause_id=cause_id_norm)

    party_type_norm = (other_party_type or "").strip().lower()
    if party_type_norm in {
        OtherPartyType.PROVIDER,
        OtherPartyType.CUSTOMER,
        OtherPartyType.SYSTEM_USER,
        OtherPartyType.OTHER,
    }:
        qs = qs.filter(other_party_type=party_type_norm)

    party_id_norm = (other_party_id or "").strip()
    if party_id_norm:
        qs = qs.filter(other_party_id=party_id_norm)

    party_name_norm = (other_party_name or "").strip()
    if party_name_norm:
        if party_type_norm == OtherPartyType.PROVIDER:
            qs = qs.filter(
                Q(provider__name__icontains=party_name_norm)
                | Q(other_party_id__icontains=party_name_norm)
            )
        elif party_type_norm == OtherPartyType.CUSTOMER:
            qs = qs.filter(
                Q(customer__name__icontains=party_name_norm)
                | Q(other_party_id__icontains=party_name_norm)
            )
        elif party_type_norm == OtherPartyType.SYSTEM_USER:
            qs = qs.filter(
                Q(actor_username__icontains=party_name_norm)
                | Q(other_party_id__icontains=party_name_norm)
            )
        else:
            qs = qs.filter(
                Q(provider__name__icontains=party_name_norm)
                | Q(customer__name__icontains=party_name_norm)
                | Q(actor_username__icontains=party_name_norm)
                | Q(other_party_id__icontains=party_name_norm)
            )

    debt_id_norm = (debt_id or "").strip()
    if debt_id_norm:
        q_id = Q(public_id__iexact=debt_id_norm)
        try:
            q_id |= Q(id=int(debt_id_norm))
        except Exception:
            pass
        qs = qs.filter(q_id)

    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)

    if cursor:
        qs = qs.filter(id__lt=cursor)

    return qs[:page_size]
