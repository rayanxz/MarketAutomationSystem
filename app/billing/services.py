# app/billing/services.py
from __future__ import annotations
import contextlib
from decimal import Decimal
from typing import Iterable, Dict, Any
from django.db import transaction
from django.shortcuts import get_object_or_404
from billing.models import Provider, Bill, BillItem
from catalog.models import Product

DEC0 = Decimal("0")
DEC3 = Decimal("0.001")
DEC4 = Decimal("0.0001")

def q3(x: Decimal) -> Decimal: return (x or DEC0).quantize(Decimal("0.001"))
def q4(x: Decimal) -> Decimal: return (x or DEC0).quantize(Decimal("0.0001"))

@transaction.atomic
def create_bill(*, provider_id: int, serial: int | None, status: str,
                paid_amount: Decimal, items: Iterable[Dict[str, Any]],
                update_product_defaults: bool = False) -> Bill:
    provider = get_object_or_404(Provider.objects.select_for_update(), pk=provider_id)
    bill = Bill(provider=provider, status=status, paid_amount=q3(paid_amount), total=DEC0)
    if serial is not None:
        bill.serial = serial
    bill.save()

    grand = DEC0
    # lock all involved products once for performance
    prod_ids = [int(it["product_id"]) for it in items]
    products = {p.id: p for p in Product.objects.select_for_update().filter(id__in=prod_ids)}

    for idx, row in enumerate(items, start=1):
        pid = int(row["product_id"])
        product = products.get(pid) or get_object_or_404(Product.objects.select_for_update(), pk=pid)

        unit_idx = 2 if int(row.get("unit_index") or 1) == 2 else 1
        qty_raw = Decimal(str(row.get("qty_raw") or "0"))
        if qty_raw <= 0:
            raise ValueError(f"qty must be > 0 at row {idx}")

        cost_u1  = q4(Decimal(str(row.get("cost") or "0")))
        price_u1 = q4(Decimal(str(row.get("price") or "0")))
        total_override = row.get("total_cost")
        total_override = Decimal(str(total_override)) if total_override not in (None, "") else None

        # convert to primary units
        qty_primary = qty_raw
        cf = getattr(product, "conversion_factor", None)
        if unit_idx == 2 and cf:
            qty_primary = qty_raw * Decimal(str(cf))
        qty_primary = q3(qty_primary)

        line_total = q3(total_override) if (total_override and total_override > 0) else q3(cost_u1 * qty_primary)

        BillItem.objects.create(
            bill=bill, product=product,
            unit_index=unit_idx, qty_primary=qty_primary,
            cost=cost_u1, price=price_u1, line_total=line_total
        )

        # stock only
        product.stock_qty = (product.stock_qty or DEC0) + qty_primary
        update_fields = ["stock_qty"]
        if hasattr(product, "updated_at"):
            from django.utils import timezone
            product.updated_at = timezone.now()
            update_fields.append("updated_at")
        if update_product_defaults:
            with contextlib.suppress(AttributeError):
                product.cost = cost_u1; update_fields.append("cost")
            with contextlib.suppress(AttributeError):
                product.price = price_u1; update_fields.append("price")
        product.save(update_fields=list(dict.fromkeys(update_fields)))
        grand += line_total

    bill.total = q3(grand)
    bill.save(update_fields=["total"])
    return bill

@transaction.atomic
def delete_bill(bill_id: int) -> None:
    bill = (
        Bill.objects.select_for_update()
        .prefetch_related("items")
        .get(pk=bill_id)
    )
    prod_ids = list(bill.items.values_list("product_id", flat=True))
    products = {p.id: p for p in Product.objects.select_for_update().filter(id__in=prod_ids)}
    for it in bill.items.all():
        p = products.get(it.product_id)
        if not p: continue
        p.stock_qty = (p.stock_qty or DEC0) - (it.qty_primary or DEC0)
        fields = ["stock_qty"]
        if hasattr(p, "updated_at"):
            from django.utils import timezone
            p.updated_at = timezone.now()
            fields.append("updated_at")
        p.save(update_fields=fields)
    bill.delete()

@transaction.atomic
def pay_full(bill_id: int) -> Bill:
    bill = Bill.objects.select_for_update().get(pk=bill_id)
    if bill.remaining <= 0:
        return bill
    bill.paid_amount = bill.total
    bill.status = Bill.Status.PAID
    bill.save(update_fields=["paid_amount", "status"])
    return bill

@transaction.atomic
def pay_partial(bill_id: int, amount: Decimal) -> Bill:
    if amount <= 0:
        raise ValueError("amount must be positive")
    bill = Bill.objects.select_for_update().get(pk=bill_id)
    if amount > bill.remaining:
        raise ValueError("amount exceeds remaining")
    bill.paid_amount = (bill.paid_amount or DEC0) + amount
    bill.status = (Bill.Status.PAID if bill.paid_amount >= bill.total else Bill.Status.PARTIAL)
    bill.save(update_fields=["paid_amount", "status"])
    return bill
