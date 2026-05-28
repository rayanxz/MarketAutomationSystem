# app/billing/selectors.py
from __future__ import annotations
from collections import Counter
from typing import Optional, Any
from decimal import Decimal

from django.db.models import (
    Q, Value, Count, Sum
)
from django.db.models.functions import Coalesce, Lower
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from billing.models import Provider, Bill, ProviderReturn, BillItem
from debts.models import (
    DebtDirection,
    DebtCauseType,
    DebtStatus,
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


def _build_account_state_label(*, total_payable: Decimal, total_receivable: Decimal) -> str:
    if total_payable > DEC0 and total_receivable > DEC0:
        return "توجد ديون باتجاهين"
    if total_payable > DEC0:
        return "المتجر مدين للمورد"
    if total_receivable > DEC0:
        return "المورد مدين للمتجر"
    return "متوازن"


def _build_currency_net_label(*, net: Decimal) -> str:
    if net > DEC0:
        return "المورد مدين للمتجر"
    if net < DEC0:
        return "المتجر مدين للمورد"
    return "متوازن"


def _pick_latest_profile_activity(*activities: dict[str, Any] | None) -> dict[str, Any] | None:
    valid = [
        activity
        for activity in activities
        if activity is not None and activity.get("created_at") is not None
    ]
    if not valid:
        return None

    valid.sort(
        key=lambda activity: (
            activity.get("created_at"),
            int(activity.get("sort_order") or 0),
        ),
        reverse=True,
    )
    winner = dict(valid[0])
    winner.pop("sort_order", None)
    return winner


def _safe_reverse(route_name: str, *, kwargs: dict[str, Any] | None = None) -> str:
    try:
        return reverse(route_name, kwargs=kwargs or {})
    except NoReverseMatch:
        return ""


def _fmt_dt(value) -> str:
    if value is None:
        return ""
    try:
        value = timezone.localtime(value)
    except Exception:
        pass
    try:
        return value.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ""


def _percent_share(*, part: int, total: int) -> Decimal:
    if total <= 0:
        return DEC0
    try:
        return ((Decimal(part) * Decimal("100.00")) / Decimal(total)).quantize(Decimal("0.01"))
    except Exception:
        return DEC0


def _build_totals_share(*, provider_count: int, total_count: int) -> dict[str, Any]:
    provider_percent = _percent_share(part=provider_count, total=total_count)
    others_percent = DEC0
    if total_count > 0:
        others_percent = Decimal("100.00") - provider_percent
        if others_percent < DEC0:
            others_percent = DEC0
    return {
        "provider_count": int(provider_count),
        "total_count": int(total_count),
        "provider_percent": provider_percent,
        "others_percent": others_percent,
    }


def _debt_status_label(status: str) -> str:
    code = (status or "").strip().lower()
    if code == DebtStatus.OPEN:
        return "مفتوح"
    if code == DebtStatus.CLOSED:
        return "مغلق"
    return "لا توجد بيانات كافية"


def _pick_latest_model_event(*, first, second, first_kind: str, second_kind: str) -> tuple[str, Any] | tuple[None, None]:
    rows: list[tuple[str, Any, int]] = []
    if first is not None and getattr(first, "created_at", None) is not None:
        rows.append((first_kind, first, int(getattr(first, "id", 0) or 0)))
    if second is not None and getattr(second, "created_at", None) is not None:
        rows.append((second_kind, second, int(getattr(second, "id", 0) or 0)))
    if not rows:
        return None, None
    rows.sort(key=lambda row: (getattr(row[1], "created_at", None), row[2]), reverse=True)
    return rows[0][0], rows[0][1]


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
    max_top_item_qty = max((item["total_qty"] for item in top_items), default=DEC0)
    for item in top_items:
        if max_top_item_qty > DEC0:
            item["percent_of_top"] = (item["total_qty"] / max_top_item_qty) * Decimal("100")
        else:
            item["percent_of_top"] = DEC0

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
    total_payable = syp_payable + usd_payable
    total_receivable = syp_receivable + usd_receivable
    account_state_label = _build_account_state_label(
        total_payable=total_payable,
        total_receivable=total_receivable,
    )

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

    latest_activity = _pick_latest_profile_activity(
        (
            {
                "key": "latest-purchase",
                "label": "آخر فاتورة شراء",
                "created_at": latest_purchase_summary["created_at"],
                "public_id": latest_purchase_summary.get("public_id") or "",
                "sort_order": 1,
            }
            if latest_purchase_summary is not None
            else None
        ),
        (
            {
                "key": "latest-return",
                "label": "آخر إرجاع",
                "created_at": latest_return_summary["created_at"],
                "public_id": latest_return_summary.get("public_id") or "",
                "sort_order": 2,
            }
            if latest_return_summary is not None
            else None
        ),
        (
            {
                "key": "latest-debt",
                "label": "آخر عملية دين",
                "created_at": latest_debt_summary["created_at"],
                "public_id": latest_debt_summary.get("public_id") or "",
                "sort_order": 3,
            }
            if latest_debt_summary is not None
            else None
        ),
        (
            {
                "key": "latest-payment",
                "label": "آخر دفعة",
                "created_at": latest_payment.get("created_at"),
                "public_id": latest_payment.get("public_id") or "",
                "sort_order": 4,
            }
            if latest_payment is not None
            else None
        ),
        (
            {
                "key": "latest-collection",
                "label": "آخر تحصيل",
                "created_at": latest_collection.get("created_at"),
                "public_id": latest_collection.get("public_id") or "",
                "sort_order": 5,
            }
            if latest_collection is not None
            else None
        ),
    )

    totals_share = {
        "purchase_bills": _build_totals_share(
            provider_count=purchase_bills_count,
            total_count=int(Bill.objects.count()),
        ),
        "provider_returns": _build_totals_share(
            provider_count=provider_returns_count,
            total_count=int(ProviderReturn.objects.count()),
        ),
        "open_debts": _build_totals_share(
            provider_count=int(
                DebtRecord.objects
                .filter(provider_id=provider_id, status=DebtStatus.OPEN)
                .count()
            ),
            total_count=int(
                DebtRecord.objects
                .filter(provider__isnull=False, status=DebtStatus.OPEN)
                .count()
            ),
        ),
    }

    latest_purchase_details = None
    if latest_purchase is not None:
        purchase_items_qs = (
            BillItem.objects
            .filter(bill_id=latest_purchase.id)
            .select_related("product")
            .order_by("-id")
        )
        preview_items: list[dict[str, Any]] = []
        for row in purchase_items_qs[:3]:
            product_name = (
                str(getattr(row, "product_name_at_txn", "") or "")
                or str(getattr(getattr(row, "product", None), "name", "") or "")
                or "—"
            )
            product_name = product_name.strip() or "—"
            preview_items.append(
                {
                    "product_name": product_name,
                    "qty_primary": _as_decimal(getattr(row, "qty_primary", DEC0)),
                    "product_view_url": _safe_reverse("manager_product_edit", kwargs={"pk": int(row.product_id)})
                    if getattr(row, "product_id", None)
                    else "",
                }
            )
        latest_purchase_details = {
            "public_id": str(latest_purchase.public_id or ""),
            "created_at": _fmt_dt(latest_purchase.created_at),
            "items_count": int(purchase_items_qs.count()),
            "total_syp": _as_decimal(getattr(latest_purchase, "total_syp", DEC0)),
            "total_usd": _as_decimal(getattr(latest_purchase, "total_usd", DEC0)),
            "status_current_label": _bill_status_label(str(latest_purchase.status or "")),
            "payment_method_label": (
                latest_purchase.get_creation_payment_method_display() or ""
                if getattr(latest_purchase, "creation_payment_method", None)
                else ""
            ),
            "status_at_creation_label": (
                latest_purchase.get_creation_payment_status_display() or ""
                if getattr(latest_purchase, "creation_payment_status", None)
                else ""
            ),
            "products_preview": preview_items,
            "view_url": _safe_reverse("billing_bill_view", kwargs={"bill_id": latest_purchase.public_id}),
        }

    latest_return_details = None
    if latest_return is not None:
        latest_return_details = {
            "public_id": str(latest_return.public_id or ""),
            "created_at": _fmt_dt(latest_return.created_at),
            "items_count": int(latest_return.items.count()),
            "total_syp": _as_decimal(getattr(latest_return, "total_syp", DEC0)),
            "total_usd": _as_decimal(getattr(latest_return, "total_usd", DEC0)),
            "status_label": _return_status_label(str(latest_return.status or "")),
            "view_url": _safe_reverse("billing_return_view", kwargs={"ret_id": latest_return.public_id}),
        }

    latest_debt_details = None
    if latest_debt is not None:
        latest_debt_details = {
            "public_id": str(latest_debt.public_id or ""),
            "created_at": _fmt_dt(latest_debt.created_at),
            "direction_label": _debt_direction_label(str(latest_debt.direction or "")),
            "cause_label": _debt_cause_label(str(latest_debt.cause_type or "")),
            "total_syp": _as_decimal(getattr(latest_debt, "total_syp", DEC0)),
            "total_usd": _as_decimal(getattr(latest_debt, "total_usd", DEC0)),
            "status_label": _debt_status_label(str(getattr(latest_debt, "status", "") or "")),
            "reason_note": str(getattr(latest_debt, "note", "") or "").strip(),
            "view_url": _safe_reverse("debts_view_central_debt", kwargs={"debt_ref": latest_debt.public_id}),
        }

    latest_payment_details = None
    payment_central = (
        DebtSettlement.objects
        .filter(debt__provider_id=provider_id, debt__direction=DebtDirection.PAYABLE)
        .select_related("debt", "money_container")
        .order_by("-created_at", "-id")
        .first()
    )
    payment_legacy = (
        DebtorPayment.objects
        .filter(entry__provider_id=provider_id)
        .select_related("entry", "money_container")
        .order_by("-created_at", "-id")
        .first()
    )
    payment_kind, payment_row = _pick_latest_model_event(
        first=payment_central,
        second=payment_legacy,
        first_kind="central",
        second_kind="legacy",
    )
    if payment_row is not None:
        if payment_kind == "central":
            amount_syp = _as_decimal(getattr(payment_row, "applied_syp", DEC0))
            amount_usd = _as_decimal(getattr(payment_row, "applied_usd", DEC0))
            linked_ref = str(getattr(getattr(payment_row, "debt", None), "public_id", "") or "")
            money_container_name = str(getattr(getattr(payment_row, "money_container", None), "name", "") or "")
            view_url = _safe_reverse("debts_view_central_debt", kwargs={"debt_ref": linked_ref}) if linked_ref else ""
            source_label = "تسوية دين مركزي"
        else:
            amount = _as_decimal(getattr(payment_row, "amount", DEC0))
            currency_code = str(getattr(payment_row, "currency_code", "SYP") or "SYP").upper()
            amount_syp = amount if currency_code == "SYP" else DEC0
            amount_usd = amount if currency_code == "USD" else DEC0
            linked_ref = str(getattr(getattr(payment_row, "entry", None), "source_id", "") or "")
            money_container_name = str(getattr(getattr(payment_row, "money_container", None), "name", "") or "")
            entry_id = int(getattr(getattr(payment_row, "entry", None), "id", 0) or 0)
            view_url = _safe_reverse("debts_view_debt", kwargs={"direction": "debtor", "entry_id": entry_id}) if entry_id else ""
            source_label = "دفعة دين قديم"

        currency_label = "متعدد العملات"
        amount_value = amount_syp
        if amount_syp > DEC0 and amount_usd <= DEC0:
            currency_label = "SYP"
            amount_value = amount_syp
        elif amount_usd > DEC0 and amount_syp <= DEC0:
            currency_label = "USD"
            amount_value = amount_usd

        latest_payment_details = {
            "created_at": _fmt_dt(getattr(payment_row, "created_at", None)),
            "currency_label": currency_label,
            "amount_value": amount_value,
            "amount_syp": amount_syp,
            "amount_usd": amount_usd,
            "source_label": source_label,
            "money_container_name": money_container_name,
            "linked_reference": linked_ref,
            "view_url": view_url,
        }

    latest_collection_details = None
    collection_central = (
        DebtSettlement.objects
        .filter(debt__provider_id=provider_id, debt__direction=DebtDirection.RECEIVABLE)
        .select_related("debt", "money_container")
        .order_by("-created_at", "-id")
        .first()
    )
    collection_legacy = (
        CreditorReceipt.objects
        .filter(entry__provider_id=provider_id)
        .select_related("entry", "money_container")
        .order_by("-created_at", "-id")
        .first()
    )
    collection_kind, collection_row = _pick_latest_model_event(
        first=collection_central,
        second=collection_legacy,
        first_kind="central",
        second_kind="legacy",
    )
    if collection_row is not None:
        if collection_kind == "central":
            amount_syp = _as_decimal(getattr(collection_row, "applied_syp", DEC0))
            amount_usd = _as_decimal(getattr(collection_row, "applied_usd", DEC0))
            linked_ref = str(getattr(getattr(collection_row, "debt", None), "public_id", "") or "")
            money_container_name = str(getattr(getattr(collection_row, "money_container", None), "name", "") or "")
            view_url = _safe_reverse("debts_view_central_debt", kwargs={"debt_ref": linked_ref}) if linked_ref else ""
            source_label = "تسوية دين مركزي"
        else:
            amount = _as_decimal(getattr(collection_row, "amount", DEC0))
            currency_code = str(getattr(collection_row, "currency_code", "SYP") or "SYP").upper()
            amount_syp = amount if currency_code == "SYP" else DEC0
            amount_usd = amount if currency_code == "USD" else DEC0
            linked_ref = str(getattr(getattr(collection_row, "entry", None), "source_id", "") or "")
            money_container_name = str(getattr(getattr(collection_row, "money_container", None), "name", "") or "")
            entry_id = int(getattr(getattr(collection_row, "entry", None), "id", 0) or 0)
            view_url = _safe_reverse("debts_view_debt", kwargs={"direction": "creditor", "entry_id": entry_id}) if entry_id else ""
            source_label = "تحصيل دين قديم"

        currency_label = "متعدد العملات"
        amount_value = amount_syp
        if amount_syp > DEC0 and amount_usd <= DEC0:
            currency_label = "SYP"
            amount_value = amount_syp
        elif amount_usd > DEC0 and amount_syp <= DEC0:
            currency_label = "USD"
            amount_value = amount_usd

        latest_collection_details = {
            "created_at": _fmt_dt(getattr(collection_row, "created_at", None)),
            "currency_label": currency_label,
            "amount_value": amount_value,
            "amount_syp": amount_syp,
            "amount_usd": amount_usd,
            "source_label": source_label,
            "money_container_name": money_container_name,
            "linked_reference": linked_ref,
            "view_url": view_url,
        }

    top_products_rows = list(
        BillItem.objects
        .filter(bill__provider_id=provider_id)
        .values("product_id", "product_name_at_txn", "product__name")
        .annotate(total_qty=Sum("qty_primary"), lines_count=Count("id"))
        .order_by("-total_qty", "-lines_count", "product_id")
    )
    top_products_items: list[dict[str, Any]] = []
    top_products_segments: list[dict[str, Any]] = []
    top_products_palette = ["#0f766e", "#2563eb", "#ea580c", "#16a34a", "#1d4ed8"]
    total_products_qty = sum((_as_decimal(row.get("total_qty")) for row in top_products_rows), DEC0)
    top5_qty = DEC0
    for idx, row in enumerate(top_products_rows[:5]):
        qty = _as_decimal(row.get("total_qty"))
        top5_qty += qty
        product_id = int(row.get("product_id") or 0)
        product_name = str(row.get("product_name_at_txn") or row.get("product__name") or "—").strip() or "—"
        top_products_items.append(
            {
                "product_name": product_name,
                "product_code": (f"#{product_id}" if product_id > 0 else "—"),
                "total_qty": qty,
                "view_url": _safe_reverse("manager_product_edit", kwargs={"pk": product_id}) if product_id > 0 else "",
            }
        )
        product_percent = DEC0
        if total_products_qty > DEC0:
            product_percent = ((qty * Decimal("100.00")) / total_products_qty).quantize(Decimal("0.01"))
        top_products_segments.append(
            {
                "label": product_name,
                "percent": product_percent,
                "color": top_products_palette[idx % len(top_products_palette)],
            }
        )
    if total_products_qty > DEC0 and (total_products_qty - top5_qty) > DEC0:
        others_percent = (((total_products_qty - top5_qty) * Decimal("100.00")) / total_products_qty).quantize(Decimal("0.01"))
        top_products_segments.append(
            {
                "label": "أصناف أخرى",
                "percent": others_percent,
                "color": "#9ca3af",
            }
        )

    weekday_labels = {
        0: "الاثنين",
        1: "الثلاثاء",
        2: "الأربعاء",
        3: "الخميس",
        4: "الجمعة",
        5: "السبت",
        6: "الأحد",
    }
    time_buckets = [
        ("ليلًا (00-05)", range(0, 6)),
        ("صباحًا (06-09)", range(6, 10)),
        ("قبل الظهر (10-13)", range(10, 14)),
        ("بعد الظهر (14-17)", range(14, 18)),
        ("مساءً (18-21)", range(18, 22)),
        ("آخر الليل (22-23)", range(22, 24)),
    ]
    visit_sources = [
        ("فواتير الشراء", list(purchase_qs.values_list("created_at", flat=True))),
        ("فواتير الإرجاع", list(returns_qs.values_list("created_at", flat=True))),
        (
            "سجلات الديون",
            list(DebtRecord.objects.filter(provider_id=provider_id).values_list("created_at", flat=True)),
        ),
        (
            "تسويات الديون",
            list(DebtSettlement.objects.filter(debt__provider_id=provider_id).values_list("created_at", flat=True)),
        ),
    ]
    visit_timestamps: list[Any] = []
    visit_sources_used: list[str] = []
    for label, rows in visit_sources:
        valid_rows = [x for x in rows if x is not None]
        if valid_rows:
            visit_sources_used.append(label)
            visit_timestamps.extend(valid_rows)

    day_counter: Counter[int] = Counter()
    time_counter: Counter[str] = Counter()
    for value in visit_timestamps:
        try:
            value = timezone.localtime(value)
        except Exception:
            pass
        day_counter[int(value.weekday())] += 1
        hour = int(value.hour)
        for bucket_label, hour_range in time_buckets:
            if hour in hour_range:
                time_counter[bucket_label] += 1
                break

    visit_days_rows = [{"label": weekday_labels[idx], "count": int(day_counter.get(idx, 0))} for idx in range(7)]
    visit_times_rows = [{"label": bucket_label, "count": int(time_counter.get(bucket_label, 0))} for bucket_label, _ in time_buckets]

    top_weekday = "لا توجد بيانات كافية"
    if day_counter:
        day_index = max(day_counter.items(), key=lambda item: (item[1], -item[0]))[0]
        top_weekday = weekday_labels.get(day_index, "لا توجد بيانات كافية")
    top_time = "لا توجد بيانات كافية"
    if time_counter:
        top_time = max(time_counter.items(), key=lambda item: item[1])[0]

    analysis_panel = {
        "selected_default": "total-purchase-bills",
        "chooser_groups": [
            {
                "key": "totals",
                "label": "الإجماليات",
                "options": [
                    {"key": "total-purchase-bills", "label": "عدد فواتير الشراء"},
                    {"key": "total-provider-returns", "label": "عدد فواتير الإرجاع"},
                    {"key": "total-open-debts", "label": "عدد الديون"},
                ],
            },
            {
                "key": "latest",
                "label": "آخر العمليات",
                "options": [
                    {"key": "latest-purchase", "label": "آخر فاتورة شراء"},
                    {"key": "latest-return", "label": "آخر إرجاع"},
                    {"key": "latest-debt", "label": "آخر دين"},
                    {"key": "latest-payment", "label": "آخر دفعة"},
                    {"key": "latest-collection", "label": "آخر تحصيل"},
                ],
            },
            {
                "key": "extras",
                "label": "إضافات",
                "options": [
                    {"key": "top-products", "label": "الأصناف الأكثر شراءً"},
                    {"key": "visit-frequency", "label": "تكرار زيارات المورد"},
                ],
            },
        ],
        "activities": {
            "total-purchase-bills": {
                "details_kind": "totals",
                "visual_kind": "donut-two",
                "details": {**totals_share["purchase_bills"], "metric_note": ""},
                "visual": {
                    "segments": [
                        {
                            "label": "هذا المورد",
                            "percent": totals_share["purchase_bills"]["provider_percent"],
                            "color": "#0f766e",
                        },
                        {
                            "label": "باقي الموردين",
                            "percent": totals_share["purchase_bills"]["others_percent"],
                            "color": "#9ca3af",
                        },
                    ],
                },
            },
            "total-provider-returns": {
                "details_kind": "totals",
                "visual_kind": "donut-two",
                "details": {**totals_share["provider_returns"], "metric_note": ""},
                "visual": {
                    "segments": [
                        {
                            "label": "هذا المورد",
                            "percent": totals_share["provider_returns"]["provider_percent"],
                            "color": "#2563eb",
                        },
                        {
                            "label": "باقي الموردين",
                            "percent": totals_share["provider_returns"]["others_percent"],
                            "color": "#9ca3af",
                        },
                    ],
                },
            },
            "total-open-debts": {
                "details_kind": "totals",
                "visual_kind": "donut-two",
                "details": {
                    **totals_share["open_debts"],
                    "metric_note": "تم احتساب الديون المفتوحة فقط لضمان اتساق المؤشر.",
                },
                "visual": {
                    "segments": [
                        {
                            "label": "هذا المورد",
                            "percent": totals_share["open_debts"]["provider_percent"],
                            "color": "#ea580c",
                        },
                        {
                            "label": "باقي الموردين",
                            "percent": totals_share["open_debts"]["others_percent"],
                            "color": "#9ca3af",
                        },
                    ],
                },
            },
            "latest-purchase": {
                "details_kind": "latest-purchase",
                "visual_kind": "none",
                "details": latest_purchase_details,
                "visual": {},
            },
            "latest-return": {
                "details_kind": "latest-return",
                "visual_kind": "none",
                "details": latest_return_details,
                "visual": {},
            },
            "latest-debt": {
                "details_kind": "latest-debt",
                "visual_kind": "none",
                "details": latest_debt_details,
                "visual": {},
            },
            "latest-payment": {
                "details_kind": "latest-payment",
                "visual_kind": "none",
                "details": latest_payment_details,
                "visual": {},
            },
            "latest-collection": {
                "details_kind": "latest-collection",
                "visual_kind": "none",
                "details": latest_collection_details,
                "visual": {},
            },
            "top-products": {
                "details_kind": "top-products",
                "visual_kind": "donut-multi",
                "details": {"items": top_products_items},
                "visual": {"segments": top_products_segments},
            },
            "visit-frequency": {
                "details_kind": "visit-frequency",
                "visual_kind": "bars-toggle",
                "details": {
                    "top_weekday": top_weekday,
                    "top_time": top_time,
                    "operations_count": len(visit_timestamps),
                    "note": "القيم تقريبية حسب العمليات المسجلة",
                    "sources": visit_sources_used,
                },
                "visual": {
                    "days": visit_days_rows,
                    "times": visit_times_rows,
                },
            },
        },
    }

    return {
        "purchase_bills_count": purchase_bills_count,
        "provider_returns_count": provider_returns_count,
        "totals_share": totals_share,
        "analysis_panel": analysis_panel,
        "activity_summary": {
            "latest_activity": latest_activity,
            "latest_activity_label": (
                latest_activity.get("label")
                if latest_activity is not None
                else "لا توجد بيانات كافية"
            ),
            "latest_activity_at": (
                latest_activity.get("created_at")
                if latest_activity is not None
                else None
            ),
            "account_state_label": account_state_label,
        },
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
                "status_label": _build_currency_net_label(net=syp_net),
                "has_open_obligations": (syp_open_payable_count + syp_open_receivable_count) > 0,
            },
            "usd": {
                "payable": usd_payable,
                "receivable": usd_receivable,
                "net": usd_net,
                "open_payable_count": usd_open_payable_count,
                "open_receivable_count": usd_open_receivable_count,
                "status_label": _build_currency_net_label(net=usd_net),
                "has_open_obligations": (usd_open_payable_count + usd_open_receivable_count) > 0,
            },
            "has_open_obligations": has_open_obligations,
            "balanced_with_open_obligations": (syp_net == DEC0 and usd_net == DEC0 and has_open_obligations),
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

