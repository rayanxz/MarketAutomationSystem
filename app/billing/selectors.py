# app/billing/selectors.py
from __future__ import annotations
from typing import Optional

from django.db.models import (
    Q, F, Value, DecimalField, Count, Sum
)
from django.db.models.functions import Coalesce, Lower

from billing.models import Provider, Bill, ProviderReturn
from debts.models import DebtorDebt as DebtorEntry, CreditorDebt as CreditorEntry


# ---------- Providers ----------

def providers_qs_base():
    # active providers only, order newest first by id (for keyset)
    return Provider.objects.filter(is_active=True).order_by("-id")

def providers_search(q: str):
    qs = providers_qs_base()
    if q:
        qs = qs.filter(name__icontains=q)
    return qs

def providers_with_stats(q: str, include_all: bool, cursor: Optional[int], page_size: int):
    base = providers_qs_base()
    if q:
        base = base.filter(name__icontains=q)

    # Stats based on subledger:
    # - bills_count: total bills
    # - unpaid_bills_count: open debtor entries (one per bill)
    # - total_debt: sum remaining of open debtor entries
    qs = (
        base
        .annotate(
            bills_count=Coalesce(Count("bills", distinct=True), Value(0)),
            unpaid_bills_count=Coalesce(
                Count("debtor_entries", filter=Q(debtor_entries__status=DebtorEntry.Status.OPEN), distinct=True),
                Value(0),
            ),
            total_debt=Coalesce(
                Sum(
                    F("debtor_entries__total") - F("debtor_entries__paid_amount"),
                    filter=Q(debtor_entries__status=DebtorEntry.Status.OPEN),
                    output_field=DecimalField(max_digits=14, decimal_places=3),
                ),
                Value(0, output_field=DecimalField(max_digits=14, decimal_places=3)),
                output_field=DecimalField(max_digits=14, decimal_places=3),
            ),
        )
        .only("id", "name", "phone", "is_active")
    )

    if cursor:
        qs = qs.filter(id__lt=cursor)

    if not include_all:
        qs = qs.filter(Q(bills_count__gt=0) | Q(unpaid_bills_count__gt=0))

    return qs[:page_size]

def providers_ac(q: str):
    if not q:
        return Provider.objects.none().values("id", "name")
    return (
        Provider.objects.filter(is_active=True, name__icontains=q)
        .order_by(Lower("name"))
        .values("id", "name")[:8]
    )

# ---------- Bills (commercial docs) ----------

def bills_base():
    # Note: no status/paid_amount fields anymore; these are properties resolved from DebtorEntry.
    return (
        Bill.objects.select_related("provider")
        .only("id", "serial", "total", "created_at", "provider__id", "provider__name")
    )

def bills_list_filters(qs, q, serial, status, date_from, date_to, cursor, page_size):
    if q:
        qs = qs.filter(provider__name__icontains=q)
    if serial not in (None, ""):
        try:
            qs = qs.filter(serial=int(serial))
        except ValueError:
            return qs.none()
    # status is filtered at the view level using Bill.status property (Python), to avoid complex subqueries
    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)
    if cursor:
        try:
            qs = qs.filter(id__lt=int(cursor))
        except ValueError:
            pass
    return qs

# ---------- Serials & returns ----------

def returns_base():
    return ProviderReturn.objects.select_related("provider")

def returns_list_filters(q, serial, rid, bill_serial, status, date_from, date_to, cursor, page_size):
    qs = returns_base()
    if q:
        qs = qs.filter(provider__name__icontains=q)
    if serial:
        qs = qs.filter(serial=serial)
    if rid:
        qs = qs.filter(id=rid)
    if bill_serial:
        qs = qs.filter(source_bill_serial=bill_serial)
    # status is a property now; filter in the view at Python level
    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)
    if cursor:
        qs = qs.filter(id__lt=cursor)
    return qs.order_by("-id")[:page_size]

