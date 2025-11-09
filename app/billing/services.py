# app/billing/services.py
from __future__ import annotations

from decimal import Decimal
from typing import Iterable, Dict, Any, Optional
from datetime import date

from django.db import transaction
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
) -> Bill:
    """
    Create a Bill, increase stock, post GL, and register DebtorDebt.
    """
    provider = get_object_or_404(Provider.objects.select_for_update(), pk=provider_id)
    intended_paid = q3(paid_amount)
    bill = Bill(provider=provider, total=DEC0)
    bill.save()

    # ====== Add items & update stock ======
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

        qty_primary = qty_raw
        cf = getattr(product, "conversion_factor", None)
        if unit_idx == 2 and cf:
            qty_primary *= Decimal(str(cf))
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
            product.cost = cost_u1
            product.price = price_u1
            update_fields += ["cost", "price"]
        product.save(update_fields=list(dict.fromkeys(update_fields)))
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
    bill = (Bill.objects
            .select_for_update()
            .select_related('provider')
            .prefetch_related('items', 'items__product')
            .get(pk=bill_id))

    # Block if there are non-closed debtor entries
    from debts.models import DebtorDebt, DebtorPayment
    entry = DebtorDebt.objects.select_for_update().filter(
        source_app="billing", source_model="Bill", source_id=str(bill.id)
    ).first()
    if entry and entry.status != DebtorDebt.Status.CLOSED and (entry.paid_amount or 0) > 0:
        raise ValueError("cannot delete a bill with payments; refund/void first")

    # Reverse stock
    for it in bill.items.all():
        p = it.product
        p.stock_qty = (p.stock_qty or DEC0) - (it.qty_primary or DEC0)
        p.save(update_fields=["stock_qty"])

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
) -> ProviderReturn:
    """
    Create a ProviderReturn, decrease stock, post GL, and register CreditorDebt.
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

        # Stock decrease
        product.stock_qty = (product.stock_qty or DEC0) - qty_primary
        update_fields = ["stock_qty"]
        if hasattr(product, "updated_at"):
            from django.utils import timezone
            product.updated_at = timezone.now()
            update_fields.append("updated_at")
        product.save(update_fields=list(dict.fromkeys(update_fields)))
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