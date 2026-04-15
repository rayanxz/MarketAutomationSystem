# app/inventory/views.py
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, Any, List

from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from accounts.decorators import role_required
from accounts.models import AccountProfile
from catalog.models import ProductCollection, ProductSet, Product
from billing.models import Bill, ProviderReturn , BillItem
from inventory.models import ProductMovement
from stock.models import ProductContainer   # <- NEW
from core.date_filters import parse_filter_date


PAGE_SIZE = 50


def _parse_date(s: str):
    d = parse_filter_date(s)
    if d is not None:
        return datetime(d.year, d.month, d.day)
    return None


@role_required(AccountProfile.Role.MANAGER)
def manager_product_movements(request: HttpRequest) -> HttpResponse:
    """
    صفحة حركة المواد (مدير).
    فلاتر:
      - date_from, date_to
      - collection_id
      - set_id
      - product_id
      - movement_type (grouped)
      - container (stock.ProductContainer)
    """
    qs = ProductMovement.objects.select_related(
        "product",
        "product__set",
        "product__set__collection",
        "actor",
        "container",   # <- NEW
    )

    # -------- filters from GET --------
    date_from_raw = request.GET.get("date_from", "")
    date_to_raw = request.GET.get("date_to", "")

    collection_id = request.GET.get("collection_id") or ""
    set_id = request.GET.get("set_id") or ""
    product_id = request.GET.get("product_id") or ""
    movement_group = (request.GET.get("movement_type") or "all").strip()

    container_code = (request.GET.get("container") or "").strip()  # <- NEW

    # dates
    dt_from = _parse_date(date_from_raw)
    dt_to = _parse_date(date_to_raw)

    if dt_from:
        qs = qs.filter(created_at__gte=dt_from)
    if dt_to:
        qs = qs.filter(created_at__lt=dt_to + timedelta(days=1))

    # hierarchy
    if product_id:
        try:
          qs = qs.filter(product_id=int(product_id))
        except ValueError:
          pass
    elif set_id:
        try:
          qs = qs.filter(product__set_id=int(set_id))
        except ValueError:
          pass
    elif collection_id:
        try:
          qs = qs.filter(product__set__collection_id=int(collection_id))
        except ValueError:
          pass

    # container filter
    if container_code:
        qs = qs.filter(container__code=container_code)

    # type group (buy / sale / customer returns / provider returns / all)
    if movement_group == "buy":
        qs = qs.filter(
            movement_type__in=[
                ProductMovement.MovementType.PURCHASE,
                ProductMovement.MovementType.PURCHASE_REVERSAL,
            ]
        )
    elif movement_group == "sale":
        qs = qs.filter(
            movement_type__in=[
                ProductMovement.MovementType.SALE,
                ProductMovement.MovementType.SALE_RETURN,
            ]
        )
    elif movement_group == "cust_ret":
        qs = qs.filter(
            movement_type=ProductMovement.MovementType.SALE_RETURN
        )
    elif movement_group == "prov_ret":
        qs = qs.filter(
            movement_type__in=[
                ProductMovement.MovementType.PROVIDER_RETURN,
                ProductMovement.MovementType.PROVIDER_RETURN_REVERSAL,
            ]
        )
    # "all" => no filter

    qs = qs.order_by("-created_at", "-id")

    # -------- pagination --------
    page_num = request.GET.get("page") or "1"
    try:
        page_num = int(page_num)
    except ValueError:
        page_num = 1

    paginator = Paginator(qs, PAGE_SIZE)
    page_obj = paginator.get_page(page_num)
    movements = list(page_obj.object_list)

    # -------- preload related docs for other party name --------
    bill_ids = set()
    pret_ids = set()
    bill_item_ids = set()


    for mv in movements:
        if mv.source_app == "billing" and mv.source_model == "Bill":
            try:
                bill_ids.add(int(mv.source_id))
            except (TypeError, ValueError):
                pass
        elif mv.source_app == "billing" and mv.source_model == "ProviderReturn":
            try:
                pret_ids.add(int(mv.source_id))
            except (TypeError, ValueError):
                pass
        elif mv.source_app == "billing" and mv.source_model == "BillItem":
            try:
                bill_item_ids.add(int(mv.source_id))
            except (TypeError, ValueError):
                pass



    bills_map: Dict[int, Bill] = {}
    if bill_ids:
        for b in Bill.objects.select_related("provider").filter(id__in=bill_ids):
            bills_map[b.id] = b

    rets_map: Dict[int, ProviderReturn] = {}
    if pret_ids:
        for r in ProviderReturn.objects.select_related("provider").filter(id__in=pret_ids):
            rets_map[r.id] = r

    bill_item_provider_map: Dict[int, str] = {}
    if bill_item_ids:
        qs_items = (
            BillItem.objects
            .select_related("bill__provider")
            .filter(id__in=bill_item_ids)
        )
        for it in qs_items:
            if it.bill and it.bill.provider:
                bill_item_provider_map[it.id] = it.bill.provider.name


    rows: List[Dict[str, Any]] = []
    for mv in movements:
        prod = mv.product
        qty = mv.qty_primary
        direction = "up" if qty > 0 else "down" if qty < 0 else "none"

        other_party_type = ""
        other_party_name = ""

        if mv.source_app == "billing" and mv.source_model == "Bill":
            other_party_type = "مورد"
            try:
                b = bills_map.get(int(mv.source_id))
            except (TypeError, ValueError):
                b = None
            if b and b.provider:
                other_party_name = b.provider.name
        elif mv.source_app == "billing" and mv.source_model == "ProviderReturn":
            other_party_type = "مورد"
            try:
                r = rets_map.get(int(mv.source_id))
            except (TypeError, ValueError):
                r = None
            if r and r.provider:
                other_party_name = r.provider.name
        elif mv.source_app == "billing" and mv.source_model == "BillItem":
            other_party_type = "مورد"
            try:
                other_party_name = bill_item_provider_map.get(int(mv.source_id), "")
            except (TypeError, ValueError):
                other_party_name = ""

        rows.append(
            {
                "movement": mv,
                "date": mv.created_at,
                "product": prod,
                "collection": prod.set.collection if prod and prod.set_id else None,
                "set": prod.set if prod else None,
                "qty": qty,
                "qty_abs": abs(qty),
                "direction": direction,
                "cost": getattr(mv, "unit_cost_at_txn", None) or mv.unit_cost,
                "price": getattr(mv, "sale_unit_price_at_txn", None),
                "product_name": (getattr(mv, "product_name_at_txn", "") or getattr(prod, "name", "") or ""),
                "movement_type": mv.movement_type,
                "movement_type_label": mv.get_movement_type_display(),
                "other_party_type": other_party_type,
                "other_party_name": other_party_name,
                "container": mv.container,  # <- NEW
                "product_is_active": bool(getattr(prod, "is_active", True)) if prod else True,
            }
        )

    collections = ProductCollection.objects.order_by("name")
    sets = ProductSet.objects.select_related("collection").order_by("collection__name", "name")
    containers = ProductContainer.objects.filter(is_active=True).order_by("sort_order", "name")

    ctx = {
        "rows": rows,
        "page_obj": page_obj,
        "paginator": paginator,
        "collections": collections,
        "sets": sets,
        "containers": containers,  # <- NEW
        "filters": {
            "date_from": date_from_raw,
            "date_to": date_to_raw,
            "collection_id": collection_id,
            "set_id": set_id,
            "product_id": product_id,
            "movement_type": movement_group,
            "container": container_code,   # <- NEW
        },
        "movement_type_choices": [
            ("all", "الكل"),
            ("buy", "فواتير شراء"),
            ("sale", "فواتير مبيع"),
            ("cust_ret", "مرتجعات الزبائن"),
            ("prov_ret", "مرتجعات الموردين"),
        ],
    }
    return render(request, "manager/product_movements.html", ctx)
