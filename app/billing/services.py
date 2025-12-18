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
    bill = Bill(provider=provider, total=DEC0, created_by=actor)
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
    from debts.models import DebtorDebt, DebtorPayment
    from inventory.models import ProductMovement  # local import to avoid cycles
    from stock.models import StockFifoLayer
    from ledger import services as LSV

    # Lock bill + related rows
    bill = (
        Bill.objects
        .select_for_update()
        .select_related("provider")
        .prefetch_related("items", "items__product")
        .get(pk=bill_id)
    )

    # ==========================
    # 1) Enforce FIFO "untouched"
    # ==========================
    # Only allow deletion if NO quantity from this bill was ever consumed in FIFO.
    _, _, untouched = get_bill_status_flags(bill)
    if not untouched:
        raise ValueError("cannot delete a bill whose items were already sold/returned")

    # ==========================
    # 2) Locate Debtor entry
    # ==========================
    entry = DebtorDebt.objects.select_for_update().filter(
        source_app="billing",
        source_model="Bill",
        source_id=str(bill.id),
    ).first()

    paid_amount = q3(entry.paid_amount if entry and entry.paid_amount is not None else DEC0)
    total_amount = q3(bill.total or DEC0)

    # ==========================
    # 3) Detect container (as before)
    # ==========================
    mv_container = None

    # Prefer new-style movements tied to BillItem
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

    # Fallback: old-style movements tied directly to Bill
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

    # ==========================================
    # 4) Reverse stock movements + FIFO layers
    # ==========================================
    for it in bill.items.all():
        product = it.product

        # 4.a) Reverse inventory movement: purchase_reversal (NEGATIVE qty)
        InvSV.record_movement(
            actor=actor,
            product=product,
            unit_index=int(it.unit_index),
            qty_primary=-(it.qty_primary or DEC0),
            unit_cost=it.cost or DEC0,
            movement_type="purchase_reversal",
            source_app="billing",
            source_model="Bill",
            source_id=bill.id,
            container=mv_container,          # same container used at purchase
            extra_product_updates=None,
        )

        # 4.b) Remove FIFO layers created from this BillItem (only if we know container)
        if mv_container is not None:
            StockFifoLayer.objects.filter(
                product=product,
                container=mv_container,
                source_app="billing",
                source_model="BillItem",
                source_id=str(it.id),
            ).delete()

    # ==========================
    # 5) Ledger reversal (GL)
    # ==========================
    # This handles INVENTORY + PROVIDER_PAYABLE + SAFE in one go.
    # Behaviour by case:
    #   - PAID:    paid_amount == total_amount → SAFE goes UP by total
    #   - UNPAID:  paid_amount == 0            → SAFE unchanged (no volt movement)
    #   - PARTIAL: 0 < paid_amount < total     → SAFE goes UP by paid_amount
    total_minor = minor(total_amount)
    paid_minor = minor(paid_amount)

    if total_minor > 0:
        LSV.post_purchase_reversal(
            actor=actor,
            total_minor=total_minor,
            paid_minor=paid_minor,
            provider_id=bill.provider_id,
            source=("billing", "Bill", bill.id),
        )
        # This journal IS your "volt movement (up) – bill deletion" in
        # the paid/partial cases, because it debits SAFE.

    # ==========================
    # 6) Delete debt + payments
    # ==========================
    if entry:
        DebtorPayment.objects.filter(entry=entry).delete()
        entry.delete()

    # ==========================
    # 7) Delete the bill itself
    # ==========================
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
    container: ProductContainer | None = None,   # ⬅️ single-container mode (legacy)
    source_bill_serial: int | None = None,
) -> ProviderReturn:
    """
    Create a ProviderReturn, decrease stock via inventory movements, post GL, and register CreditorDebt.

    Supports two shapes of `items`:

    1) Legacy (single container):
       {
         "product_id": ...,
         "unit_index": 1|2,
         "qty_raw": "10.000",
         "cost": "123.456",
         "total_cost": "..." (optional)
       }
       + global `container` argument.

    2) Wizard per-container mode:
       {
         "product_id": ...,
         "unit_index": 1,
         "qty_primary": "10.000",
         "cost": "123.456",
         "container_splits": [
            {"code": "store", "qty_primary": "3.000"},
            {"code": "wh1",   "qty_primary": "7.000"},
         ]
       }
       In this mode `container` is ignored and we use `container_splits`.
    """
    provider = get_object_or_404(Provider.objects.select_for_update(), pk=provider_id)
    intended_paid = q3(paid_amount)

    pret = ProviderReturn(
        provider=provider,
        total=DEC0,
        source_bill_serial=source_bill_serial,
        created_by=actor,
    )
    pret.save()

    pret.initial_paid = intended_paid
    pret.initial_status = (status or "unpaid").lower()
    pret.save(update_fields=["initial_paid", "initial_status", "source_bill_serial"])

    # ====== Collect container codes (wizard mode) ======
    all_codes: set[str] = set()
    for row in items:
        for split in row.get("container_splits") or []:
            code = (split.get("code") or "").strip().lower()
            if code:
                all_codes.add(code)

    containers_by_code: dict[str, ProductContainer] = {}
    if all_codes:
        containers_by_code = {
            c.code.lower(): c
            for c in ProductContainer.objects.select_for_update().filter(code__in=all_codes)
        }

    # ====== Process items ======
    grand = DEC0
    prod_ids = [int(it["product_id"]) for it in items]
    products = {p.id: p for p in Product.objects.select_for_update().filter(id__in=prod_ids)}

    for idx, row in enumerate(items, start=1):
        pid = int(row["product_id"])
        product = products.get(pid) or get_object_or_404(Product.objects.select_for_update(), pk=pid)

        unit_idx = 2 if int(row.get("unit_index") or 1) == 2 else 1
        cost_u1 = q4(Decimal(str(row.get("cost") or "0")))
        total_override_raw = row.get("total_cost")
        total_override = Decimal(str(total_override_raw)) if total_override_raw not in (None, "") else None

        container_splits = row.get("container_splits") or []

        # ---------- determine qty_primary ----------
        if container_splits:
            # wizard mode: qty_primary is already in primary unit and split per container
            qty_total_primary = DEC0
            for split in container_splits:
                q_split = Decimal(str(split.get("qty_primary") or "0"))
                if q_split <= DEC0:
                    continue
                qty_total_primary = q3(qty_total_primary + q_split)

            if qty_total_primary <= DEC0:
                raise ValueError(f"qty must be > 0 at row {idx}")

            qty_primary = q3(qty_total_primary)

        else:
            # legacy mode: use qty_raw (or qty_primary) + unit_index + conversion factor
            qty_raw = Decimal(str(row.get("qty_raw") or row.get("qty_primary") or "0"))
            if qty_raw <= 0:
                raise ValueError(f"qty must be > 0 at row {idx}")

            qty_primary = qty_raw
            cf = getattr(product, "conversion_factor", None)
            if unit_idx == 2 and cf:
                qty_primary *= Decimal(str(cf))
            qty_primary = q3(qty_primary)

        # ---------- line total & ProviderReturnItem ----------
        line_total = q3(total_override) if (total_override and total_override > 0) else q3(cost_u1 * qty_primary)

        ProviderReturnItem.objects.create(
            ret=pret,
            product=product,
            unit_index=unit_idx,
            qty_primary=qty_primary,
            cost=cost_u1,
            line_total=line_total,
        )

        # ---------- Inventory movements ----------
        if container_splits:
            # wizard: multiple negative movements, one per container
            for split in container_splits:
                q_split = Decimal(str(split.get("qty_primary") or "0"))
                if q_split <= DEC0:
                    continue

                code = (split.get("code") or "").strip().lower()
                cont = containers_by_code.get(code)
                if cont is None:
                    raise ValueError("حاوية غير معروفة في مرتجع المورد.")

                InvSV.record_provider_return_item(
                    actor=actor,
                    product=product,
                    unit_index=unit_idx,
                    qty_primary=-q3(q_split),  # stock out per container
                    unit_cost=cost_u1,
                    source_app="billing",
                    source_model="ProviderReturn",
                    source_id=pret.id,
                    container=cont,
                )
        else:
            # legacy: single movement in the given container
            InvSV.record_provider_return_item(
                actor=actor,
                product=product,
                unit_index=unit_idx,
                qty_primary=-qty_primary,
                unit_cost=cost_u1,
                source_app="billing",
                source_model="ProviderReturn",
                source_id=pret.id,
                container=container,
            )

        grand = q3(grand + line_total)

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
    Compute remaining quantity per BillItem using StockFifoLayer.

    Instead of reconstructing FIFO from ProductMovement, we trust the
    stock app's FIFO layers, which already track how much of each batch
    is left, regardless of which container it's currently in.

    Returns:
        { bill_item_id: left_qty_primary }
    """
    from collections import defaultdict
    from stock.models import StockFifoLayer

    items = list(bill.items.all())
    if not items:
        return {}

    item_ids = [it.id for it in items]
    item_ids_set = set(item_ids)

    layers = (
        StockFifoLayer.objects
        .filter(
            source_app="billing",
            source_model="BillItem",
            source_id__in=[str(i) for i in item_ids],
        )
    )

    left_by_item: dict[int, Decimal] = defaultdict(lambda: DEC0)

    # Try multiple possible field names for "qty left" to stay compatible
    qty_field_candidates = (
        "qty_left_primary",
        "qty_left",
        "qty_remaining",
        "qty_primary_left",
    )

    for layer in layers:
        try:
            item_id = int(layer.source_id or "0")
        except (TypeError, ValueError):
            continue

        if item_id not in item_ids_set:
            continue

        qty_raw = None
        for fname in qty_field_candidates:
            if hasattr(layer, fname):
                qty_raw = getattr(layer, fname)
                break

        qty = q3(qty_raw or DEC0)
        if qty <= DEC0:
            continue

        left_by_item[item_id] = q3(left_by_item[item_id] + qty)

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
