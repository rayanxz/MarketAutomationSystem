# app/billing/selectors.py
from __future__ import annotations
from typing import Optional
from decimal import Decimal

from django.db.models import (
    Q, Value, Count
)
from django.db.models.functions import Coalesce, Lower

from billing.models import Provider, Bill, ProviderReturn
from debts.models import (
    DebtDirection,
)
from debts.provider_position import collect_provider_open_obligations_batch


DEC0 = Decimal("0.00")


# ---------- Providers ----------

def _as_decimal(raw) -> Decimal:
    if raw is None:
        return DEC0
    if isinstance(raw, Decimal):
        return raw
    return Decimal(str(raw))


def _build_provider_payable_snapshot_from_obligations(*, obligations: list[dict]) -> tuple[int, Decimal, Decimal]:
    """
    Compatibility snapshot for provider-list totals:
    - payable-only
    - count rows with positive remaining in either currency
    """
    open_payable_count = 0
    payable_syp = DEC0
    payable_usd = DEC0

    for row in obligations:
        if str(row.get("direction") or "") != DebtDirection.PAYABLE:
            continue
        remaining_syp = _as_decimal(row.get("remaining_syp"))
        remaining_usd = _as_decimal(row.get("remaining_usd"))
        if remaining_syp <= DEC0 and remaining_usd <= DEC0:
            continue
        open_payable_count += 1
        payable_syp += remaining_syp
        payable_usd += remaining_usd

    return open_payable_count, payable_syp, payable_usd

def providers_qs_base():
    # active providers only, order newest first by id (for keyset)
    return Provider.objects.filter(is_active=True).order_by("-id")


def _provider_search_q(query: str) -> Q:
    token = str(query or "").strip()
    if not token:
        return Q()
    q_obj = Q(name__icontains=token) | Q(phone__icontains=token) | Q(public_id__iexact=token)
    if token.upper().startswith("P-"):
        q_obj |= Q(public_id__istartswith=token)
    try:
        provider_id = int(token)
        if provider_id > 0:
            q_obj |= Q(id=provider_id)
    except Exception:
        pass
    return q_obj


def providers_search(q: str):
    qs = providers_qs_base()
    if q:
        qs = qs.filter(_provider_search_q(q))
    return qs


def providers_list_basic(q: str, cursor: Optional[int], page_size: int):
    base = providers_qs_base()
    if q:
        base = base.filter(_provider_search_q(q))
    if cursor:
        base = base.filter(id__lt=cursor)
    return list(base.only("id", "public_id", "name", "phone", "is_active")[:page_size])

def providers_with_stats(q: str, include_all: bool, cursor: Optional[int], page_size: int):
    base = providers_qs_base()
    if q:
        base = base.filter(_provider_search_q(q))
    if cursor:
        base = base.filter(id__lt=cursor)

    scan_size = max(int(page_size or 0) * 3, 60)
    results = []
    scan_cursor = None
    while len(results) < page_size:
        chunk_qs = base
        if scan_cursor is not None:
            chunk_qs = chunk_qs.filter(id__lt=scan_cursor)

        chunk = list(
            chunk_qs
            .annotate(
                bills_count=Coalesce(Count("bills", distinct=True), Value(0)),
            )
            .only("id", "public_id", "name", "phone", "is_active")[:scan_size]
        )
        if not chunk:
            break

        provider_ids = [int(provider.id) for provider in chunk]
        collected_by_provider = collect_provider_open_obligations_batch(provider_ids=provider_ids)

        for provider in chunk:
            collected = collected_by_provider.get(
                int(provider.id),
                {"provider_id": int(provider.id), "obligations": []},
            )
            obligations = list(collected.get("obligations") or [])
            unpaid_bills_count, total_debt_syp, total_debt_usd = _build_provider_payable_snapshot_from_obligations(
                obligations=obligations
            )
            provider.unpaid_bills_count = int(unpaid_bills_count)
            provider.total_debt_syp = total_debt_syp
            provider.total_debt_usd = total_debt_usd

            if include_all or int(provider.bills_count or 0) > 0 or unpaid_bills_count > 0:
                results.append(provider)
                if len(results) >= page_size:
                    break

        scan_cursor = int(chunk[-1].id)

    return results[:page_size]

def providers_ac(q: str):
    if not q:
        return Provider.objects.none().values("id", "public_id", "name", "phone")
    return (
        Provider.objects.filter(is_active=True).filter(_provider_search_q(q))
        .order_by(Lower("name"))
        .values("id", "public_id", "name", "phone")[:8]
    )

# ---------- Bills (commercial docs) ----------

def bills_base():
    return (
        Bill.objects
        .select_related("provider", "created_by")
        .only(
            "id", "public_id", "serial", "total", "total_syp", "total_usd", "created_at",
            "provider__id", "provider__name",
            "created_by__id",
            "created_by__username",
            # if you want full name and your user model supports it, it's still safe
            "created_by__first_name",
            "created_by__last_name",
        )
    )

def bills_list_filters(qs, q, bill_public_id, status, date_from, date_to, cursor, page_size):
    if q:
        qs = qs.filter(provider__name__icontains=q)
    if bill_public_id not in (None, ""):
        ref = str(bill_public_id or "").strip()
        if ref:
            qs = qs.filter(public_id__iexact=ref)
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

def returns_list_filters(q, return_public_id, source_bill_public_id, status, date_from, date_to, cursor, page_size):
    qs = returns_base()
    if q:
        qs = qs.filter(provider__name__icontains=q)
    if return_public_id:
        qs = qs.filter(public_id__iexact=str(return_public_id).strip())
    if source_bill_public_id:
        bill_ref = str(source_bill_public_id).strip()
        bill_serials = list(
            Bill.objects.filter(public_id__iexact=bill_ref).values_list("serial", flat=True)
        )
        source_q = Q(source_bill_public_id__iexact=bill_ref)
        if bill_serials:
            source_q |= Q(source_bill_serial__in=bill_serials)
        qs = qs.filter(source_q)
    # status is a property now; filter in the view at Python level
    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)
    if cursor:
        try:
            qs = qs.filter(id__lt=cursor)
        except ValueError:
            pass
    return qs.order_by("-id")[:page_size]

