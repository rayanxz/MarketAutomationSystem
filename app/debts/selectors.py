from __future__ import annotations
from typing import Optional
from django.db.models import Q
from debts.models import DebtorDebt as DebtorEntry, CreditorDebt as CreditorEntry

def debtors_list(q: str, status: str, cursor: Optional[int], page_size: int):
    qs = DebtorEntry.objects.select_related("provider").order_by("-id")
    if q:
        qs = qs.filter(Q(provider__name__icontains=q) | Q(party_name__icontains=q))
    if status in {"open", "closed"}:
        qs = qs.filter(status=status)
    if cursor:
        qs = qs.filter(id__lt=cursor)
    return qs[:page_size]

def creditors_list(q: str, status: str, cursor: Optional[int], page_size: int):
    qs = CreditorEntry.objects.select_related("provider").order_by("-id")
    if q:
        qs = qs.filter(Q(provider__name__icontains=q) | Q(party_name__icontains=q))
    if status in {"open", "closed"}:
        qs = qs.filter(status=status)
    if cursor:
        qs = qs.filter(id__lt=cursor)
    return qs[:page_size]
