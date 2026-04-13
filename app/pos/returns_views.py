# app/pos/returns_views.py
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.decorators import role_required
from accounts.models import AccountProfile
from catalog.models import Product, UnitType
from inventory.models import DEC0, q3
from stock.models import ProductContainer
from financials import services as FinSV

from .models import SalesBill, SalesReturn
from . import services_returns as SV
from .views import _resolve_sales_bill_from_ref

logger = logging.getLogger(__name__)


def _dec(x) -> Decimal:
    try:
        return Decimal(str(x or "0"))
    except Exception:
        return Decimal("0")


def _build_return_rows(*, bill: SalesBill, products: dict[int, Product]) -> list[dict[str, Any]]:
    unit_label_map = dict(UnitType.choices)
    returned_map = SV.returned_qty_by_sale_row(bill_id=bill.id)

    rows = []
    for r in bill.rows.all().order_by("id"):
        product = products.get(int(r.product_id))
        if product is None:
            continue

        conv = _dec(getattr(r, "conv_factor_at_txn", None) or "1")
        if conv <= 0:
            conv = Decimal("1")
        uom_index = int(r.uom_index or 1)
        if not (getattr(r, "unit_2_label_at_txn", "") or "").strip() and uom_index == 2:
            conv = Decimal("1")
            uom_index = 1

        sold_qty = _dec(r.qty)
        sold_primary = q3(sold_qty * conv) if uom_index == 2 else q3(sold_qty)

        already_primary = returned_map.get(int(r.id), DEC0)
        remaining_primary = q3(sold_primary - already_primary)
        if remaining_primary < DEC0:
            remaining_primary = DEC0

        to_display = lambda q: q3(q / conv) if uom_index == 2 else q3(q)

        rows.append(
            {
                "row_id": r.id,
                "product_name": (getattr(r, "product_name_at_txn", "") or r.product_name),
                "currency": (r.sale_currency or "SYP").upper(),
                "uom_label": (
                    (getattr(r, "unit_2_label_at_txn", "") or "").strip()
                    if uom_index == 2
                    else (getattr(r, "unit_1_label_at_txn", "") or "").strip()
                )
                or (
                    unit_label_map.get(product.unit_secondary, product.unit_secondary)
                    if uom_index == 2 and product.unit_secondary
                    else unit_label_map.get(product.unit_primary, product.unit_primary)
                ),
                "sold_qty": to_display(sold_primary),
                "already_returned": to_display(already_primary),
                "remaining_qty": to_display(remaining_primary),
                "remaining_primary": remaining_primary,
                "ret_qty_raw": "",
                "reason_raw": "",
            }
        )

    return rows


@role_required(AccountProfile.Role.MANAGER)
def pos_manager_sale_return_wizard(request: HttpRequest, bill_id: str) -> HttpResponse:
    bill_ref = _resolve_sales_bill_from_ref(ref=bill_id)
    if bill_ref is None:
        return HttpResponse(status=404)
    bill = get_object_or_404(
        SalesBill.objects.select_related("cashier", "customer").prefetch_related("rows"),
        pk=bill_ref.id,
    )

    if bill.parked or not bill.finalized:
        return render(
            request,
            "pos/manager_sale_return_wizard.html",
            {"bill": bill, "error_msg": "Cannot return items from a non-finalized bill.", "rows": []},
        )

    product_ids = {int(r.product_id) for r in bill.rows.all() if r.product_id}
    products = {p.id: p for p in Product.objects.filter(id__in=product_ids)}

    rows = _build_return_rows(bill=bill, products=products)
    containers = ProductContainer.objects.filter(is_active=True).order_by("sort_order", "id")
    default_container = containers.filter(is_store=True).first() or containers.first()

    selected_container_id = ""
    if request.method == "POST":
        selected_container_id = (request.POST.get("stock_container_id") or "").strip()
        for row in rows:
            rid = row["row_id"]
            row["ret_qty_raw"] = (request.POST.get(f"ret_qty_{rid}") or "").strip()
            row["reason_raw"] = (request.POST.get(f"ret_reason_{rid}") or "").strip()

        items = []
        for row in rows:
            qty_raw = _dec(row.get("ret_qty_raw"))
            if qty_raw <= DEC0:
                continue
            items.append(
                {
                    "sale_row_id": row["row_id"],
                    "qty": qty_raw,
                    "reason": row.get("reason_raw") or "",
                }
            )

        try:
            if not selected_container_id:
                raise ValueError("Stock container is required.")

            ret = SV.create_sales_return_draft(
                actor=request.user,
                sale_bill_id=bill.id,
                stock_container_id=int(selected_container_id),
                items=items,
            )
            return redirect("pos:pos_manager_sale_return_settle", return_id=ret.id)
        except ValueError as ve:
            error_msg = str(ve)
        except Exception:
            error_msg = "Failed to create sales return."
    else:
        error_msg = ""

    return render(
        request,
        "pos/manager_sale_return_wizard.html",
        {
            "bill": bill,
            "rows": rows,
            "containers": containers,
            "default_container": default_container,
            "selected_container_id": selected_container_id,
            "error_msg": error_msg,
        },
    )


def _build_settle_context(
    ret: SalesReturn,
    *,
    user,
    error_msg: str = "",
) -> dict[str, Any]:
    rows = list(ret.rows.all().order_by("id"))
    total_syp = DEC0
    total_usd = DEC0
    for r in rows:
        if (r.currency_code or "SYP").upper() == "USD":
            total_usd = q3(total_usd + q3(_dec(r.line_total)))
        else:
            total_syp = q3(total_syp + q3(_dec(r.line_total)))

    money_containers = (
        FinSV.money_containers_for_user_qs(
            user=user,
            feature_code=("pos_returns", "pos_sales"),
        )
        .order_by("id")
    )

    return {
        "ret": ret,
        "bill": ret.sale_bill,
        "rows": rows,
        "total_syp": total_syp,
        "total_usd": total_usd,
        "money_containers": money_containers,
        "error_msg": error_msg,
    }


@role_required(AccountProfile.Role.MANAGER)
def pos_manager_sale_return_settle(request: HttpRequest, return_id: int) -> HttpResponse:
    ret = (
        SalesReturn.objects
        .select_related("sale_bill", "customer", "stock_container")
        .prefetch_related("rows__product")
        .get(pk=return_id)
    )

    if ret.status != SalesReturn.Status.DRAFT:
        bill_ref = (getattr(ret.sale_bill, "public_id", "") or "").strip()
        if not bill_ref:
            return HttpResponse(status=404)
        return redirect("pos:pos_manager_bill_detail", bill_id=bill_ref)

    ctx = _build_settle_context(ret, user=request.user)
    return render(request, "pos/manager_sale_return_settle.html", ctx)


@role_required(AccountProfile.Role.MANAGER)
@require_POST
def pos_manager_sale_return_post(request: HttpRequest, return_id: int) -> HttpResponse:
    try:
        ret = SalesReturn.objects.select_related("sale_bill").get(pk=return_id)
        if ret.status != SalesReturn.Status.DRAFT:
            return HttpResponseBadRequest("Return is not in draft state.")

        settle_mode = (request.POST.get("settle_mode") or "cash").strip()
        money_container_id_raw = (request.POST.get("money_container_id") or "").strip()
        money_container_id = int(money_container_id_raw) if money_container_id_raw else None

        ret = SV.post_sales_return(
            actor=request.user,
            return_id=return_id,
            settle_mode=settle_mode,
            money_container_id=money_container_id,
        )
        bill_ref = (getattr(ret.sale_bill, "public_id", "") or "").strip()
        if not bill_ref:
            return HttpResponse(status=404)
        return redirect("pos:pos_manager_bill_detail", bill_id=bill_ref)
    except (ValueError, ValidationError) as e:
        return HttpResponseBadRequest(str(e))
    except Exception:
        logger.exception("pos_manager_sale_return_post failed")
        return HttpResponseBadRequest("Failed to post sales return.")
