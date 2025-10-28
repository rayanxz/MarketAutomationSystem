# app/billing/services.py
from __future__ import annotations

import contextlib
from decimal import Decimal
from typing import Iterable, Dict, Any

from django.db import transaction
from django.shortcuts import get_object_or_404

from billing.models import (
    Provider, Bill, BillItem,
    ProviderReturn, ProviderReturnItem,
    DebtorEntry, DebtorPayment,
    CreditorEntry, CreditorReceipt,
)
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
# Bills  (commercial doc) + Debtor subledger (payables)
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
    Create a bill, increase stock, post GL (purchase), and create DebtorEntry:
      Dr INVENTORY (total) / Cr SAFE (paid) / Cr PROVIDER_PAYABLE (remaining)
    """
    provider = get_object_or_404(Provider.objects.select_for_update(), pk=provider_id)
    intended_paid = q3(paid_amount)

    # Persist a shell bill first to get PK/serial
    bill = Bill(provider=provider, total=DEC0)
    if serial is not None:
        bill.serial = serial
    bill.save()

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
        price_u1 = q4(Decimal(str(row.get("price") or "0")))
        total_override_raw = row.get("total_cost")
        total_override = Decimal(str(total_override_raw)) if total_override_raw not in (None, "") else None

        qty_primary = qty_raw
        cf = getattr(product, "conversion_factor", None)
        if unit_idx == 2 and cf:
            qty_primary = qty_raw * Decimal(str(cf))
        qty_primary = q3(qty_primary)

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

        # Stock increase
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

    # Totals
    bill.total = q3(grand)
    bill.save(update_fields=["total"])

    # Debtor entry (source of truth for payable)
    final_paid = _resolve_paid_amount(status, intended_paid, bill.total)
    debtor, _ = DebtorEntry.objects.update_or_create(
        provider=provider,
        source_app="billing",
        source_model="Bill",
        source_id=str(bill.id),
        defaults={
            "total": q3(bill.total),
            "paid_amount": q3(final_paid),
            "status": DebtorEntry.Status.CLOSED if q3(bill.total - final_paid) <= DEC0 else DebtorEntry.Status.OPEN,
        },
    )

    # GL posting
    total_minor = minor3(bill.total)
    paid_minor = minor3(final_paid)
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
    Reverse stock and post GL reversal:
      Cr INVENTORY / Dr SAFE (paid) / Dr PROVIDER_PAYABLE (remaining)
    Only allowed if no DebtorPayment exists for the associated entry.
    """
    bill = Bill.objects.select_for_update().prefetch_related("items").get(pk=bill_id)

    debtor = DebtorEntry.objects.select_for_update().filter(
        source_app="billing", source_model="Bill", source_id=str(bill.id)
    ).first()
    if debtor and debtor.payments.exists():
        raise ValueError("Cannot delete a bill with recorded debtor payments. Reverse payments first.")

    # Reverse stock
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

    # GL reversal
    paid_amt = debtor.paid_amount if debtor else DEC0
    LSV.post_purchase_reversal(
        actor=actor,
        total_minor=minor3(bill.total or DEC0),
        paid_minor=minor3(paid_amt),
        provider_id=bill.provider_id,
        source=("billing", "Bill", bill.id),
    )

    if debtor:
        debtor.delete()
    bill.delete()


@transaction.atomic
def pay_full(*, actor, bill_id: int) -> Bill:
    """
    Pay the remaining balance against DebtorEntry and post GL:
      Dr PROVIDER_PAYABLE / Cr SAFE (in our GL design this is implemented via LSV helper)
    """
    bill = Bill.objects.select_for_update().get(pk=bill_id)
    debtor = DebtorEntry.objects.select_for_update().get(
        source_app="billing", source_model="Bill", source_id=str(bill.id)
    )
    if debtor.remaining <= 0:
        return bill

    pay_amt = debtor.remaining  # capture
    # Update subledger
    DebtorPayment.objects.create(entry=debtor, amount=q3(pay_amt))
    debtor.paid_amount = q3((debtor.paid_amount or DEC0) + pay_amt)
    debtor.status = DebtorEntry.Status.CLOSED if debtor.remaining <= DEC0 else DebtorEntry.Status.OPEN
    debtor.save(update_fields=["paid_amount", "status"])

    # GL
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
    Pay a partial amount against DebtorEntry and post GL.
    """
    amt = q3(amount or DEC0)
    if amt <= 0:
        raise ValueError("amount must be positive")

    bill = Bill.objects.select_for_update().get(pk=bill_id)
    debtor = DebtorEntry.objects.select_for_update().get(
        source_app="billing", source_model="Bill", source_id=str(bill.id)
    )
    if amt > debtor.remaining:
        raise ValueError("amount exceeds remaining")

    DebtorPayment.objects.create(entry=debtor, amount=amt)
    debtor.paid_amount = q3((debtor.paid_amount or DEC0) + amt)
    debtor.status = DebtorEntry.Status.CLOSED if debtor.remaining <= DEC0 else DebtorEntry.Status.OPEN
    debtor.save(update_fields=["paid_amount", "status"])

    LSV.post_provider_payment_from_safe(
        actor=actor,
        amount_minor=minor3(amt),
        provider_id=bill.provider_id,
        source=("billing", "Bill", bill.id),
    )
    return bill


# =======================================================================
# Provider Returns (commercial doc) + Creditor subledger (receivables)
# =======================================================================

@transaction.atomic
def create_return(
    *,
    actor,
    provider_id: int,
    status: str,
    paid_amount: Decimal,   # "collected" at creation time
    items: Iterable[Dict[str, Any]],
) -> ProviderReturn:
    """
    Create a provider return, decrease stock, post GL (return), and create CreditorEntry:
      Cr INVENTORY (total) / Dr SAFE (paid) / Dr PROVIDER_RECEIVABLE (remaining)
    """
    provider = get_object_or_404(Provider.objects.select_for_update(), pk=provider_id)
    intended_paid = q3(paid_amount)

    pret = ProviderReturn(provider=provider, total=DEC0)
    pret.save()

    # We'll fill total later, but we can already store intended paid info
    final_collected = _resolve_paid_amount(status, intended_paid, DEC0)
    pret.initial_paid = intended_paid
    pret.initial_status = (status or "unpaid").lower()
    pret.save(update_fields=["initial_paid", "initial_status"])

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

        # Stock decrease
        product.stock_qty = (product.stock_qty or DEC0) - qty_primary
        update_fields = ["stock_qty"]
        if hasattr(product, "updated_at"):
            from django.utils import timezone
            product.updated_at = timezone.now()
            update_fields.append("updated_at")
        product.save(update_fields=list(dict.fromkeys(update_fields)))

        grand += line_total

    # Totals
    pret.total = q3(grand)
    pret.save(update_fields=["total"])

    # Creditor entry (source of truth for receivable)
    final_collected = _resolve_paid_amount(status, intended_paid, pret.total)
    cred, _ = CreditorEntry.objects.update_or_create(
        provider=provider,
        source_app="billing",
        source_model="ProviderReturn",
        source_id=str(pret.id),
        defaults={
            "total": q3(pret.total),
            "collected": q3(final_collected),
            "status": CreditorEntry.Status.CLOSED if q3(pret.total - final_collected) <= DEC0 else CreditorEntry.Status.OPEN,
        },
    )

    # GL posting
    total_minor = minor3(pret.total)
    paid_minor = minor3(final_collected)
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
    Only allowed if no CreditorReceipt exists for the associated entry.
    """
    pret = ProviderReturn.objects.select_for_update().prefetch_related("items").get(pk=return_id)

    cred = CreditorEntry.objects.select_for_update().filter(
        source_app="billing", source_model="ProviderReturn", source_id=str(pret.id)
    ).first()
    if cred and cred.receipts.exists():
        raise ValueError("Cannot delete a provider return with recorded receipts. Reverse receipts first.")

    # Reverse stock
    prod_ids = list(pret.items.values_list("product_id", flat=True))
    products = {p.id: p for p in Product.objects.select_for_update().filter(id__in=prod_ids)}

    for it in pret.items.all():
        p = products.get(it.product_id)
        if not p:
            continue
        p.stock_qty = (p.stock_qty or DEC0) + (it.qty_primary or DEC0)
        fields = ["stock_qty"]
        if hasattr(p, "updated_at"):
            from django.utils import timezone
            p.updated_at = timezone.now()
            fields.append("updated_at")
        p.save(update_fields=fields)

    # GL reversal
    collected_amt = cred.collected if cred else DEC0
    LSV.post_provider_return_reversal(
        actor=actor,
        total_minor=minor3(pret.total or DEC0),
        paid_minor=minor3(collected_amt),
        provider_id=pret.provider_id,
        source=("billing", "ProviderReturn", pret.id),
    )

    if cred:
        cred.delete()
    pret.delete()


@transaction.atomic
def collect_full(*, actor, return_id: int) -> ProviderReturn:
    """
    Provider pays the remaining balance (receivable) against CreditorEntry and post GL:
      Dr SAFE / Cr PROVIDER_RECEIVABLE
    """
    pret = ProviderReturn.objects.select_for_update().get(pk=return_id)
    cred = CreditorEntry.objects.select_for_update().get(
        source_app="billing", source_model="ProviderReturn", source_id=str(pret.id)
    )
    if cred.remaining <= 0:
        return pret

    amt = cred.remaining
    CreditorReceipt.objects.create(entry=cred, amount=q3(amt))
    cred.collected = q3((cred.collected or DEC0) + amt)
    cred.status = CreditorEntry.Status.CLOSED if cred.remaining <= DEC0 else CreditorEntry.Status.OPEN
    cred.save(update_fields=["collected", "status"])

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
    amt = q3(amount or DEC0)
    if amt <= 0:
        raise ValueError("amount must be positive")

    pret = ProviderReturn.objects.select_for_update().get(pk=return_id)
    cred = CreditorEntry.objects.select_for_update().get(
        source_app="billing", source_model="ProviderReturn", source_id=str(pret.id)
    )
    if amt > cred.remaining:
        raise ValueError("amount exceeds remaining")

    CreditorReceipt.objects.create(entry=cred, amount=amt)
    cred.collected = q3((cred.collected or DEC0) + amt)
    cred.status = CreditorEntry.Status.CLOSED if cred.remaining <= DEC0 else CreditorEntry.Status.OPEN
    cred.save(update_fields=["collected", "status"])

    LSV.collect_from_provider(
        actor=actor,
        amount_minor=minor3(amt),
        provider_id=pret.provider_id,
        source=("billing", "ProviderReturn", pret.id),
    )
    return pret
