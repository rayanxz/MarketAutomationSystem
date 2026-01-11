# app/billing/services.py
from __future__ import annotations


from decimal import Decimal
from typing import Iterable, Dict, Any, Optional
from datetime import date

from django.db import transaction
from inventory.models import ProductMovement

from django.shortcuts import get_object_or_404

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
from debts import services as DebtSV  # NEW unified debts layer
from inventory import services as InvSV
from stock.models import ProductContainer

from audit_log.services import log_create, log_update, log_delete

from financials import services as FinSV
from financials.models import Counterparty, CounterpartyType, MoneyContainer, Receipt, ReceiptStatus




# ====== Decimals / helpers ======
DEC0 = Decimal("0")
DEC3 = Decimal("0.001")
DEC4 = Decimal("0.0001")

def q3(x: Decimal) -> Decimal:
    return (x or DEC0).quantize(DEC3)

def q4(x: Decimal) -> Decimal:
    return (x or DEC0).quantize(DEC4)

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

def _recalc_bill_currency_totals(*, bill: Bill) -> None:
    """
    Recalculate per-currency totals from bill items.
    Updates bill.total_syp / bill.total_usd and mirrors subtotals.
    """
    total_syp = DEC0
    total_usd = DEC0
    for it in bill.items.all():
        cur = (it.currency or "SYP").upper()
        amt = q3(it.line_total or DEC0)
        if cur == "USD":
            total_usd = q3(total_usd + amt)
        else:
            total_syp = q3(total_syp + amt)

    bill.total_syp = q3(total_syp)
    bill.total_usd = q3(total_usd)
    bill.subtotal_syp = bill.total_syp
    bill.subtotal_usd = bill.total_usd

def _ensure_provider_cp(*, provider: Provider) -> Counterparty:
    marker = f"[provider_id={provider.id}]"

    cp = Counterparty.objects.filter(
        type=CounterpartyType.PROVIDER,
        note__contains=marker,
    ).first()

    if cp:
        # optional: keep name synced
        if (cp.name or "").strip() != (provider.name or "").strip():
            cp.name = (provider.name or "").strip()
            cp.save(update_fields=["name"])
        return cp

    # fallback: create new
    cp = Counterparty.objects.create(
        type=CounterpartyType.PROVIDER,
        name=(provider.name or "").strip(),
        note=marker,
        is_active=True,
    )
    return cp



def _default_money_container() -> MoneyContainer:
    c = (
        MoneyContainer.objects
        .filter(is_active=True, container_type=MoneyContainer.ContainerType.DRAWER)
        .order_by("id")
        .first()
    )
    if not c:
        raise ValueError("No active cash drawer container found in financials")
    return c


# =======================================================================
# BILLS (Purchases) — MULTI CURRENCY, FX SNAPSHOT SAFE
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
    container: ProductContainer | None = None,
    money_container_id: int | None = None,
    settlement_currency: str = "SYP",   # 🔥 authoritative currency
    fx_usd_syp: Decimal | None = None,   # 🔥 snapshot FX (SYP per 1 USD)
) -> Bill:
    """
    Create a purchase Bill with multi-currency items.
    - Each BillItem has its own currency (SYP / USD)
    - Bill stores FX snapshot and per-currency subtotals
    - Financials posting happens ONLY in settlement_currency
    """

    # -------------------------------
    # Resolve stock container
    # -------------------------------
    if container is None:
        container = ProductContainer.objects.select_for_update().get(code="store")

    provider = get_object_or_404(
        Provider.objects.select_for_update(),
        pk=provider_id,
    )

    settlement_currency = (settlement_currency or "SYP").upper()
    if settlement_currency not in ("SYP", "USD"):
        raise ValueError("Invalid settlement currency")

    intended_paid = q3(paid_amount)
    items = list(items)

    fx_snapshot = Decimal(str(fx_usd_syp)) if fx_usd_syp is not None else None
    if fx_snapshot is None:
        fx_snapshot = q3(FinSV.get_current_fx_syp_per_usd())

    # -------------------------------
    # Create empty bill (authoritative shell)
    # -------------------------------
    bill = Bill.objects.create(
        provider=provider,
        created_by=actor,
        settlement_currency=settlement_currency,
        fx_usd_syp=fx_snapshot,
        fx_rate_usd_to_syp_used=fx_snapshot,
        subtotal_syp=DEC0,
        subtotal_usd=DEC0,
        grand_total_syp=DEC0,
        grand_total_usd=DEC0,
        total=DEC0,
    )

    # -------------------------------
    # Pre-lock products
    # -------------------------------
    prod_ids = [int(it["product_id"]) for it in items]
    products = {
        p.id: p
        for p in Product.objects.select_for_update().filter(id__in=prod_ids)
    }

    # -------------------------------
    # Process items
    # -------------------------------
    for idx, row in enumerate(items, start=1):
        pid = int(row["product_id"])
        product = products.get(pid) or get_object_or_404(
            Product.objects.select_for_update(), pk=pid
        )

        item_currency = (row.get("currency") or "").upper()
        if not item_currency:
            item_currency = (product.default_currency or "").upper()
        if not item_currency:
            item_currency = "SYP" if product.enable_syp else "USD"
        if item_currency not in ("SYP", "USD"):
            raise ValueError(f"Invalid currency at row {idx}")

        unit_idx = 2 if int(row.get("unit_index") or 1) == 2 else 1

        qty_raw = Decimal(str(row.get("qty_raw") or "0"))
        if qty_raw <= 0:
            raise ValueError(f"qty must be > 0 at row {idx}")

        cost_u1 = q4(Decimal(str(row.get("cost") or "0")))
        price_u1 = q4(Decimal(str(row.get("price") or "0")))

        total_override_raw = row.get("total_cost")
        total_override = (
            Decimal(str(total_override_raw))
            if total_override_raw not in (None, "")
            else None
        )

        # ---- convert to primary unit
        qty_primary = qty_raw
        cf = getattr(product, "conversion_factor", None)
        if unit_idx == 2 and cf:
            qty_primary *= Decimal(str(cf))
        qty_primary = q3(qty_primary)

        # ---- line total (in ITEM currency)
        line_total = (
            q3(total_override)
            if total_override and total_override > 0
            else q3(cost_u1 * qty_primary)
        )

        # ---- create bill item
        item = BillItem.objects.create(
            bill=bill,
            product=product,
            unit_index=unit_idx,
            qty_primary=qty_primary,
            cost=cost_u1,
            price=price_u1,
            line_total=line_total,
            currency=item_currency,   # 🔥 NEW FIELD
        )

        # ---- inventory movement (currency-agnostic)
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
            source_model="BillItem",
            source_id=str(item.id),
            container=container,
            extra_product_updates=extra_updates or None,
        )

    # -------------------------------
    # FX validation & totals
    # -------------------------------
    if fx_snapshot is None or fx_snapshot <= 0:
        raise ValueError("FX rate is required for multi-currency bills")

    _recalc_bill_currency_totals(bill=bill)

    bill.grand_total_syp = q3(
        bill.total_syp + (bill.total_usd * fx_snapshot)
    )
    bill.grand_total_usd = q3(
        bill.total_usd + (bill.total_syp / fx_snapshot)
    )

    # Legacy total remains SYP-based for backward compatibility.
    bill.total = bill.total_syp

    bill.save(update_fields=[
        "subtotal_syp",
        "subtotal_usd",
        "grand_total_syp",
        "grand_total_usd",
        "total_syp",
        "total_usd",
        "total",
        "fx_usd_syp",
        "fx_rate_usd_to_syp_used",
        "settlement_currency",
    ])

    # -------------------------------
    # Debts (UNCHANGED LOGIC)
    # -------------------------------
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

    # -------------------------------
    # Financials (settlement currency ONLY)
    # -------------------------------
    cp = _ensure_provider_cp(provider=provider)

    cash_container = (
        MoneyContainer.objects.select_for_update().get(pk=money_container_id)
        if money_container_id
        else _default_money_container()
    )

    r1 = FinSV.post_counterparty_adjust(
        actor=actor,
        counterparty_id=cp.id,
        currency_code=bill.settlement_currency,
        amount_signed=-q3(bill.total),
        note=f"فاتورة شراء #{bill.serial}",
        source_app="billing",
        source_model="Bill",
        source_id=str(bill.id),
    )

    r2 = None
    if q3(final_paid) > DEC0:
        r2 = FinSV.post_settlement(
            actor=actor,
            container_id=cash_container.id,
            counterparty_id=cp.id,
            currency_code=bill.settlement_currency,
            cash_amount_signed=-q3(final_paid),
            note=f"دفع على فاتورة شراء #{bill.serial}",
            source_app="billing",
            source_model="Bill",
            source_id=str(bill.id),
        )

    # -------------------------------
    # AUDIT
    # -------------------------------
    log_create(
        actor=actor,
        target=bill,
        title="Create purchase bill",
        message=f"Purchase bill #{bill.serial} provider={provider.name} total={bill.total}",
        meta={
            "kind": "billing.purchase_bill_created",
            "summary": {
                "bill_id": bill.id,
                "serial": bill.serial,
                "provider_id": provider.id,
                "provider_name": provider.name,
                "status": (status or "").lower(),
                "total": str(bill.total),
                "paid_amount": str(final_paid),
                "settlement_currency": bill.settlement_currency,
                "fx": str(bill.fx_usd_syp),
                "items_count": len(items),
            },
            "financials": {
                "receipt_ids": [r1.id] + ([r2.id] if r2 else []),
            },
        },
    )

    return bill


@transaction.atomic
def delete_bill(*, actor, bill_id: int) -> None:
    from debts.models import DebtorDebt, DebtorPayment
    from inventory.models import ProductMovement  # local import to avoid cycles

    # Lock bill + related rows
    bill = (
        Bill.objects
        .select_for_update()
        .select_related("provider")
        .prefetch_related("items", "items__product")
        .get(pk=bill_id)
    )

    # ====== AUDIT (snapshot before delete) ======
    before_bill = {
        "id": bill.id,
        "serial": bill.serial,
        "provider_id": bill.provider_id,
        "provider_name": bill.provider.name if bill.provider_id else "",
        "total": str(q3(bill.total or DEC0)),
        "items": [
            {"id": it.id, "product_id": it.product_id, "qty_primary": str(q3(it.qty_primary or DEC0)), "cost": str(q4(it.cost or DEC0))}
            for it in bill.items.all()
        ],
    }


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
    item_ids_str = [str(i) for i in item_ids]
    if item_ids:
        mv = (
            ProductMovement.objects
            .filter(
                source_app="billing",
                source_model="BillItem",
                source_id__in=item_ids_str,
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
    # 4) Reverse stock movements (FIFO-safe)
    # ==========================================
    from django.db.models import Sum
    from stock.models import StockFifoLayer
    from stock import services as StockSV

    touched_codes: set[str] = set()

    for it in bill.items.all():
        product = it.product

        # Find remaining FIFO qty for THIS BillItem, grouped per container
        qs = (
            StockFifoLayer.objects
            .select_for_update()
            .filter(
                product=product,
                source_app="billing",
                source_model="BillItem",
                source_id=str(it.id),
                qty_remaining__gt=DEC0,
            )
            .values("container_id")
            .annotate(qty=Sum("qty_remaining"))
        )

        rows = list(qs)

        container_ids = [r["container_id"] for r in rows if r["container_id"]]
        containers = {
            c.id: c
            for c in ProductContainer.objects.select_for_update().filter(id__in=container_ids)
        }

        

        for row in rows:
            cid = row["container_id"]
            qty_out = q3(row["qty"] or DEC0)
            if not cid or qty_out <= DEC0:
                continue

            cont = containers.get(cid)
            if cont is None:
                continue

            touched_codes.add(cont.code)

            # 1) FIFO FIRST: consume ONLY from this BillItem batch in this container
            eff_cost = StockSV.fifo_consume_scoped(
                product=product,
                container=cont,
                qty_out_primary=qty_out,
                scope_source_app="billing",
                scope_source_model="BillItem",
                scope_source_id=str(it.id),
            )

            # 2) THEN movement
            InvSV.record_movement(
                actor=actor,
                product=product,
                unit_index=int(it.unit_index),
                qty_primary=-qty_out,
                unit_cost=eff_cost,
                movement_type=ProductMovement.MovementType.PURCHASE_REVERSAL,
                source_app="billing",
                source_model="BillItem",
                source_id=str(it.id),
                container=cont,
                extra_product_updates=None,
                origin_source_app="billing",
                origin_source_model="BillItem",
                origin_source_id=str(it.id),

            )


        # Optional cleanup: delete zero FIFO layers for this bill item
        StockFifoLayer.objects.filter(
            product=product,
            source_app="billing",
            source_model="BillItem",
            source_id=str(it.id),
            qty_remaining__lte=DEC0,
        ).delete()

    
    # ==========================
    # 5) FINANCIALS reversal (replace ledger)
    # ==========================
    # Reverse any POSTED receipts created for this bill
    fin_qs = (
        Receipt.objects
        .select_for_update()
        .filter(
            source_app="billing",
            source_model="Bill",
            source_id=str(bill.id),
            status=ReceiptStatus.POSTED,
        )
        .order_by("-id")
    )

    for r in fin_qs:
        FinSV.reverse_receipt(
            actor=actor,
            receipt_id=r.id,
            reason_note=f"حذف فاتورة شراء #{bill.serial}",
        )

    # ==========================
    # 6) Delete debt + payments
    # ==========================
    if entry:
        DebtorPayment.objects.filter(entry=entry).delete()
        entry.delete()

    # ====== AUDIT ======
    log_delete(
        actor=actor,
        target=bill,
        title="Delete purchase bill",
        message=f"Deleted purchase bill #{bill.serial}",
        before=before_bill,
        meta={
            "kind": "billing.purchase_bill_deleted",
            "summary": {
                "bill_id": bill.id,
                "serial": bill.serial,
                "provider_id": bill.provider_id,
                "provider_name": bill.provider.name if bill.provider_id else "",
                "status": ("paid" if paid_amount >= total_amount and total_amount > 0 else ("unpaid" if paid_amount <= 0 else "partial")),
                "total": str(q3(total_amount)),
                "paid_amount": str(q3(paid_amount)),
                "items_count": bill.items.count(),
                "container": getattr(mv_container, "code", None) if mv_container else None,
            },
            "financials": {
                "reversed_receipt_ids": [r.id for r in fin_qs],
            },
            "touched_containers": sorted(touched_codes),
            }
    )


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
    container: ProductContainer | None = None,   
    source_bill_serial: int | None = None,
    money_container_id: int | None = None,
    currency_code: str = "SYP",
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
    
    items = list(items)

    # ✅ legacy mode needs a container (FIFO requires container)
    if container is None and not any((row.get("container_splits") or []) for row in items):
        container = ProductContainer.objects.select_for_update().get(code="store")
        # OR enforce strict:
        # raise ValueError("container is required in legacy return mode")

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

            bill_item_id = row.get("bill_item_id")
            if not bill_item_id:
                raise ValueError("bill_item_id مفقود في بيانات المرتجع (wizard).")

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
                    fifo_scope={  # ✅ enforce returning from SAME purchase bill item
                        "source_app": "billing",
                        "source_model": "BillItem",
                        "source_id": str(bill_item_id),
                    },
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

    # ====== FINANCIALS (replace ledger) ======
    cp = _ensure_provider_cp(provider=provider)

    cash_container = None
    if money_container_id:
        cash_container = MoneyContainer.objects.select_for_update().get(pk=money_container_id)
    else:
        cash_container = _default_money_container()

    # 1) receivable: provider owes store (positive)
    r1 = FinSV.post_counterparty_adjust(
        actor=actor,
        counterparty_id=cp.id,
        currency_code=currency_code,
        amount_signed=+q3(pret.total),
        note=f"مرتجع مورد #{pret.serial}",
        source_app="billing",
        source_model="ProviderReturn",
        source_id=str(pret.id),
    )

    # 2) settlement if collected now (cash enters drawer => positive)
    r2 = None
    if q3(final_collected) > DEC0:
        r2 = FinSV.post_settlement(
            actor=actor,
            container_id=cash_container.id,
            counterparty_id=cp.id,
            currency_code=currency_code,
            cash_amount_signed=+q3(final_collected),
            note=f"تحصيل على مرتجع مورد #{pret.serial}",
            source_app="billing",
            source_model="ProviderReturn",
            source_id=str(pret.id),
        )


    # ====== AUDIT (meta-driven UI) ======
    is_wizard = bool(any((row.get("container_splits") or []) for row in items))

    log_create(
        actor=actor,
        target=pret,
        title="Create provider return",
        message=f"Provider return #{pret.serial} provider={provider.name} total={pret.total}",
        meta={
            "kind": "billing.provider_return_created",
            "summary": {
                "return_id": pret.id,
                "serial": pret.serial,
                "provider_id": provider.id,
                "provider_name": provider.name,
                "status": (status or "").lower(),
                "total": str(q3(pret.total)),
                "collected_amount": str(q3(final_collected)),
                "items_count": len(items),
                "source_bill_serial": source_bill_serial,
                "legacy_container": (getattr(container, "code", None) if container else None),
                "wizard_mode": is_wizard,
            },
            "financials": {
                "currency": currency_code,
                "money_container_id": cash_container.id if cash_container else None,
                "receipt_ids": [r1.id] + ([r2.id] if r2 else []),
                "receipt_serials": [r1.serial] + ([r2.serial] if r2 else []),
            },

        },
        after={
            # optional: keep for debugging
            "serial": pret.serial,
            "provider_id": provider.id,
            "provider_name": provider.name,
            "status": (status or "").lower(),
            "collected_amount": str(final_collected),
            "total": str(pret.total),
            "source_bill_serial": source_bill_serial,
            "legacy_container": getattr(container, "code", None) if container else None,
            "wizard_mode": is_wizard,
        },
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

    log_update(
        actor=actor,
        target=bill,
        title="Pay purchase bill",
        message=f"Pay full for bill #{bill.serial}",
        meta={"bill_id": bill.id, "entry_id": entry.id, "mode": "full"},
    )


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

    log_update(
        actor=actor,
        target=bill,
        title="Pay purchase bill",
        message=f"Pay partial for bill #{bill.serial} amount={amt}",
        meta={"bill_id": bill.id, "entry_id": entry.id, "mode": "partial", "amount": str(amt)},
    )


    return bill



# =======================================================================
# RECEIVABLES (Provider Returns) – wrappers to debts layer
# =======================================================================

@transaction.atomic
def collect_full(*, actor, return_id: int) -> ProviderReturn:
    pret = ProviderReturn.objects.select_for_update().get(pk=return_id)
    entry, _ = CreditorDebt.objects.select_for_update().get_or_create(
        provider=pret.provider,
        source_app="billing",
        source_model="ProviderReturn",
        source_id=str(pret.id),
        defaults=dict(
            total=q3(pret.total or DEC0),
            collected=DEC0,
            status=CreditorDebt.Status.OPEN,
            party_type=PartyType.PROVIDER,
            party_name=pret.provider.name if pret.provider_id else "",
            doc_serial=pret.serial,
        ),
    )

    DebtSV.collect_debt(actor=actor, entry_id=entry.id, full=True)

    log_update(
        actor=actor,
        target=pret,
        title="Collect provider return",
        message=f"Collect full for return #{pret.serial}",
        meta={"return_id": pret.id, "entry_id": entry.id, "mode": "full"},
    )


    return pret

@transaction.atomic
def collect_partial(*, actor, return_id: int, amount: Decimal) -> ProviderReturn:
    amt = q3(amount or DEC0)
    if amt <= 0:
        raise ValueError("amount must be positive")

    pret = ProviderReturn.objects.select_for_update().get(pk=return_id)
    entry, _ = CreditorDebt.objects.select_for_update().get_or_create(
        provider=pret.provider,
        source_app="billing",
        source_model="ProviderReturn",
        source_id=str(pret.id),
        defaults=dict(
            total=q3(pret.total or DEC0),
            collected=DEC0,
            status=CreditorDebt.Status.OPEN,
            party_type=PartyType.PROVIDER,
            party_name=pret.provider.name if pret.provider_id else "",
            doc_serial=pret.serial,
        ),
    )

    if amt > q3(entry.remaining):
        raise ValueError(f"amount exceeds remaining ({q3(entry.remaining)})")

    DebtSV.collect_debt(actor=actor, entry_id=entry.id, amount=amt, full=False)

    log_update(
        actor=actor,
        target=pret,
        title="Collect provider return",
        message=f"Collect partial for return #{pret.serial} amount={amt}",
        meta={"return_id": pret.id, "entry_id": entry.id, "mode": "partial", "amount": str(amt)},
    )

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
