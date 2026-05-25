# app/billing/selectors.py
from __future__ import annotations
from typing import Optional, Any
from decimal import Decimal

from django.db.models import (
    Q, Value, Count, Sum
)
from django.db.models.functions import Coalesce, Lower

from billing.models import Provider, Bill, ProviderReturn, BillItem
from debts.models import (
    DebtDirection,
    DebtCauseType,
    DebtRecord,
    DebtSettlement,
    DebtorPayment,
    CreditorReceipt,
)
from debts.provider_position import collect_provider_open_obligations_batch, get_provider_net_position


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


def _bill_status_label(status: str) -> str:
    mapping = {
        Bill.Status.PAID: "مدفوعة بالكامل",
        Bill.Status.PARTIAL: "مدفوعة جزئياً",
        Bill.Status.UNPAID: "غير مدفوعة",
    }
    return mapping.get((status or "").strip().lower(), "لا توجد بيانات كافية")


def _return_status_label(status: str) -> str:
    mapping = {
        ProviderReturn.Status.PAID: "مدفوعة بالكامل",
        ProviderReturn.Status.PARTIAL: "مدفوعة جزئياً",
        ProviderReturn.Status.UNPAID: "غير مدفوعة",
    }
    return mapping.get((status or "").strip().lower(), "لا توجد بيانات كافية")


def _debt_direction_label(direction: str) -> str:
    code = (direction or "").strip().lower()
    if code == DebtDirection.PAYABLE:
        return "مستحق للمورد"
    if code == DebtDirection.RECEIVABLE:
        return "مستحق على المورد"
    return "لا توجد بيانات كافية"


def _debt_cause_label(cause_type: str) -> str:
    code = (cause_type or "").strip().lower()
    if code == DebtCauseType.PURCHASE_BILL:
        return "فاتورة شراء"
    if code == DebtCauseType.PROVIDER_RETURN:
        return "مرتجع مورد"
    if code == DebtCauseType.MANUAL:
        return "دين يدوي"
    if code == DebtCauseType.POS_BILL:
        return "فاتورة بيع"
    return "لا توجد بيانات كافية"


def _pack_central_settlement_event(settlement: DebtSettlement | None, *, source_label: str) -> dict[str, Any] | None:
    if settlement is None:
        return None
    return {
        "_created_at": settlement.created_at,
        "_sort_id": int(getattr(settlement, "id", 0) or 0),
        "source_label": source_label,
        "created_at": settlement.created_at,
        "public_id": str(getattr(getattr(settlement, "debt", None), "public_id", "") or ""),
        "amount_syp": _as_decimal(getattr(settlement, "applied_syp", DEC0)),
        "amount_usd": _as_decimal(getattr(settlement, "applied_usd", DEC0)),
    }


def _pack_legacy_payment_event(payment: DebtorPayment | None) -> dict[str, Any] | None:
    if payment is None:
        return None
    currency_code = str(getattr(payment, "currency_code", "SYP") or "SYP").upper()
    amount = _as_decimal(getattr(payment, "amount", DEC0))
    return {
        "_created_at": payment.created_at,
        "_sort_id": int(getattr(payment, "id", 0) or 0),
        "source_label": "دفعة دين قديم",
        "created_at": payment.created_at,
        "public_id": "",
        "amount_syp": amount if currency_code == "SYP" else DEC0,
        "amount_usd": amount if currency_code == "USD" else DEC0,
    }


def _pack_legacy_collection_event(receipt: CreditorReceipt | None) -> dict[str, Any] | None:
    if receipt is None:
        return None
    currency_code = str(getattr(receipt, "currency_code", "SYP") or "SYP").upper()
    amount = _as_decimal(getattr(receipt, "amount", DEC0))
    return {
        "_created_at": receipt.created_at,
        "_sort_id": int(getattr(receipt, "id", 0) or 0),
        "source_label": "تحصيل دين قديم",
        "created_at": receipt.created_at,
        "public_id": "",
        "amount_syp": amount if currency_code == "SYP" else DEC0,
        "amount_usd": amount if currency_code == "USD" else DEC0,
    }


def _pick_latest_event(*events: dict[str, Any] | None) -> dict[str, Any] | None:
    valid = [event for event in events if event is not None]
    if not valid:
        return None
    valid.sort(
        key=lambda event: (
            event.get("_created_at"),
            int(event.get("_sort_id") or 0),
        ),
        reverse=True,
    )
    winner = dict(valid[0])
    winner.pop("_created_at", None)
    winner.pop("_sort_id", None)
    return winner


def _build_overall_net_label(*, syp_net: Decimal, usd_net: Decimal, has_open_obligations: bool) -> str:
    has_positive = (syp_net > DEC0) or (usd_net > DEC0)
    has_negative = (syp_net < DEC0) or (usd_net < DEC0)
    if has_positive and not has_negative:
        return "المورد مدين للمتجر"
    if has_negative and not has_positive:
        return "المتجر مدين للمورد"
    if (not has_positive) and (not has_negative):
        if has_open_obligations:
            return "الحساب متعادل حالياً مع وجود ذمم مفتوحة"
        return "الحساب متعادل"
    return "يوجد ذمم متبادلة بين المتجر والمورد"


def get_provider_profile_details(*, provider_id: int, top_items_limit: int = 10) -> dict[str, Any]:
    try:
        requested_limit = int(top_items_limit)
    except Exception:
        requested_limit = 10
    safe_top_items_limit = max(3, min(requested_limit, 10))

    purchase_qs = Bill.objects.filter(provider_id=provider_id)
    returns_qs = ProviderReturn.objects.filter(provider_id=provider_id)

    purchase_bills_count = int(purchase_qs.count())
    provider_returns_count = int(returns_qs.count())

    latest_purchase = (
        purchase_qs
        .only("id", "public_id", "created_at", "total_syp", "total_usd", "settlement_currency")
        .order_by("-created_at", "-id")
        .first()
    )
    latest_return = (
        returns_qs
        .only("id", "public_id", "created_at", "total_syp", "total_usd", "settlement_currency")
        .order_by("-created_at", "-id")
        .first()
    )

    latest_debt = (
        DebtRecord.objects
        .filter(provider_id=provider_id)
        .only(
            "id",
            "public_id",
            "created_at",
            "direction",
            "cause_type",
            "remaining_syp",
            "remaining_usd",
        )
        .order_by("-created_at", "-id")
        .first()
    )

    latest_payment_central = (
        DebtSettlement.objects
        .filter(
            debt__provider_id=provider_id,
            debt__direction=DebtDirection.PAYABLE,
        )
        .select_related("debt")
        .only(
            "id",
            "created_at",
            "applied_syp",
            "applied_usd",
            "debt__public_id",
        )
        .order_by("-created_at", "-id")
        .first()
    )
    latest_payment_legacy = (
        DebtorPayment.objects
        .filter(entry__provider_id=provider_id)
        .order_by("-created_at", "-id")
        .first()
    )
    latest_collection_central = (
        DebtSettlement.objects
        .filter(
            debt__provider_id=provider_id,
            debt__direction=DebtDirection.RECEIVABLE,
        )
        .select_related("debt")
        .only(
            "id",
            "created_at",
            "applied_syp",
            "applied_usd",
            "debt__public_id",
        )
        .order_by("-created_at", "-id")
        .first()
    )
    latest_collection_legacy = (
        CreditorReceipt.objects
        .filter(entry__provider_id=provider_id)
        .order_by("-created_at", "-id")
        .first()
    )

    latest_payment = _pick_latest_event(
        _pack_central_settlement_event(
            latest_payment_central,
            source_label="دفعة تسوية دين مركزي",
        ),
        _pack_legacy_payment_event(latest_payment_legacy),
    )
    latest_collection = _pick_latest_event(
        _pack_central_settlement_event(
            latest_collection_central,
            source_label="تحصيل تسوية دين مركزي",
        ),
        _pack_legacy_collection_event(latest_collection_legacy),
    )

    top_items_rows = (
        BillItem.objects
        .filter(bill__provider_id=provider_id)
        .values("product_id", "product_name_at_txn", "product__name")
        .annotate(
            total_qty=Sum("qty_primary"),
            lines_count=Count("id"),
        )
        .order_by("-total_qty", "-lines_count", "product_id")[:safe_top_items_limit]
    )
    top_items: list[dict[str, Any]] = []
    for row in top_items_rows:
        item_name = str(row.get("product_name_at_txn") or row.get("product__name") or "—").strip() or "—"
        top_items.append(
            {
                "product_name": item_name,
                "total_qty": _as_decimal(row.get("total_qty")),
                "lines_count": int(row.get("lines_count") or 0),
            }
        )

    net_position = get_provider_net_position(provider_id=provider_id)
    currencies = net_position.get("currencies", {}) or {}
    syp_bucket = currencies.get("SYP", {}) or {}
    usd_bucket = currencies.get("USD", {}) or {}

    syp_receivable = _as_decimal(syp_bucket.get("receivable"))
    syp_payable = _as_decimal(syp_bucket.get("payable"))
    syp_net = _as_decimal(syp_bucket.get("net"))
    syp_open_receivable_count = int(syp_bucket.get("open_receivable_count") or 0)
    syp_open_payable_count = int(syp_bucket.get("open_payable_count") or 0)

    usd_receivable = _as_decimal(usd_bucket.get("receivable"))
    usd_payable = _as_decimal(usd_bucket.get("payable"))
    usd_net = _as_decimal(usd_bucket.get("net"))
    usd_open_receivable_count = int(usd_bucket.get("open_receivable_count") or 0)
    usd_open_payable_count = int(usd_bucket.get("open_payable_count") or 0)

    has_open_obligations = (
        syp_open_receivable_count
        + syp_open_payable_count
        + usd_open_receivable_count
        + usd_open_payable_count
    ) > 0

    latest_purchase_summary = None
    if latest_purchase is not None:
        latest_purchase_summary = {
            "public_id": str(latest_purchase.public_id or ""),
            "created_at": latest_purchase.created_at,
            "status_label": _bill_status_label(str(latest_purchase.status or "")),
            "total_syp": _as_decimal(getattr(latest_purchase, "total_syp", DEC0)),
            "total_usd": _as_decimal(getattr(latest_purchase, "total_usd", DEC0)),
            "remaining_syp": _as_decimal(getattr(latest_purchase, "remaining_syp", DEC0)),
            "remaining_usd": _as_decimal(getattr(latest_purchase, "remaining_usd", DEC0)),
        }

    latest_return_summary = None
    if latest_return is not None:
        latest_return_summary = {
            "public_id": str(latest_return.public_id or ""),
            "created_at": latest_return.created_at,
            "status_label": _return_status_label(str(latest_return.status or "")),
            "total_syp": _as_decimal(getattr(latest_return, "total_syp", DEC0)),
            "total_usd": _as_decimal(getattr(latest_return, "total_usd", DEC0)),
            "remaining_syp": _as_decimal(getattr(latest_return, "remaining_syp", DEC0)),
            "remaining_usd": _as_decimal(getattr(latest_return, "remaining_usd", DEC0)),
        }

    latest_debt_summary = None
    if latest_debt is not None:
        latest_debt_summary = {
            "public_id": str(latest_debt.public_id or ""),
            "created_at": latest_debt.created_at,
            "direction_label": _debt_direction_label(str(latest_debt.direction or "")),
            "cause_label": _debt_cause_label(str(latest_debt.cause_type or "")),
            "remaining_syp": _as_decimal(getattr(latest_debt, "remaining_syp", DEC0)),
            "remaining_usd": _as_decimal(getattr(latest_debt, "remaining_usd", DEC0)),
        }

    return {
        "purchase_bills_count": purchase_bills_count,
        "provider_returns_count": provider_returns_count,
        "latest_purchase": latest_purchase_summary,
        "latest_return": latest_return_summary,
        "latest_debt": latest_debt_summary,
        "latest_payment": latest_payment,
        "latest_collection": latest_collection,
        "top_items_metric_label": "الترتيب حسب إجمالي الكمية المشتراة",
        "top_items_default_limit": 3,
        "top_items_max_limit": safe_top_items_limit,
        "top_items": top_items,
        "net_summary": {
            "syp": {
                "payable": syp_payable,
                "receivable": syp_receivable,
                "net": syp_net,
                "open_payable_count": syp_open_payable_count,
                "open_receivable_count": syp_open_receivable_count,
            },
            "usd": {
                "payable": usd_payable,
                "receivable": usd_receivable,
                "net": usd_net,
                "open_payable_count": usd_open_payable_count,
                "open_receivable_count": usd_open_receivable_count,
            },
            "has_open_obligations": has_open_obligations,
            "overall_label": _build_overall_net_label(
                syp_net=syp_net,
                usd_net=usd_net,
                has_open_obligations=has_open_obligations,
            ),
        },
    }

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

