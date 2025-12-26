# app/debts/selectors.py
from __future__ import annotations
from typing import Optional
from django.db.models import Q
from debts.models import DebtorDebt as DebtorEntry, CreditorDebt as CreditorEntry

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
