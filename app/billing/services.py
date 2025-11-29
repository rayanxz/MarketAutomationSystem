# app/billing/services.py
from __future__ import annotations


from collections import defaultdict
from decimal import Decimal
from typing import Iterable, Dict, Any, Optional
from datetime import date

from django.db import transaction
from inventory.models import ProductMovement

from django.shortcuts import get_object_or_404
from django.db.models import Max

from billing.models import (
    Provider,
    Bill,
    BillItem,
    ProviderReturn,
    ProviderReturnItem,
)

from debts.models import (
    PartyType,
    DebtorDebt ,
    CreditorDebt ,
)

from catalog.models import Product
from ledger import services as LSV
from debts import services as DebtSV  # NEW unified debts layer
from inventory import services as InvSV
from stock.models import ProductContainer


# ====== Decimals / helpers ======
DEC0 = Decimal("0")
DEC3 = Decimal("0.001")
DEC4 = Decimal("0.0001")

def q3(x: Decimal) -> Decimal:
    return (x or DEC0).quantize(DEC3)

def q4(x: Decimal) -> Decimal:
    return (x or DEC0).quantize(DEC4)

def minor(x: Decimal) -> int:
    """1 minor = 1 SYP."""
    return LSV.to_minor(q3(x or DEC0))


def _resolve_paid_amount(status: str, intended_paid: Decimal, total: Decimal) -> Decimal:
    """
    Normalize user intent vs. authoritative server logic.
    """
    s = (status or "").lower().strip()
    total = q3(total)
    intended = q3(intended_paid if intended_paid is not None else DEC0)
    if s == "paid":
        return total
    if s == "unpaid":
        return DEC0
    if intended < DEC0:
        intended = DEC0
    if intended > total:
        intended = total
    return intended


# =======================================================================
# SERIAL HELPERS
# =======================================================================

@transaction.atomic
def _next_bill_serial_locked() -> int:
    from debts.models import DebtorDebt
    m_bill = Bill.objects.select_for_update().aggregate(m=Max("serial")).get("m") or 0
    m_debt = DebtorDebt.objects.select_for_update().aggregate(m=Max("doc_serial")).get("m") or 0
    return int(max(int(m_bill or 0), int(m_debt or 0))) + 1


@transaction.atomic
def _next_return_serial_locked() -> int:
    from debts.models import CreditorDebt
    m_ret = ProviderReturn.objects.select_for_update().aggregate(m=Max("serial")).get("m") or 0
    m_cred = CreditorDebt.objects.select_for_update().aggregate(m=Max("doc_serial")).get("m") or 0
    return int(max(int(m_ret or 0), int(m_cred or 0))) + 1


# =======================================================================
# BILLS (Purchases)
# =======================================================================
@transaction.atomic
def create_bill(
    *,
    actor,
    provider_id: int,
    status: str,
    paid_amount: Decimal,
    items: Iterable[Dict[str, Any]],
    update_product_defaults: bool = False,
    container: ProductContainer | None = None,   # ⬅️ NEW
) -> Bill:
    """
    Create a Bill, increase stock via inventory movements, post GL, and register DebtorDebt.
    """
    provider = get_object_or_404(Provider.objects.select_for_update(), pk=provider_id)
    intended_paid = q3(paid_amount)
    bill = Bill(provider=provider, total=DEC0)
    bill.save()

    # ====== Add items & update stock via inventory layer ======
    grand = DEC0
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

        # Convert to primary unit
        qty_primary = qty_raw
        cf = getattr(product, "conversion_factor", None)
        if unit_idx == 2 and cf:
            qty_primary *= Decimal(str(cf))
        qty_primary = q3(qty_primary)

        line_total = q3(total_override) if (total_override and total_override > 0) else q3(cost_u1 * qty_primary)

        # Create the bill item
        item = BillItem.objects.create(
            bill=bill,
            product=product,
            unit_index=unit_idx,
            qty_primary=qty_primary,
            cost=cost_u1,
            price=price_u1,
            line_total=line_total,
        )

        # Stock increase via inventory layer (+qty_primary)
        extra_updates: Dict[str, Any] = {}
        if update_product_defaults:
            extra_updates["cost"] = cost_u1
            extra_updates["price"] = price_u1

        InvSV.record_purchase_item(
            actor=actor,
            product=product,
            unit_index=unit_idx,
            qty_primary=qty_primary,
            unit_cost=cost_u1,
            source_app="billing",
            source_model="BillItem",   # ⬅️ tie movement to BillItem
            source_id=item.id,         # ⬅️ so FIFO can map back
            container=container,
            extra_product_updates=extra_updates or None,
        )

        grand += line_total

    bill.total = q3(grand)
    bill.save(update_fields=["total"])

    # ====== Create debt ======
    final_paid = _resolve_paid_amount(status, intended_paid, bill.total)
    DebtSV.create_debtor_entry(
        provider=provider,
        total=bill.total,
        paid_amount=final_paid,
        source_app="billing",
        source_model="Bill",
        source_id=str(bill.id),
        doc_serial=bill.serial,
    )

    # ====== Ledger ======
    total_minor = minor(bill.total)
    paid_minor = minor(final_paid)
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
    bill = (
        Bill.objects
        .select_for_update()
        .select_related("provider")
        .prefetch_related("items", "items__product")
        .get(pk=bill_id)
    )

    # Block if there are non-closed debtor entries
    from debts.models import DebtorDebt, DebtorPayment
    entry = DebtorDebt.objects.select_for_update().filter(
        source_app="billing", source_model="Bill", source_id=str(bill.id)
    ).first()
    if entry and entry.status != DebtorDebt.Status.CLOSED and (entry.paid_amount or 0) > 0:
        raise ValueError("cannot delete a bill with payments; refund/void first")

    # Try to detect container from existing movements (if bill was created after inventory integration)
    from inventory.models import ProductMovement  # local import to avoid cycles

    mv_container = None

    # First try new-style movements tied to BillItem
    item_ids = list(bill.items.values_list("id", flat=True))
    if item_ids:
        mv = (
            ProductMovement.objects
            .filter(
                source_app="billing",
                source_model="BillItem",
                source_id__in=item_ids,
            )
            .select_related("container")
            .first()
        )
        if mv and mv.container_id:
            mv_container = mv.container

    # Fallback: old-style movements tied to Bill
    if mv_container is None:
        mv_old = (
            ProductMovement.objects
            .filter(
                source_app="billing",
                source_model="Bill",
                source_id=str(bill.id),
            )
            .select_related("container")
            .first()
        )
        if mv_old and mv_old.container_id:
            mv_container = mv_old.container

    # Reverse stock via movements (purchase_reversal)
    for it in bill.items.all():
        product = it.product
        InvSV.record_movement(
            actor=actor,
            product=product,
            unit_index=int(it.unit_index),
            qty_primary=-(it.qty_primary or DEC0),  # reverse
            unit_cost=it.cost or DEC0,
            movement_type="purchase_reversal",
            source_app="billing",
            source_model="Bill",
            source_id=bill.id,
            container=mv_container,                # ⬅️ KEEP SAME CONTAINER (if known)
            extra_product_updates=None,
        )

    # Remove subledger entry (and its payments if any exist but total=0)
    if entry:
        DebtorPayment.objects.filter(entry=entry).delete()
        entry.delete()

    # TODO: post reversal in GL if you have a reversal policy
    # LSV.reverse_purchase(...)

    bill.delete()





# =======================================================================
# MANUAL DEBTS
# =======================================================================

@transaction.atomic
def create_manual_debt(
    *,
    actor,
    direction: str,
    party_type: str,
    provider_id: Optional[int],
    party_name: str,
    amount: Decimal,
    due_date: Optional[date] = None,
):
    """
    Delegate manual debts to DebtSV layer.
    """
    return DebtSV.create_manual_debt(
        actor=actor,
        direction=direction,
        party_type=party_type,
        provider_id=provider_id,
        party_name=party_name,
        amount=amount,
        due_date=due_date,
    )


# =======================================================================
# PROVIDER RETURNS (Purchases Returns)
# =======================================================================

@transaction.atomic
def create_return(
    *,
    actor,
    provider_id: int,
    status: str,
    paid_amount: Decimal,
    items: Iterable[Dict[str, Any]],
    container: ProductContainer | None = None,   # ⬅️ NEW
) -> ProviderReturn:
    """
    Create a ProviderReturn, decrease stock via inventory movements, post GL, and register CreditorDebt.
    """
    provider = get_object_or_404(Provider.objects.select_for_update(), pk=provider_id)
    intended_paid = q3(paid_amount)
    pret = ProviderReturn(provider=provider, total=DEC0)
    pret.save()

    pret.initial_paid = intended_paid
    pret.initial_status = (status or "unpaid").lower()
    pret.save(update_fields=["initial_paid", "initial_status"])

    # ====== Process items ======
    grand = DEC0
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
            qty_primary *= Decimal(str(cf))
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

        # Stock decrease via inventory layer (NEGATIVE qty_primary)
        InvSV.record_provider_return_item(
            actor=actor,
            product=product,
            unit_index=unit_idx,
            qty_primary=-qty_primary,  # stock out
            unit_cost=cost_u1,
            source_app="billing",
            source_model="ProviderReturn",
            source_id=pret.id,
            container=container,       # ⬅️ PASS CONTAINER
        )

        grand += line_total

    pret.total = q3(grand)
    pret.save(update_fields=["total"])

    # ====== Create debt ======
    final_collected = _resolve_paid_amount(status, intended_paid, pret.total)
    DebtSV.create_creditor_entry(
        provider=provider,
        total=pret.total,
        collected=final_collected,
        source_app="billing",
        source_model="ProviderReturn",
        source_id=str(pret.id),
        doc_serial=pret.serial,
    )

    # ====== Ledger ======
    total_minor = minor(pret.total)
    paid_minor = minor(final_collected)
    LSV.post_provider_return(
        actor=actor,
        total_minor=total_minor,
        paid_minor=paid_minor,
        provider_id=provider.id,
        source=("billing", "ProviderReturn", pret.id),
    )
    return pret




@transaction.atomic
def pay_full(*, actor, bill_id: int) -> Bill:
    bill = Bill.objects.select_for_update().get(pk=bill_id)
    # ensure a DebtorDebt exists (self-heal if someone created a bill before debts migration)
    entry, _ = DebtorDebt.objects.select_for_update().get_or_create(
        provider=bill.provider,
        source_app="billing",
        source_model="Bill",
        source_id=str(bill.id),
        defaults=dict(
            total=q3(bill.total or DEC0),
            paid_amount=DEC0,
            status=DebtorDebt.Status.OPEN,
            party_type=PartyType.PROVIDER,
            party_name=bill.provider.name if bill.provider_id else "",
            doc_serial=bill.serial,
        ),
    )
    DebtSV.pay_debt(actor=actor, entry_id=entry.id, full=True)
    return bill


@transaction.atomic
def pay_partial(*, actor, bill_id: int, amount: Decimal) -> Bill:
    amt = q3(amount or DEC0)
    if amt <= 0:
        raise ValueError("amount must be positive")

    bill = Bill.objects.select_for_update().get(pk=bill_id)
    entry, _ = DebtorDebt.objects.select_for_update().get_or_create(
        provider=bill.provider,
        source_app="billing",
        source_model="Bill",
        source_id=str(bill.id),
        defaults=dict(
            total=q3(bill.total or DEC0),
            paid_amount=DEC0,
            status=DebtorDebt.Status.OPEN,
            party_type=PartyType.PROVIDER,
            party_name=bill.provider.name if bill.provider_id else "",
            doc_serial=bill.serial,
        ),
    )
    # validate against remaining
    if amt > q3(entry.remaining):
        raise ValueError(f"amount exceeds remaining ({q3(entry.remaining)})")

    DebtSV.pay_debt(actor=actor, entry_id=entry.id, amount=amt, full=False)
    return bill


# =======================================================================
# RECEIVABLES (Provider Returns) – wrappers to debts layer
# =======================================================================

@transaction.atomic
def collect_full(*, actor, return_id: int) -> ProviderReturn:
    pret = ProviderReturn.objects.select_for_update().get(pk=return_id)
    entry = CreditorDebt.objects.select_for_update().get(
        source_app="billing", source_model="ProviderReturn", source_id=str(pret.id)
    )
    DebtSV.collect_debt(actor=actor, entry_id=entry.id, full=True)
    return pret


@transaction.atomic
def collect_partial(*, actor, return_id: int, amount: Decimal) -> ProviderReturn:
    amt = q3(amount or DEC0)
    if amt <= 0:
        raise ValueError("amount must be positive")

    pret = ProviderReturn.objects.select_for_update().get(pk=return_id)
    entry = CreditorDebt.objects.select_for_update().get(
        source_app="billing", source_model="ProviderReturn", source_id=str(pret.id)
    )
    if amt > q3(entry.remaining):
        raise ValueError(f"amount exceeds remaining ({q3(entry.remaining)})")

    DebtSV.collect_debt(actor=actor, entry_id=entry.id, amount=amt, full=False)
    return pret


# =======================================================================
# MANUAL-DEBT wrappers (views already call SV.*)
# =======================================================================

@transaction.atomic
def pay_manual_debt_full(*, actor, entry_id: int) -> DebtorDebt:
    return DebtSV.pay_debt(actor=actor, entry_id=entry_id, full=True)

@transaction.atomic
def pay_manual_debt_partial(*, actor, entry_id: int, amount: Decimal) -> DebtorDebt:
    amt = q3(amount or DEC0)
    if amt <= 0:
        raise ValueError("amount must be positive")
    return DebtSV.pay_debt(actor=actor, entry_id=entry_id, amount=amt, full=False)

@transaction.atomic
def collect_manual_debt_full(*, actor, entry_id: int) -> CreditorDebt:
    return DebtSV.collect_debt(actor=actor, entry_id=entry_id, full=True)

@transaction.atomic
def collect_manual_debt_partial(*, actor, entry_id: int, amount: Decimal) -> CreditorDebt:
    amt = q3(amount or DEC0)
    if amt <= 0:
        raise ValueError("amount must be positive")
    return DebtSV.collect_debt(actor=actor, entry_id=entry_id, amount=amt, full=False)

# ==============================
# FIFO helpers for purchase bills
# ==============================

def compute_bill_fifo_left(bill: Bill) -> dict[int, Decimal]:
    """
    Compute remaining quantity per BillItem using full FIFO over ProductMovement.

    Returns:
        { bill_item_id: left_qty_primary }
    """
    # Collect items by product
    items = list(bill.items.select_related("product"))
    if not items:
        return {}

    by_product: dict[int, list] = defaultdict(list)
    for it in items:
        if not it.product_id:
            continue
        by_product[it.product_id].append(it)

    left_by_item: dict[int, Decimal] = defaultdict(lambda: DEC0)

    # Process each product separately
    for product_id, product_items in by_product.items():
        # All movements for this product, oldest → newest
        mv_qs = (
            ProductMovement.objects
            .filter(product_id=product_id)
            .order_by("created_at", "id")
        )

        inflow_remaining: dict[int, Decimal] = {}
        inflow_map: dict[int, ProductMovement] = {}
        fifo_queue: list[ProductMovement] = []

        for mv in mv_qs:
            qty = q3(mv.qty_primary or DEC0)

            # ===== FIX: provider returns must ALWAYS be treated as OUT (negative) =====
            # In older data, some ProviderReturn movements may have been saved with +qty.
            # For the FIFO "what is left from each BillItem", a provider return is ALWAYS
            # stock OUT, so we flip the sign to negative if needed.
            from inventory.models import ProductMovement as PM

            if (
                mv.movement_type == PM.MovementType.PROVIDER_RETURN
                and qty > DEC0
            ):
                qty = -qty

            if qty > DEC0:
                # Inflow (purchase, sale_return, adjustment positive, ...)
                inflow_remaining[mv.id] = qty
                inflow_map[mv.id] = mv
                fifo_queue.append(mv)

            elif qty < DEC0:
                # Outflow (sale, provider_return, adjustment negative, ...)
                need = -qty
                while need > DEC0 and fifo_queue:
                    first = fifo_queue[0]
                    first_id = first.id
                    rem = inflow_remaining.get(first_id, DEC0)

                    if rem <= DEC0:
                        # no left in this batch, pop and continue
                        fifo_queue.pop(0)
                        continue

                    if rem <= need:
                        # consume entire batch
                        need = q3(need - rem)
                        inflow_remaining[first_id] = DEC0
                        fifo_queue.pop(0)
                    else:
                        # consume part of this batch
                        inflow_remaining[first_id] = q3(rem - need)
                        need = DEC0



        # Map remaining inflows that belong to BillItems of THIS bill
        item_ids_for_product = {it.id for it in product_items}

        for mv_id, rem in inflow_remaining.items():
            rem = q3(rem or DEC0)
            if rem <= DEC0:
                continue
            mv = inflow_map[mv_id]
            if mv.source_app != "billing" or mv.source_model != "BillItem":
                # Not a purchase line tied to a BillItem → ignore for our per-bill left
                continue
            try:
                item_id = int(mv.source_id or "0")
            except (TypeError, ValueError):
                continue

            if item_id in item_ids_for_product:
                left_by_item[item_id] = q3(left_by_item[item_id] + rem)

    return left_by_item


def get_bill_status_flags(bill: Bill) -> tuple[dict[int, Decimal], bool, bool]:
    """
    Returns:
        (left_map, is_closed, untouched)

    - left_map: { BillItem.id: left_qty_primary }
    - is_closed: True if ALL items have left_qty == 0
    - untouched: True if ALL items still have full quantity
                 (no FIFO consumption from this bill at all)
    """
    left_map = compute_bill_fifo_left(bill)
    items = list(bill.items.all())
    if not items:
        # No items → nothing to do, treat as closed & untouched
        return left_map, True, True

    is_closed = True
    untouched = True

    for it in items:
        orig = q3(it.qty_primary or DEC0)
        left = q3(left_map.get(it.id, DEC0))

        if left > DEC0:
            is_closed = False
        if left < orig:
            untouched = False

    return left_map, is_closed, untouched
