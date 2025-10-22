# app/billing/selectors.py
from __future__ import annotations
from decimal import Decimal
from typing import Optional, Tuple
from django.db.models import Q, F, Value, DecimalField, IntegerField, Case, When , Count , Sum
from django.db.models.functions import Coalesce, Lower
from billing.models import Provider, Bill

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
    # start from base
    base = providers_qs_base()
    if q:
        base = base.filter(name__icontains=q)

    debt_expr = Coalesce(
        Sum(
            F("bills__total") - F("bills__paid_amount"),
            filter=Q(bills__status__in=[Bill.Status.UNPAID, Bill.Status.PARTIAL]),
            output_field=DecimalField(max_digits=14, decimal_places=3),
        ),
        Value(0, output_field=DecimalField(max_digits=14, decimal_places=3)),
        output_field=DecimalField(max_digits=14, decimal_places=3),
    )

    qs = (
        base
        .annotate(
            bills_count=Coalesce(Count("bills", distinct=True), Value(0)),
            unpaid_bills_count=Coalesce(
                Count("bills", filter=Q(bills__status__in=[Bill.Status.UNPAID, Bill.Status.PARTIAL]), distinct=True),
                Value(0),
            ),
            total_debt=debt_expr,
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

# ---------- Bills ----------
def bills_base():
    return (
        Bill.objects.select_related("provider")
        .only("id", "serial", "total", "status", "paid_amount", "created_at",
              "provider__id", "provider__name")
    )

def bills_list_filters(qs, q, serial, bill_id, status, date_from, date_to, cursor, page_size):
    if q:
        qs = qs.filter(provider__name__icontains=q)
    if serial not in (None, ""):
        try:
            qs = qs.filter(serial=int(serial))
        except ValueError:
            return qs.none()
    if bill_id not in (None, ""):
        try:
            qs = qs.filter(id=int(bill_id))
        except ValueError:
            return qs.none()
    if status in {"paid", "unpaid", "partial"}:
        qs = qs.filter(status=status)
    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)
    if cursor:
        try:
            qs = qs.filter(id__lt=int(cursor))
        except ValueError:
            pass
    # IMPORTANT: no order_by or slicing here — callers will do it
    return qs

def debts_list(q, serial, bill_id, status, date_from, date_to, cursor, page_size):
    qs = bills_base().annotate(
        remaining_calc=Coalesce(
            F("total") - Coalesce(F("paid_amount"),
                                  Value(0, output_field=DecimalField(max_digits=14, decimal_places=3))),
            Value(0, output_field=DecimalField(max_digits=14, decimal_places=3)),
        )
    )

    qs = bills_list_filters(qs, q, serial, bill_id, status, date_from, date_to, cursor, page_size)

    status_weight = Case(
        When(status=Bill.Status.UNPAID, then=Value(0)),
        When(status=Bill.Status.PARTIAL, then=Value(1)),
        When(status=Bill.Status.PAID,   then=Value(2)),
        default=Value(3),
        output_field=IntegerField(),
    )
    qs = qs.order_by(status_weight, "-created_at", "-id")
    return qs[:page_size]


def next_bill_serial() -> int:
    last = Bill.objects.order_by("-serial").values_list("serial", flat=True).first()
    return 1 if (last in (None, 0)) else int(last) + 1
