# app/billing/services.py
from __future__ import annotations

import contextlib
from decimal import Decimal
from typing import Iterable, Dict, Any

from django.db import transaction
from django.shortcuts import get_object_or_404

from billing.models import Provider, Bill, BillItem, ProviderReturn, ProviderReturnItem
from catalog.models import Product
from ledger import services as LSV


# ====== Decimals / helpers ======
DEC0 = Decimal("0")
DEC3 = Decimal("0.001")
DEC4 = Decimal("0.0001")


def q3(x: Decimal) -> Decimal:
    return (x or DEC0).quantize(DEC3)


def q4(x: Decimal) -> Decimal:
    return (x or DEC0).quantize(DEC4)


def minor3(x: Decimal) -> int:
    """Convert a Decimal (quantized to 3dp) to integer minor units (x * 1000)."""
    return LSV.to_minor(q3(x or DEC0), 3)


def _resolve_paid_amount(status: str, intended_paid: Decimal, total: Decimal) -> Decimal:
    """
    Make the server authoritative:
      - "paid"   -> full total
      - "unpaid" -> 0
      - "partial"/other -> clamp intended to [0, total]
    """
    s = (status or "").lower().strip()
    total = q3(total)
    intended = q3(intended_paid if intended_paid is not None else DEC0)
    if s == "paid":
        return total
    if s == "unpaid":
        return DEC0
    # partial / unknown
    if intended < DEC0:
        intended = DEC0
    if intended > total:
        intended = total
    return intended


# =======================================================================
# Bills
# =======================================================================

@transaction.atomic
def create_bill(
    *,
    actor,
    provider_id: int,
    serial: int | None,
    status: str,
    paid_amount: Decimal,
    items: Iterable[Dict[str, Any]],
    update_product_defaults: bool = False,
) -> Bill:
    """
    Create a bill, increase stock, and post purchase to the ledger:
      Dr INVENTORY (total) / Cr SAFE (paid) / Cr PROVIDER_PAYABLE (remaining)

    We also capture creation-time payment snapshot:
      - bill.initial_status
      - bill.initial_paid_amount

    Payment logic is server-side authoritative (see _resolve_paid_amount).
    """
    provider = get_object_or_404(Provider.objects.select_for_update(), pk=provider_id)
    intended_paid = q3(paid_amount)

    # Create an empty shell to assign an auto/explicit serial first
    bill = Bill(provider=provider, status=status, paid_amount=DEC0, total=DEC0)
    if serial is not None:
        bill.serial = serial
    bill.save()  # persist early to get PK/serial

    grand = DEC0

    # Lock involved products once
    prod_ids = [int(it["product_id"]) for it in items]
    products = {p.id: p for p in Product.objects.select_for_update().filter(id__in=prod_ids)}

    for idx, row in enumerate(items, start=1):
        pid = int(row["product_id"])
        product = products.get(pid) or get_object_or_404(Product.objects.select_for_update(), pk=pid)

        unit_idx = 2 if int(row.get("unit_index") or 1) == 2 else 1
        qty_raw = Decimal(str(row.get("qty_raw") or "0"))
        if qty_raw <= 0:
            raise ValueError(f"qty must be > 0 at row {idx}")

        cost_u1 = q4(Decimal(str(row.get("cost") or "0")))
        price_u1 = q4(Decimal(str(row.get("price") or "0")))
        total_override_raw = row.get("total_cost")
        total_override = Decimal(str(total_override_raw)) if total_override_raw not in (None, "") else None

        # Convert quantity to primary units if the selected unit was u2
        qty_primary = qty_raw
        cf = getattr(product, "conversion_factor", None)
        if unit_idx == 2 and cf:
            qty_primary = qty_raw * Decimal(str(cf))
        qty_primary = q3(qty_primary)

        # If a total override exists and > 0 use it, otherwise qty * cost_u1
        line_total = q3(total_override) if (total_override and total_override > 0) else q3(cost_u1 * qty_primary)

        BillItem.objects.create(
            bill=bill,
            product=product,
            unit_index=unit_idx,
            qty_primary=qty_primary,
            cost=cost_u1,
            price=price_u1,
            line_total=line_total,
        )

        # Stock increase (purchases)
        product.stock_qty = (product.stock_qty or DEC0) + qty_primary
        update_fields = ["stock_qty"]

        if hasattr(product, "updated_at"):
            from django.utils import timezone
            product.updated_at = timezone.now()
            update_fields.append("updated_at")

        if update_product_defaults:
            with contextlib.suppress(AttributeError):
                product.cost = cost_u1
                update_fields.append("cost")
            with contextlib.suppress(AttributeError):
                product.price = price_u1
                update_fields.append("price")

        product.save(update_fields=list(dict.fromkeys(update_fields)))
        grand += line_total

    # Totals and payment state (authoritative)
    bill.total = q3(grand)
    final_paid = _resolve_paid_amount(status, intended_paid, bill.total)
    bill.paid_amount = final_paid

    if bill.paid_amount >= bill.total:
        bill.status = Bill.Status.PAID
    elif bill.paid_amount > DEC0:
        bill.status = Bill.Status.PARTIAL
    else:
        bill.status = Bill.Status.UNPAID

    # Creation-time snapshot
    bill.initial_status = bill.status
    bill.initial_paid_amount = bill.paid_amount

    bill.save(update_fields=[
        "total", "paid_amount", "status",
        "initial_status", "initial_paid_amount"
    ])

    # Ledger: Provider purchase via SAFE
    total_minor = minor3(bill.total)
    paid_minor = minor3(bill.paid_amount or DEC0)
    LSV.post_purchase(
        actor=actor,
        total_minor=total_minor,
        paid_minor=paid_minor,
        provider_id=provider.id,
        source=("billing", "Bill", bill.id),
    )
    return bill


@transaction.atomic
def delete_bill(*, actor, bill_id: int) -> None:
    """
    Reverse stock and post reversal:
      Cr INVENTORY / Dr SAFE (paid) / Dr PROVIDER_PAYABLE (remaining)
    """
    bill = Bill.objects.select_for_update().prefetch_related("items").get(pk=bill_id)

    # Lock related products once
    prod_ids = list(bill.items.values_list("product_id", flat=True))
    products = {p.id: p for p in Product.objects.select_for_update().filter(id__in=prod_ids)}

    for it in bill.items.all():
        p = products.get(it.product_id)
        if not p:
            continue
        p.stock_qty = (p.stock_qty or DEC0) - (it.qty_primary or DEC0)
        fields = ["stock_qty"]
        if hasattr(p, "updated_at"):
            from django.utils import timezone
            p.updated_at = timezone.now()
            fields.append("updated_at")
        p.save(update_fields=fields)

    # Ledger reversal
    LSV.post_purchase_reversal(
        actor=actor,
        total_minor=minor3(bill.total or DEC0),
        paid_minor=minor3(bill.paid_amount or DEC0),
        provider_id=bill.provider_id,
        source=("billing", "Bill", bill.id),
    )

    bill.delete()


@transaction.atomic
def pay_full(*, actor, bill_id: int) -> Bill:
    """
    Mark a bill as fully paid and post SAFE payment against PROVIDER_PAYABLE.
    """
    bill = Bill.objects.select_for_update().get(pk=bill_id)
    if bill.remaining <= 0:
        return bill

    pay_amt = bill.remaining  # capture before mutation
    bill.paid_amount = bill.total
    bill.status = Bill.Status.PAID
    bill.save(update_fields=["paid_amount", "status"])

    LSV.post_provider_payment_from_safe(
        actor=actor,
        amount_minor=minor3(pay_amt),
        provider_id=bill.provider_id,
        source=("billing", "Bill", bill.id),
    )
    return bill


@transaction.atomic
def pay_partial(*, actor, bill_id: int, amount: Decimal) -> Bill:
    """
    Increase paid_amount, update status, and post SAFE payment for 'amount'.
    """
    if amount <= 0:
        raise ValueError("amount must be positive")

    bill = Bill.objects.select_for_update().get(pk=bill_id)
    if amount > bill.remaining:
        raise ValueError("amount exceeds remaining")

    bill.paid_amount = (bill.paid_amount or DEC0) + amount
    bill.status = Bill.Status.PAID if bill.paid_amount >= bill.total else Bill.Status.PARTIAL
    bill.save(update_fields=["paid_amount", "status"])

    LSV.post_provider_payment_from_safe(
        actor=actor,
        amount_minor=minor3(amount),
        provider_id=bill.provider_id,
        source=("billing", "Bill", bill.id),
    )
    return bill


# =======================================================================
# Provider Returns
# =======================================================================

@transaction.atomic
def create_return(
    *,
    actor,
    provider_id: int,
    status: str,
    paid_amount: Decimal,
    items: Iterable[Dict[str, Any]],
) -> ProviderReturn:
    """
    Create a provider return, decrease stock, and post return to the ledger:
      Cr INVENTORY (total) / Dr SAFE (paid) / Dr PROVIDER_RECEIVABLE (remaining)
    (Provider owes the store when not fully paid.)
    """
    provider = get_object_or_404(Provider.objects.select_for_update(), pk=provider_id)
    intended_paid = q3(paid_amount)

    pret = ProviderReturn(provider=provider, status=status, paid_amount=DEC0, total=DEC0)
    pret.save()

    grand = DEC0

    # Lock products once
    prod_ids = [int(it["product_id"]) for it in items]
    products = {p.id: p for p in Product.objects.select_for_update().filter(id__in=prod_ids)}

    for idx, row in enumerate(items, start=1):
        pid = int(row["product_id"])
        product = products.get(pid) or get_object_or_404(Product.objects.select_for_update(), pk=pid)

        unit_idx = 2 if int(row.get("unit_index") or 1) == 2 else 1
        qty_raw = Decimal(str(row.get("qty_raw") or "0"))
        if qty_raw <= 0:
            raise ValueError(f"qty must be > 0 at row {idx}")

        cost_u1 = q4(Decimal(str(row.get("cost") or "0")))
        total_override_raw = row.get("total_cost")
        total_override = Decimal(str(total_override_raw)) if total_override_raw not in (None, "") else None

        qty_primary = qty_raw
        cf = getattr(product, "conversion_factor", None)
        if unit_idx == 2 and cf:
            qty_primary = qty_raw * Decimal(str(cf))
        qty_primary = q3(qty_primary)

        line_total = q3(total_override) if (total_override and total_override > 0) else q3(cost_u1 * qty_primary)

        ProviderReturnItem.objects.create(
            ret=pret,
            product=product,
            unit_index=unit_idx,
            qty_primary=qty_primary,
            cost=cost_u1,
            line_total=line_total,
        )

        # Stock decrease (return to provider)
        product.stock_qty = (product.stock_qty or DEC0) - qty_primary
        update_fields = ["stock_qty"]
        if hasattr(product, "updated_at"):
            from django.utils import timezone
            product.updated_at = timezone.now()
            update_fields.append("updated_at")
        product.save(update_fields=list(dict.fromkeys(update_fields)))

        grand += line_total

    # Totals and payment state (authoritative)
    pret.total = q3(grand)
    final_paid = _resolve_paid_amount(status, intended_paid, pret.total)
    pret.paid_amount = final_paid

    if pret.paid_amount >= pret.total:
        pret.status = ProviderReturn.Status.PAID
    elif pret.paid_amount > DEC0:
        pret.status = ProviderReturn.Status.PARTIAL
    else:
        pret.status = ProviderReturn.Status.UNPAID

    pret.save(update_fields=["total", "paid_amount", "status"])

    # Ledger: Provider return via SAFE
    total_minor = minor3(pret.total)
    paid_minor = minor3(pret.paid_amount or DEC0)
    LSV.post_provider_return(
        actor=actor,
        total_minor=total_minor,
        paid_minor=paid_minor,
        provider_id=provider.id,
        source=("billing", "ProviderReturn", pret.id),
    )
    return pret


@transaction.atomic
def delete_return(*, actor, return_id: int) -> None:
    """
    Reverse a provider return:
      Dr INVENTORY / Cr SAFE (paid) / Cr PROVIDER_RECEIVABLE (remaining)
    """
    pret = ProviderReturn.objects.select_for_update().prefetch_related("items").get(pk=return_id)

    prod_ids = list(pret.items.values_list("product_id", flat=True))
    products = {p.id: p for p in Product.objects.select_for_update().filter(id__in=prod_ids)}

    for it in pret.items.all():
        p = products.get(it.product_id)
        if not p:
            continue
        # add stock back
        p.stock_qty = (p.stock_qty or DEC0) + (it.qty_primary or DEC0)
        fields = ["stock_qty"]
        if hasattr(p, "updated_at"):
            from django.utils import timezone
            p.updated_at = timezone.now()
            fields.append("updated_at")
        p.save(update_fields=fields)

    LSV.post_provider_return_reversal(
        actor=actor,
        total_minor=minor3(pret.total or DEC0),
        paid_minor=minor3(pret.paid_amount or DEC0),
        provider_id=pret.provider_id,
        source=("billing", "ProviderReturn", pret.id),
    )

    pret.delete()


@transaction.atomic
def collect_full(*, actor, return_id: int) -> ProviderReturn:
    """
    Provider pays the store the remaining balance for a return.
      Dr SAFE / Cr PROVIDER_RECEIVABLE
    """
    pret = ProviderReturn.objects.select_for_update().get(pk=return_id)
    if pret.remaining <= 0:
        return pret

    amt = pret.remaining
    pret.paid_amount = pret.total
    pret.status = ProviderReturn.Status.PAID
    pret.save(update_fields=["paid_amount", "status"])

    LSV.collect_from_provider(
        actor=actor,
        amount_minor=minor3(amt),
        provider_id=pret.provider_id,
        source=("billing", "ProviderReturn", pret.id),
    )
    return pret


@transaction.atomic
def collect_partial(*, actor, return_id: int, amount: Decimal) -> ProviderReturn:
    """
    Partial collection on provider receivable.
    """
    if amount <= 0:
        raise ValueError("amount must be positive")

    pret = ProviderReturn.objects.select_for_update().get(pk=return_id)
    if amount > pret.remaining:
        raise ValueError("amount exceeds remaining")

    pret.paid_amount = (pret.paid_amount or DEC0) + amount
    pret.status = ProviderReturn.Status.PAID if pret.paid_amount >= pret.total else ProviderReturn.Status.PARTIAL
    pret.save(update_fields=["paid_amount", "status"])

    LSV.collect_from_provider(
        actor=actor,
        amount_minor=minor3(amount),
        provider_id=pret.provider_id,
        source=("billing", "ProviderReturn", pret.id),
    )
    return pret
