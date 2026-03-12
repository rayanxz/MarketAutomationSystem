# app/billing/services.py
from __future__ import annotations


from decimal import Decimal
from typing import Iterable, Dict, Any, Optional
from datetime import date

from django.db import transaction
from django.core.exceptions import ValidationError
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
    CreditorReceipt,
)

from catalog.models import Product
from debts import services as DebtSV  # NEW unified debts layer
from inventory import services as InvSV
from stock.models import ProductContainer

from audit_log.services import (
    log_create_safe as log_create,
    log_update_safe as log_update,
    log_delete_safe as log_delete,
)

from financials import services as FinSV
from financials.models import Counterparty, CounterpartyType, MoneyContainer, Receipt, ReceiptStatus, ReceiptKind, PostingLine, PostingTargetType




# ====== Decimals / helpers ======
DEC0 = Decimal("0")
DEC3 = Decimal("0.001")
DEC4 = Decimal("0.0001")

def q3(x: Decimal) -> Decimal:
    return (x or DEC0).quantize(DEC3)

def q4(x: Decimal) -> Decimal:
    return (x or DEC0).quantize(DEC4)

def _q_money(currency_code: str, amount: Decimal) -> Decimal:
    return FinSV.q_money(amount=Decimal(amount or DEC0), currency_code=(currency_code or "SYP").upper())


def _q_fx(value: Decimal) -> Decimal:
    return FinSV.q_fx(value)


def _resolve_paid_amount(status: str, intended_paid: Decimal, total: Decimal, *, currency_code: str) -> Decimal:
    """
    Normalize user intent vs. authoritative server logic.
    """
    s = (status or "").lower().strip()
    code = (currency_code or "SYP").upper()
    total = _q_money(code, total)
    intended = _q_money(code, intended_paid if intended_paid is not None else DEC0)
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
        amt = _q_money(cur, it.line_total or DEC0)
        if cur == "USD":
            total_usd = _q_money("USD", total_usd + amt)
        else:
            total_syp = _q_money("SYP", total_syp + amt)

    bill.total_syp = _q_money("SYP", total_syp)
    bill.total_usd = _q_money("USD", total_usd)
    bill.subtotal_syp = bill.total_syp
    bill.subtotal_usd = bill.total_usd

def _apply_container_balance_split(
    *,
    container: MoneyContainer,
    delta_syp: Decimal,
    delta_usd: Decimal,
) -> None:
    """
    Apply per-currency deltas to MoneyContainer balances.
    """
    if delta_syp:
        container.balance_syp = _q_money("SYP", (container.balance_syp or DEC0) + delta_syp)
    if delta_usd:
        container.balance_usd = _q_money("USD", (container.balance_usd or DEC0) + delta_usd)
    container.save(update_fields=["balance_syp", "balance_usd"])

def _ensure_provider_cp(*, provider: Provider) -> Counterparty:
    marker = f"[provider_id={provider.id}]"
    name = (provider.name or "").strip()

    cp = Counterparty.objects.filter(
        type=CounterpartyType.PROVIDER,
        provider_id=provider.id,
    ).order_by("id").first()
    if not cp:
        cp = Counterparty.objects.filter(
            type=CounterpartyType.PROVIDER,
            note__contains=marker,
        ).order_by("id").first()
        if cp and not cp.provider_id:
            cp.provider_id = provider.id
            cp.save(update_fields=["provider_id"])

    if cp:
        # optional: keep name synced
        updates = []
        if (cp.name or "").strip() != name:
            cp.name = name
            updates.append("name")
        if marker not in (cp.note or ""):
            cp.note = ((cp.note or "").strip() + ("\n" if (cp.note or "").strip() else "") + marker).strip()
            updates.append("note")
        if updates:
            cp.save(update_fields=updates)
        return cp

    cp, created = Counterparty.objects.get_or_create(
        type=CounterpartyType.PROVIDER,
        provider_id=provider.id,
        defaults={
            "name": name,
            "note": marker,
            "is_active": True,
        },
    )
    if not created:
        updates = []
        if (cp.name or "").strip() != name:
            cp.name = name
            updates.append("name")
        if marker not in (cp.note or ""):
            cp.note = ((cp.note or "").strip() + ("\n" if (cp.note or "").strip() else "") + marker).strip()
            updates.append("note")
        if updates:
            cp.save(update_fields=updates)
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


def _default_purchase_money_container() -> MoneyContainer:
    c = (
        MoneyContainer.objects
        .filter(
            is_active=True,
            features__code="purchase_bills",
            features__is_active=True,
        )
        .distinct()
        .order_by("id")
        .first()
    )
    if not c:
        raise ValueError("No active money container with purchase_bills feature found in financials")
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

    intended_paid = _q_money(settlement_currency, paid_amount)
    items = list(items)
    prod_ids = [int(it["product_id"]) for it in items]
    inactive_ids = list(
        Product.objects.filter(id__in=prod_ids, is_active=False).values_list("id", flat=True)
    )
    if inactive_ids:
        raise ValidationError("Product is archived and cannot be used in new operations.")

    fx_snapshot = Decimal(str(fx_usd_syp)) if fx_usd_syp is not None else None
    if fx_snapshot is None:
        fx_snapshot = FinSV.get_current_fx_syp_per_usd()
    fx_snapshot = _q_fx(fx_snapshot)

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
    products = {
        p.id: p
        for p in Product.objects.select_for_update().filter(id__in=prod_ids, is_active=True)
    }

    # -------------------------------
    # Process items
    # -------------------------------
    for idx, row in enumerate(items, start=1):
        pid = int(row["product_id"])
        product = products.get(pid)
        if product is None:
            raise ValidationError(f"Product {pid} is archived and cannot be used in new operations.")

        allow_syp_purch = getattr(product, "allow_syp_purchasing", product.enable_syp)
        allow_usd_purch = getattr(product, "allow_usd_purchasing", product.enable_usd)
        allow_syp_sales = getattr(product, "allow_syp_sales", product.enable_syp)
        allow_usd_sales = getattr(product, "allow_usd_sales", product.enable_usd)

        item_currency = (row.get("currency") or "").upper()
        if not item_currency:
            if hasattr(product, "get_effective_default_purchase_currency"):
                item_currency = product.get_effective_default_purchase_currency()
        if not item_currency:
            item_currency = "SYP" if allow_syp_purch else "USD"
        if item_currency not in ("SYP", "USD"):
            raise ValueError(f"Invalid currency at row {idx}")
        if item_currency == "SYP" and not allow_syp_purch:
            raise ValueError(f"SYP purchasing not enabled for product at row {idx}")
        if item_currency == "USD" and not allow_usd_purch:
            raise ValueError(f"USD purchasing not enabled for product at row {idx}")

        single_unit = bool(getattr(product, "is_single_unit", False))
        unit_idx = 1 if single_unit else (2 if int(row.get("unit_index") or 1) == 2 else 1)

        qty_raw = Decimal(str(row.get("qty_raw") or "0"))
        if qty_raw <= 0:
            raise ValueError(f"qty must be > 0 at row {idx}")

        cost_u1 = q4(Decimal(str(row.get("cost") or "0")))

        def _price_or_none(raw):
            if raw in (None, ""):
                return None
            return q4(Decimal(str(raw)))

        price_syp_val = _price_or_none(row.get("price_syp"))
        price_usd_val = _price_or_none(row.get("price_usd"))

        if price_syp_val is not None and not allow_syp_sales:
            raise ValueError(f"SYP sales not enabled for product at row {idx}")
        if price_usd_val is not None and not allow_usd_sales:
            raise ValueError(f"USD sales not enabled for product at row {idx}")

        price_fallback = price_usd_val if item_currency == "USD" else price_syp_val
        price_u1 = q4(Decimal(str(row.get("price") or price_fallback or "0")))

        total_override_raw = row.get("total_cost")
        total_override = (
            Decimal(str(total_override_raw))
            if total_override_raw not in (None, "")
            else None
        )

        # ---- convert to primary unit
        qty_primary = qty_raw
        cf = getattr(product, "conversion_factor", None)
        try:
            cf_val = Decimal(str(cf)) if cf else Decimal("1")
        except Exception:
            cf_val = Decimal("1")
        if cf_val <= 0:
            cf_val = Decimal("1")
        if single_unit:
            cf_val = Decimal("1")
        if unit_idx == 2:
            qty_primary *= cf_val
        qty_primary = q3(qty_primary)

        unit1_label = product.get_unit_primary_display() if getattr(product, "unit_primary", None) else ""
        unit2_label = "" if single_unit else (product.get_unit_secondary_display() if getattr(product, "unit_secondary", None) else "")

        # ---- line total (in ITEM currency)
        line_total_raw = (
            q3(total_override)
            if total_override and total_override > 0
            else q3(cost_u1 * qty_primary)
        )
        line_total = _q_money(item_currency, line_total_raw)

        # ---- create bill item
        item = BillItem.objects.create(
            bill=bill,
            product=product,
            product_name_at_txn=getattr(product, "name", "") or "",
            unit_index=unit_idx,
            conv_factor_at_txn=cf_val,
            unit_1_label_at_txn=unit1_label or "",
            unit_2_label_at_txn=unit2_label or "",
            qty_used_at_txn=qty_raw,
            qty_primary=qty_primary,
            cost=cost_u1,
            price=price_u1,
            line_total=line_total,
            currency=item_currency,   # 🔥 NEW FIELD
            fx_rate_at_txn=fx_snapshot,
        )

        # ---- inventory movement (currency-agnostic)
        extra_updates: Dict[str, Any] = {}

        if item_currency == "USD":
            extra_updates["latest_cost_usd"] = cost_u1
            extra_updates["default_cost_usd"] = cost_u1
            extra_updates["cost_usd"] = cost_u1
        else:
            extra_updates["latest_cost_syp"] = cost_u1
            extra_updates["default_cost_syp"] = cost_u1
            extra_updates["cost_syp"] = cost_u1

        if price_syp_val is not None:
            extra_updates["default_price_syp"] = price_syp_val
            extra_updates["price_syp"] = price_syp_val
        if price_usd_val is not None:
            extra_updates["default_price_usd"] = price_usd_val
            extra_updates["price_usd"] = price_usd_val

        if update_product_defaults:
            extra_updates["cost"] = cost_u1
            extra_updates["price"] = price_u1

        InvSV.record_purchase_item(
            actor=actor,
            product=product,
            unit_index=unit_idx,
            qty_primary=qty_primary,
            unit_cost=cost_u1,
            cost_currency=item_currency,
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

    # Grand totals include FX conversions (for reporting only).
    bill.grand_total_syp = _q_money("SYP", bill.total_syp + (bill.total_usd * fx_snapshot))
    bill.grand_total_usd = _q_money("USD", bill.total_usd + (bill.total_syp / fx_snapshot))

    # Legacy total remains SYP-only for backward compatibility.
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
    # Debts (per currency)
    # -------------------------------
    status_norm = (status or "").lower().strip()
    if bill.settlement_currency == "USD":
        paid_syp = DEC0
        paid_usd = _resolve_paid_amount(status, intended_paid, bill.total_usd, currency_code="USD")
    else:
        paid_syp = _resolve_paid_amount(status, intended_paid, bill.total_syp, currency_code="SYP")
        paid_usd = DEC0

    if status_norm == "paid":
        paid_syp = _q_money("SYP", bill.total_syp)
        paid_usd = _q_money("USD", bill.total_usd)

    settlement_total = bill.total_usd if bill.settlement_currency == "USD" else bill.total_syp
    final_paid = paid_usd if bill.settlement_currency == "USD" else paid_syp

    entry_syp = DebtSV.create_debtor_entry(
        provider=provider,
        total=bill.total_syp,
        paid_amount=paid_syp,
        source_app="billing",
        source_model="Bill",
        source_id=str(bill.id),
        currency_code="SYP",
        doc_serial=bill.serial,
    )

    entry_usd = None
    if bill.total_usd and bill.total_usd > DEC0:
        entry_usd = DebtSV.create_debtor_entry(
            provider=provider,
            total=bill.total_usd,
            paid_amount=paid_usd,
            source_app="billing",
            source_model="Bill",
            source_id=str(bill.id),
            currency_code="USD",
            doc_serial=bill.serial,
        )

    # -------------------------------
    # Financials (per-currency)
    # -------------------------------
    cp = _ensure_provider_cp(provider=provider)

    cash_container = (
        MoneyContainer.objects.select_for_update().get(pk=money_container_id)
        if money_container_id
        else _default_purchase_money_container()
    )
    if cash_container and bill.money_container_id != cash_container.id:
        bill.money_container = cash_container
        bill.save(update_fields=["money_container"])

    fx_for_receipts = _q_fx(bill.fx_rate_usd_to_syp_used or bill.fx_usd_syp or FinSV.get_current_fx_syp_per_usd())

    receipts = []

    # Counterparty adjust per currency
    if entry_syp and entry_syp.total > DEC0:
        receipts.append(FinSV.post_counterparty_adjust_with_fx(
            actor=actor,
            counterparty_id=cp.id,
            currency_code="SYP",
            amount_signed=-entry_syp.total,
            fx_syp_per_usd=fx_for_receipts,
            note=f"Purchase bill #{bill.serial} (SYP)",
            source_app="billing",
            source_model="Bill",
            source_id=str(bill.id),
        ))
    if entry_usd and entry_usd.total > DEC0:
        receipts.append(FinSV.post_counterparty_adjust_with_fx(
            actor=actor,
            counterparty_id=cp.id,
            currency_code="USD",
            amount_signed=-entry_usd.total,
            fx_syp_per_usd=fx_for_receipts,
            note=f"Purchase bill #{bill.serial} (USD)",
            source_app="billing",
            source_model="Bill",
            source_id=str(bill.id),
        ))

    # Settlement receipts per currency (paid amounts only)
    posted_paid_syp = _q_money("SYP", entry_syp.paid_amount if entry_syp else DEC0)
    posted_paid_usd = _q_money("USD", entry_usd.paid_amount if entry_usd else DEC0)
    if posted_paid_syp > DEC0:
        receipts.append(FinSV.post_settlement_with_fx(
            actor=actor,
            container_id=cash_container.id,
            counterparty_id=cp.id,
            currency_code="SYP",
            cash_amount_signed=-posted_paid_syp,
            fx_syp_per_usd=fx_for_receipts,
            note=f"Purchase bill payment #{bill.serial} (SYP)",
            source_app="billing",
            source_model="Bill",
            source_id=str(bill.id),
        ))
    if posted_paid_usd > DEC0:
        receipts.append(FinSV.post_settlement_with_fx(
            actor=actor,
            container_id=cash_container.id,
            counterparty_id=cp.id,
            currency_code="USD",
            cash_amount_signed=-posted_paid_usd,
            fx_syp_per_usd=fx_for_receipts,
            note=f"Purchase bill payment #{bill.serial} (USD)",
            source_app="billing",
            source_model="Bill",
            source_id=str(bill.id),
        ))

    # Link payment history to receipts (if any)
    if receipts:
        from debts.models import DebtorPayment
        entry_by_currency = {}
        entry_syp = DebtSV.resolve_debtor_entry_for_source(
            source_app="billing",
            source_model="Bill",
            source_id=str(bill.id),
            currency_code="SYP",
            for_update=True,
        )
        entry_usd = DebtSV.resolve_debtor_entry_for_source(
            source_app="billing",
            source_model="Bill",
            source_id=str(bill.id),
            currency_code="USD",
            for_update=True,
        )
        if entry_syp:
            entry_by_currency["SYP"] = entry_syp
        if entry_usd:
            entry_by_currency["USD"] = entry_usd
        for r in receipts:
            # only settlement receipts should create payment rows
            if r.kind != ReceiptKind.COUNTERPARTY_SETTLE:
                continue
            cur = None
            # infer currency from posting lines
            for ln in r.lines.all():
                if ln.target_type == PostingTargetType.CONTAINER:
                    cur = ln.currency.code
                    break
            cur = (cur or "SYP").upper()
            entry = entry_by_currency.get(cur)
            if not entry:
                continue
            amt = posted_paid_syp if cur == "SYP" else posted_paid_usd
            amt = _q_money(cur, amt)
            if amt <= DEC0:
                continue
            DebtorPayment.objects.create(
                entry=entry,
                amount=amt,
                currency_code=cur,
                receipt=r,
                money_container=cash_container,
                fx_syp_per_usd_used=fx_for_receipts,
            )

    # -------------------------------
    # AUDIT    # -------------------------------
    # AUDIT
    # -------------------------------
    log_create(
        actor=actor,
        target=bill,
        title="Create purchase bill",
        message=f"Purchase bill #{bill.serial} provider={provider.name} total={settlement_total}",
        meta={
            "kind": "billing.purchase_bill_created",
            "summary": {
                "bill_id": bill.id,
                "serial": bill.serial,
                "provider_id": provider.id,
                "provider_name": provider.name,
                "status": (status or "").lower(),
                "total": str(settlement_total),
                "total_syp": str(bill.total_syp),
                "total_usd": str(bill.total_usd),
                "paid_amount": str(final_paid),
                "settlement_currency": bill.settlement_currency,
                "fx": str(bill.fx_usd_syp),
                "items_count": len(items),
            },
            "financials": {
                "receipt_ids": [r.id for r in receipts],
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
        "total_syp": str(q3(getattr(bill, "total_syp", DEC0) or DEC0)),
        "total_usd": str(q3(getattr(bill, "total_usd", DEC0) or DEC0)),
        "items": [
            {"id": it.id, "product_id": it.product_id, "qty_primary": str(q3(it.qty_primary or DEC0)), "cost": str(q4(it.cost or DEC0))}
            for it in bill.items.all()
        ],
    }


    # ==========================
    # 1) Enforce "untouched" (FIFO + returns)
    # ==========================
    # Block deletion if a provider return references this bill.
    if ProviderReturn.objects.filter(source_bill_serial=bill.serial).exists():
        raise ValidationError("cannot delete a bill that has provider returns")

    # Only allow deletion if NO quantity from this bill was ever consumed in FIFO.
    _, _, untouched = get_bill_status_flags(bill)
    if not untouched:
        raise ValidationError("cannot delete a bill whose items were already sold/returned")

    # ==========================
    # 2) Locate Debtor entries (per currency)
    # ==========================
    entries = DebtSV.list_debtor_entries_for_source(
        source_app="billing",
        source_model="Bill",
        source_id=str(bill.id),
        for_update=True,
    )
    entry_syp = DebtSV.resolve_debtor_entry_for_source(
        source_app="billing",
        source_model="Bill",
        source_id=str(bill.id),
        currency_code="SYP",
        for_update=True,
    )
    entry_usd = DebtSV.resolve_debtor_entry_for_source(
        source_app="billing",
        source_model="Bill",
        source_id=str(bill.id),
        currency_code="USD",
        for_update=True,
    )

    paid_syp = _q_money("SYP", entry_syp.paid_amount if entry_syp and entry_syp.paid_amount is not None else DEC0)
    paid_usd = _q_money("USD", entry_usd.paid_amount if entry_usd and entry_usd.paid_amount is not None else DEC0)

    total_syp = _q_money("SYP", getattr(bill, "total_syp", DEC0) or DEC0)
    total_usd = _q_money("USD", getattr(bill, "total_usd", DEC0) or DEC0)
    total_amount = _q_money((bill.settlement_currency or "SYP"), bill.total or DEC0)

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
            conv_val = Decimal(str(getattr(it, "conv_factor_at_txn", None) or "1"))
            if not conv_val or conv_val <= 0:
                conv_val = Decimal("1")
            qty_used_val = qty_out if int(it.unit_index or 1) == 1 else q3(qty_out / conv_val)
            InvSV.record_movement(
                actor=actor,
                product=product,
                unit_index=int(it.unit_index),
                qty_primary=-qty_out,
                unit_cost=eff_cost,
                cost_currency=getattr(it, "currency", None),
                movement_type=ProductMovement.MovementType.PURCHASE_REVERSAL,
                source_app="billing",
                source_model="BillItem",
                source_id=str(it.id),
                container=cont,
                extra_product_updates=None,
                origin_source_app="billing",
                origin_source_model="BillItem",
                origin_source_id=str(it.id),
                product_name_at_txn=getattr(it, "product_name_at_txn", "") or "",
                qty_used_at_txn=qty_used_val,
                qty_primary_at_txn=-q3(qty_out),
                unit_index_used_at_txn=int(it.unit_index or 1),
                conversion_factor_at_txn=conv_val,
                unit_1_label_at_txn=getattr(it, "unit_1_label_at_txn", "") or "",
                unit_2_label_at_txn=getattr(it, "unit_2_label_at_txn", "") or "",

            )


        # Optional cleanup: delete zero FIFO layers for this bill item
        StockFifoLayer.objects.filter(
            product=product,
            source_app="billing",
            source_model="BillItem",
            source_id=str(it.id),
            qty_remaining__lte=DEC0,
        ).delete()

    # Remove original purchase movements tied to this bill
    if item_ids_str:
        ProductMovement.objects.filter(
            source_app="billing",
            source_model="BillItem",
            source_id__in=item_ids_str,
        ).delete()
    ProductMovement.objects.filter(
        source_app="billing",
        source_model="Bill",
        source_id=str(bill.id),
    ).delete()

    
    # ==========================
    # 5) FINANCIALS reversal (replace ledger)
    # ==========================
    # Reverse posted receipts linked to this bill, including payments posted later via debts.
    bill_fin_qs = (
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

    debt_payment_receipt_ids = list(
        DebtorPayment.objects
        .select_for_update()
        .filter(entry__in=entries, receipt__status=ReceiptStatus.POSTED)
        .exclude(receipt_id__isnull=True)
        .values_list("receipt_id", flat=True)
    )
    debt_payment_fin_qs = (
        Receipt.objects
        .select_for_update()
        .filter(id__in=debt_payment_receipt_ids, status=ReceiptStatus.POSTED)
        .order_by("-id")
    )

    receipts_to_reverse = list(bill_fin_qs)
    seen_receipt_ids = {r.id for r in receipts_to_reverse}
    for r in debt_payment_fin_qs:
        if r.id in seen_receipt_ids:
            continue
        receipts_to_reverse.append(r)
        seen_receipt_ids.add(r.id)

    reversed_receipt_ids: list[int] = []

    for r in receipts_to_reverse:
        FinSV.reverse_receipt(
            actor=actor,
            receipt_id=r.id,
            reason_note=f"حذف فاتورة شراء #{bill.serial}",
        )
        reversed_receipt_ids.append(r.id)


    # ==========================
    # 6) Delete debt + payments
    # ==========================
    if entries:
        DebtorPayment.objects.filter(entry__in=entries).delete()
        DebtorDebt.objects.filter(id__in=[e.id for e in entries]).delete()

    paid_amount_total = q3(paid_syp + paid_usd)
    if (total_syp > DEC0 or total_usd > DEC0) and paid_syp >= total_syp and paid_usd >= total_usd:
        status_label = "paid"
    elif paid_syp <= DEC0 and paid_usd <= DEC0:
        status_label = "unpaid"
    else:
        status_label = "partial"

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
                "status": status_label,
                "total": str(q3(total_amount)),
                "paid_amount": str(q3(paid_amount_total)),
                "items_count": bill.items.count(),
                "container": getattr(mv_container, "code", None) if mv_container else None,
            },
            "financials": {
                "reversed_receipt_ids": reversed_receipt_ids,
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
    valuation_mode: str = "HISTORICAL",
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

    settlement_currency = (currency_code or "SYP").upper()
    if settlement_currency not in ("SYP", "USD"):
        raise ValueError("Invalid settlement currency")

    valuation_mode_norm = (valuation_mode or "HISTORICAL").upper().strip()
    if valuation_mode_norm not in ("HISTORICAL", "CURRENT_FX"):
        raise ValueError("Invalid valuation mode")

    # legacy mode needs a container (FIFO requires container)
    if container is None and not any((row.get("container_splits") or []) for row in items):
        container = ProductContainer.objects.select_for_update().get(code="store")

    intended_paid = _q_money(settlement_currency, paid_amount)

    pret = ProviderReturn(
        provider=provider,
        total=DEC0,
        total_syp=DEC0,
        total_usd=DEC0,
        settlement_currency=settlement_currency,
        valuation_mode=valuation_mode_norm,
        source_bill_serial=source_bill_serial,
        created_by=actor,
    )
    pret.save()

    pret.initial_paid = intended_paid
    pret.initial_status = (status or "unpaid").lower()
    pret.save(update_fields=["initial_paid", "initial_status", "source_bill_serial"])

    # ----- Collect container codes (wizard mode) -----
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

    # ----- Process items -----
    total_syp = DEC0
    total_usd = DEC0
    settlement_total = DEC0
    conv_base_sum = DEC0
    conv_converted_sum = DEC0

    bill_item_ids = [int(row["bill_item_id"]) for row in items if row.get("bill_item_id")]
    bill_items = {}
    if bill_item_ids:
        bill_items = {
            it.id: it
            for it in BillItem.objects.select_related("bill", "product").select_for_update()
            .filter(id__in=bill_item_ids)
        }

    prod_ids = [int(it["product_id"]) for it in items]
    inactive_ids = list(
        Product.objects.filter(id__in=prod_ids, is_active=False).values_list("id", flat=True)
    )
    if inactive_ids:
        raise ValidationError("Product is archived and cannot be used in new operations.")
    products = {p.id: p for p in Product.objects.select_for_update().filter(id__in=prod_ids, is_active=True)}

    for idx, row in enumerate(items, start=1):
        pid = int(row["product_id"])
        product = products.get(pid)
        if product is None:
            raise ValidationError(f"Product {pid} is archived and cannot be used in new operations.")

        single_unit = bool(getattr(product, "is_single_unit", False))
        unit_idx = 1 if single_unit else (2 if int(row.get("unit_index") or 1) == 2 else 1)
        total_override_raw = row.get("total_cost")
        total_override = Decimal(str(total_override_raw)) if total_override_raw not in (None, "") else None

        cf = getattr(product, "conversion_factor", None)
        try:
            cf_val = Decimal(str(cf)) if cf else Decimal("1")
        except Exception:
            cf_val = Decimal("1")
        if cf_val <= 0:
            cf_val = Decimal("1")
        if single_unit:
            cf_val = Decimal("1")
        unit1_label = product.get_unit_primary_display() if getattr(product, "unit_primary", None) else ""
        unit2_label = "" if single_unit else (product.get_unit_secondary_display() if getattr(product, "unit_secondary", None) else "")

        container_splits = row.get("container_splits") or []
        bill_item_id = row.get("bill_item_id")
        bill_item = bill_items.get(int(bill_item_id)) if bill_item_id else None

        item_currency = (getattr(bill_item, "currency", None) or row.get("currency") or settlement_currency or "SYP").upper()
        if item_currency not in ("SYP", "USD"):
            raise ValueError(f"Invalid item currency at row {idx}")

        if bill_item is not None:
            cost_u1 = q4(Decimal(str(bill_item.cost or DEC0)))
        else:
            cost_u1 = q4(Decimal(str(row.get("cost") or "0")))

        # ----- determine qty_primary -----
        if container_splits:
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
            qty_raw = Decimal(str(row.get("qty_raw") or row.get("qty_primary") or "0"))
            if qty_raw <= 0:
                raise ValueError(f"qty must be > 0 at row {idx}")

            qty_primary = qty_raw
            if unit_idx == 2:
                qty_primary *= cf_val
            qty_primary = q3(qty_primary)

        # ----- line total & ProviderReturnItem -----
        line_total_raw = q3(total_override) if (total_override and total_override > 0) else q3(cost_u1 * qty_primary)
        line_total = _q_money(item_currency, line_total_raw)
        qty_used_val = abs(qty_primary) if unit_idx == 1 else q3(abs(qty_primary) / (cf_val or Decimal("1")))
        fx_used_for_item = None

        # ----- totals per currency -----
        if item_currency == "USD":
            total_usd = _q_money("USD", total_usd + line_total)
        else:
            total_syp = _q_money("SYP", total_syp + line_total)

        # ----- settlement total (with FX if needed) -----
        if item_currency == settlement_currency:
            settlement_total = _q_money(settlement_currency, settlement_total + line_total)
        else:
            fx_used = None
            if valuation_mode_norm == "HISTORICAL":
                if bill_item is not None:
                    b = getattr(bill_item, "bill", None)
                    fx_used = getattr(b, "fx_rate_usd_to_syp_used", None) or getattr(b, "fx_usd_syp", None)
            if fx_used is None:
                fx_used = FinSV.get_current_fx_syp_per_usd()

            fx_used = _q_fx(Decimal(str(fx_used)))
            if fx_used <= 0:
                raise ValueError("FX rate is required for return valuation")
            fx_used_for_item = fx_used

            if settlement_currency == "SYP" and item_currency == "USD":
                converted = _q_money("SYP", line_total * fx_used)
                settlement_total = _q_money("SYP", settlement_total + converted)
                conv_base_sum = _q_money("USD", conv_base_sum + line_total)
                conv_converted_sum = _q_money("SYP", conv_converted_sum + converted)
            elif settlement_currency == "USD" and item_currency == "SYP":
                converted = _q_money("USD", line_total / fx_used)
                settlement_total = _q_money("USD", settlement_total + converted)
                conv_base_sum = _q_money("SYP", conv_base_sum + line_total)
                conv_converted_sum = _q_money("USD", conv_converted_sum + converted)

        # fix indentation error around ProviderReturnItem insertion
        ProviderReturnItem.objects.create(
            ret=pret,
            product=product,
            product_name_at_txn=getattr(product, "name", "") or "",
            unit_index=unit_idx,
            conv_factor_at_txn=cf_val,
            unit_1_label_at_txn=unit1_label or "",
            unit_2_label_at_txn=unit2_label or "",
            qty_used_at_txn=qty_used_val,
            qty_primary=qty_primary,
            currency=item_currency,
            cost=cost_u1,
            line_total=line_total,
            fx_rate_at_txn=fx_used_for_item,
        )

        # ----- Inventory movements -----
        if container_splits:
            if not bill_item_id:
                raise ValueError("bill_item_id is required for wizard returns.")

            for split in container_splits:
                q_split = Decimal(str(split.get("qty_primary") or "0"))
                if q_split <= DEC0:
                    continue

                code = (split.get("code") or "").strip().lower()
                cont = containers_by_code.get(code)
                if cont is None:
                    raise ValueError("invalid container code in return splits")
                if not cont.is_active:
                    raise ValueError("inactive container code in return splits")

                InvSV.record_provider_return_item(
                    actor=actor,
                    product=product,
                    unit_index=unit_idx,
                    qty_primary=-q3(q_split),
                    unit_cost=cost_u1,
                    cost_currency=item_currency,
                    source_app="billing",
                    source_model="ProviderReturn",
                    source_id=pret.id,
                    container=cont,
                    fifo_scope={
                        "source_app": "billing",
                        "source_model": "BillItem",
                        "source_id": str(bill_item_id),
                    },
                )
        else:
            InvSV.record_provider_return_item(
                actor=actor,
                product=product,
                unit_index=unit_idx,
                qty_primary=-qty_primary,
                unit_cost=cost_u1,
                cost_currency=item_currency,
                source_app="billing",
                source_model="ProviderReturn",
                source_id=pret.id,
                container=container,
            )

        # ----- Product latest cost update -----
        if item_currency == "USD":
            product.latest_cost_usd = cost_u1
            product.save(update_fields=["latest_cost_usd"])
        else:
            product.latest_cost_syp = cost_u1
            product.save(update_fields=["latest_cost_syp"])

    pret.total_syp = _q_money("SYP", total_syp)
    pret.total_usd = _q_money("USD", total_usd)
    pret.total = _q_money(settlement_currency, settlement_total)

    fx_rate_used = None
    if conv_base_sum > DEC0 and conv_converted_sum > DEC0:
        fx_rate_used = _q_fx(conv_converted_sum / conv_base_sum)

    pret.fx_rate_used = fx_rate_used
    pret.save(update_fields=["total", "total_syp", "total_usd", "fx_rate_used", "settlement_currency", "valuation_mode"])

    # ----- Create debt -----
    status_norm = (status or "").lower().strip()
    final_collected = _resolve_paid_amount(status, intended_paid, pret.total, currency_code=settlement_currency)
    if status_norm == "paid":
        final_collected = pret.total

    if status_norm != "paid":
        if settlement_currency == "USD":
            if final_collected > pret.total_usd:
                raise ValueError("Paid amount exceeds USD total for this return.")
        else:
            if final_collected > pret.total_syp:
                raise ValueError("Paid amount exceeds SYP total for this return.")

    collected_syp = DEC0
    collected_usd = DEC0
    if status_norm == "paid":
        collected_syp = pret.total_syp
        collected_usd = pret.total_usd
    else:
        if settlement_currency == "USD":
            collected_usd = _q_money("USD", final_collected)
        else:
            collected_syp = _q_money("SYP", final_collected)

    entry_syp = DebtSV.create_creditor_entry(
        provider=provider,
        total=pret.total_syp,
        collected=collected_syp,
        source_app="billing",
        source_model="ProviderReturn",
        source_id=str(pret.id),
        currency_code="SYP",
        doc_serial=pret.serial,
    )
    entry_usd = None
    if pret.total_usd and pret.total_usd > DEC0:
        entry_usd = DebtSV.create_creditor_entry(
            provider=provider,
            total=pret.total_usd,
            collected=collected_usd,
            source_app="billing",
            source_model="ProviderReturn",
            source_id=str(pret.id),
            currency_code="USD",
            doc_serial=pret.serial,
        )

    # ----- FINANCIALS -----
    cp = _ensure_provider_cp(provider=provider)

    posted_collected_syp = _q_money("SYP", entry_syp.collected if entry_syp else DEC0)
    posted_collected_usd = _q_money("USD", entry_usd.collected if entry_usd else DEC0)
    has_any_collection = (posted_collected_syp > DEC0) or (posted_collected_usd > DEC0)

    cash_container = None
    if has_any_collection:
        if not money_container_id:
            raise ValueError("money container is required for paid returns")
        cash_container = MoneyContainer.objects.select_for_update().get(pk=money_container_id)
    else:
        cash_container = _default_money_container()

    fx_for_receipt = _q_fx(pret.fx_rate_used or FinSV.get_current_fx_syp_per_usd())
    receipts: list[Receipt] = []

    # Counterparty adjustment must reflect authoritative per-currency debt totals.
    if entry_syp and entry_syp.total > DEC0:
        receipts.append(
            FinSV.post_counterparty_adjust_with_fx(
                actor=actor,
                counterparty_id=cp.id,
                currency_code="SYP",
                amount_signed=+_q_money("SYP", entry_syp.total),
                fx_syp_per_usd=fx_for_receipt,
                note=f"Provider return #{pret.serial} (SYP)",
                source_app="billing",
                source_model="ProviderReturn",
                source_id=str(pret.id),
            )
        )
    if entry_usd and entry_usd.total > DEC0:
        receipts.append(
            FinSV.post_counterparty_adjust_with_fx(
                actor=actor,
                counterparty_id=cp.id,
                currency_code="USD",
                amount_signed=+_q_money("USD", entry_usd.total),
                fx_syp_per_usd=fx_for_receipt,
                note=f"Provider return #{pret.serial} (USD)",
                source_app="billing",
                source_model="ProviderReturn",
                source_id=str(pret.id),
            )
        )

    # Settlement receipts must also stay currency-aligned.
    settlement_receipt_syp = None
    settlement_receipt_usd = None
    if posted_collected_syp > DEC0:
        settlement_receipt_syp = FinSV.post_settlement_with_fx(
            actor=actor,
            container_id=cash_container.id,
            counterparty_id=cp.id,
            currency_code="SYP",
            cash_amount_signed=+posted_collected_syp,
            fx_syp_per_usd=fx_for_receipt,
            note=f"Provider return settlement #{pret.serial} (SYP)",
            source_app="billing",
            source_model="ProviderReturn",
            source_id=str(pret.id),
        )
        receipts.append(settlement_receipt_syp)
    if posted_collected_usd > DEC0:
        settlement_receipt_usd = FinSV.post_settlement_with_fx(
            actor=actor,
            container_id=cash_container.id,
            counterparty_id=cp.id,
            currency_code="USD",
            cash_amount_signed=+posted_collected_usd,
            fx_syp_per_usd=fx_for_receipt,
            note=f"Provider return settlement #{pret.serial} (USD)",
            source_app="billing",
            source_model="ProviderReturn",
            source_id=str(pret.id),
        )
        receipts.append(settlement_receipt_usd)

    # Link collection history rows to their matching per-currency settlement receipts.
    entry_by_currency = {}
    entry_syp = DebtSV.resolve_creditor_entry_for_source(
        source_app="billing",
        source_model="ProviderReturn",
        source_id=str(pret.id),
        currency_code="SYP",
        for_update=True,
    )
    entry_usd = DebtSV.resolve_creditor_entry_for_source(
        source_app="billing",
        source_model="ProviderReturn",
        source_id=str(pret.id),
        currency_code="USD",
        for_update=True,
    )
    if entry_syp:
        entry_by_currency["SYP"] = entry_syp
    if entry_usd:
        entry_by_currency["USD"] = entry_usd

    if settlement_receipt_syp and posted_collected_syp > DEC0:
        entry = entry_by_currency.get("SYP")
        if entry:
            CreditorReceipt.objects.create(
                entry=entry,
                amount=posted_collected_syp,
                currency_code="SYP",
                receipt=settlement_receipt_syp,
                money_container=cash_container,
                fx_syp_per_usd_used=fx_for_receipt,
            )
    if settlement_receipt_usd and posted_collected_usd > DEC0:
        entry = entry_by_currency.get("USD")
        if entry:
            CreditorReceipt.objects.create(
                entry=entry,
                amount=posted_collected_usd,
                currency_code="USD",
                receipt=settlement_receipt_usd,
                money_container=cash_container,
                fx_syp_per_usd_used=fx_for_receipt,
            )

    # ----- AUDIT -----
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
                "total_syp": str(q3(pret.total_syp)),
                "total_usd": str(q3(pret.total_usd)),
                "collected_amount": str(q3(final_collected)),
                "items_count": len(items),
                "source_bill_serial": source_bill_serial,
                "settlement_currency": settlement_currency,
                "valuation_mode": valuation_mode_norm,
                "fx_rate_used": str(pret.fx_rate_used) if pret.fx_rate_used else None,
                "legacy_container": (getattr(container, "code", None) if container else None),
                "wizard_mode": is_wizard,
            },
            "financials": {
                "currency": settlement_currency,
                "money_container_id": cash_container.id if cash_container else None,
                "receipt_ids": [r.id for r in receipts],
                "receipt_serials": [r.serial for r in receipts],
            },
        },
        after={
            "serial": pret.serial,
            "provider_id": provider.id,
            "provider_name": provider.name,
            "status": (status or "").lower(),
            "collected_amount": str(final_collected),
            "total": str(pret.total),
            "total_syp": str(pret.total_syp),
            "total_usd": str(pret.total_usd),
            "source_bill_serial": source_bill_serial,
            "settlement_currency": settlement_currency,
            "valuation_mode": valuation_mode_norm,
            "fx_rate_used": str(pret.fx_rate_used) if pret.fx_rate_used else None,
            "legacy_container": getattr(container, "code", None) if container else None,
            "wizard_mode": is_wizard,
        },
    )

    return pret


@transaction.atomic
def delete_return(*, actor, return_id: int) -> None:
    from debts.models import CreditorDebt, CreditorReceipt
    from stock import services as StockSV

    pret = (
        ProviderReturn.objects
        .select_for_update()
        .select_related("provider")
        .prefetch_related("items", "items__product")
        .get(pk=return_id)
    )

    before_ret = {
        "id": pret.id,
        "serial": pret.serial,
        "provider_id": pret.provider_id,
        "provider_name": pret.provider.name if pret.provider_id else "",
        "total": str(q3(pret.total or DEC0)),
        "total_syp": str(q3(getattr(pret, "total_syp", DEC0) or DEC0)),
        "total_usd": str(q3(getattr(pret, "total_usd", DEC0) or DEC0)),
        "items": [
            {"id": it.id, "product_id": it.product_id, "qty_primary": str(q3(it.qty_primary or DEC0)), "cost": str(q4(it.cost or DEC0))}
            for it in pret.items.all()
        ],
    }

    # ----- Reverse stock movements (FIFO-safe) -----
    mvs = (
        ProductMovement.objects
        .select_for_update()
        .select_related("product", "container")
        .filter(
            source_app="billing",
            source_model="ProviderReturn",
            source_id=str(pret.id),
        )
    )

    bill_item_ids = [mv.origin_source_id for mv in mvs if mv.origin_source_model == "BillItem" and mv.origin_source_id]
    bill_items = {}
    if bill_item_ids:
        bill_items = {
            it.id: it
            for it in BillItem.objects.select_for_update().filter(id__in=bill_item_ids)
        }

    for mv in mvs:
        if not mv.container_id:
            continue
        qty_in = q3(abs(mv.qty_primary or DEC0))
        if qty_in <= DEC0:
            continue

        cost_currency = (pret.settlement_currency or "SYP")
        if mv.origin_source_model == "BillItem":
            try:
                bid = int(mv.origin_source_id or 0)
                bi = bill_items.get(bid)
                if bi and getattr(bi, "currency", None):
                    cost_currency = bi.currency
            except Exception:
                pass

        # restore FIFO layer first
        StockSV.fifo_add_incoming(
            product=mv.product,
            container=mv.container,
            qty_primary=qty_in,
            unit_cost=mv.unit_cost,
            cost_currency=cost_currency,
            source_app=(mv.origin_source_app or mv.source_app),
            source_model=(mv.origin_source_model or mv.source_model),
            source_id=(mv.origin_source_id or mv.source_id),
        )

        # then record reversal movement
        conv_val = Decimal(str(getattr(mv, "conversion_factor_at_txn", None) or "1"))
        if not conv_val or conv_val <= 0:
            conv_val = Decimal("1")
        qty_used_val = qty_in if int(mv.unit_index or 1) == 1 else q3(qty_in / conv_val)
        InvSV.record_movement(
            actor=actor,
            product=mv.product,
            unit_index=int(mv.unit_index),
            qty_primary=qty_in,
            unit_cost=mv.unit_cost,
            cost_currency=getattr(mv, "cost_currency_at_txn", None),
            movement_type=ProductMovement.MovementType.PROVIDER_RETURN_REVERSAL,
            source_app="billing",
            source_model="ProviderReturn",
            source_id=str(pret.id),
            container=mv.container,
            extra_product_updates=None,
            origin_source_app=(mv.origin_source_app or ""),
            origin_source_model=(mv.origin_source_model or ""),
            origin_source_id=(mv.origin_source_id or ""),
            product_name_at_txn=getattr(mv, "product_name_at_txn", "") or getattr(mv.product, "name", ""),
            qty_used_at_txn=qty_used_val,
            qty_primary_at_txn=qty_in,
            unit_index_used_at_txn=int(mv.unit_index or 1),
            conversion_factor_at_txn=conv_val,
            unit_1_label_at_txn=getattr(mv, "unit_1_label_at_txn", "") or "",
            unit_2_label_at_txn=getattr(mv, "unit_2_label_at_txn", "") or "",
        )

    entries = DebtSV.list_creditor_entries_for_source(
        source_app="billing",
        source_model="ProviderReturn",
        source_id=str(pret.id),
        currency_code=None,
        for_update=True,
    )
    # ----- FINANCIALS reversal -----
    billing_fin_qs = (
        Receipt.objects
        .select_for_update()
        .filter(
            source_app="billing",
            source_model="ProviderReturn",
            source_id=str(pret.id),
            status=ReceiptStatus.POSTED,
        )
        .order_by("-id")
    )

    debt_collection_receipt_ids = list(
        CreditorReceipt.objects
        .select_for_update()
        .filter(entry__in=entries, receipt__status=ReceiptStatus.POSTED)
        .exclude(receipt_id__isnull=True)
        .values_list("receipt_id", flat=True)
    )
    debt_collection_fin_qs = (
        Receipt.objects
        .select_for_update()
        .filter(id__in=debt_collection_receipt_ids, status=ReceiptStatus.POSTED)
        .order_by("-id")
    )

    receipts_to_reverse = list(billing_fin_qs)
    seen_receipt_ids = {r.id for r in receipts_to_reverse}
    for r in debt_collection_fin_qs:
        if r.id in seen_receipt_ids:
            continue
        receipts_to_reverse.append(r)
        seen_receipt_ids.add(r.id)

    reversed_receipt_ids: list[int] = []
    for r in receipts_to_reverse:
        FinSV.reverse_receipt(
            actor=actor,
            receipt_id=r.id,
            reason_note=f"Reverse provider return #{pret.serial}",
        )
        reversed_receipt_ids.append(r.id)

    # ----- Delete creditor entries/receipts -----
    CreditorReceipt.objects.filter(entry__in=entries).delete()
    CreditorDebt.objects.filter(id__in=[e.id for e in entries]).delete()

    log_delete(
        actor=actor,
        target=pret,
        title="Delete provider return",
        message=f"Deleted provider return #{pret.serial}",
        before=before_ret,
        meta={
            "kind": "billing.provider_return_deleted",
            "summary": {
                "return_id": pret.id,
                "serial": pret.serial,
                "provider_id": pret.provider_id,
                "provider_name": pret.provider.name if pret.provider_id else "",
                "total": str(q3(pret.total or DEC0)),
                "total_syp": str(q3(getattr(pret, "total_syp", DEC0) or DEC0)),
                "total_usd": str(q3(getattr(pret, "total_usd", DEC0) or DEC0)),
                "items_count": pret.items.count(),
            },
            "financials": {
                "reversed_receipt_ids": reversed_receipt_ids,
            },
        },
    )

    pret.delete()


def _normalize_supported_currency(currency_code: str) -> str:
    cur = (currency_code or "SYP").upper()
    if cur not in ("SYP", "USD"):
        raise ValueError("Invalid currency")
    return cur


def _resolve_or_create_bill_debtor_entry(*, bill: Bill, currency_code: str) -> DebtorDebt:
    entry = DebtSV.resolve_debtor_entry_for_source(
        source_app="billing",
        source_model="Bill",
        source_id=str(bill.id),
        currency_code=currency_code,
        for_update=True,
    )
    if entry:
        return entry

    total_amt = _q_money(currency_code, bill.total_usd if currency_code == "USD" else bill.total_syp)
    return DebtorDebt.objects.create(
        provider=bill.provider,
        source_app="billing",
        source_model="Bill",
        source_id=str(bill.id),
        total=total_amt,
        paid_amount=DEC0,
        status=DebtorDebt.Status.OPEN,
        party_type=PartyType.PROVIDER,
        party_name=bill.provider.name if bill.provider_id else "",
        doc_serial=bill.serial,
        currency_code=currency_code,
    )


def _resolve_or_create_return_creditor_entry(*, pret: ProviderReturn, currency_code: str) -> CreditorDebt:
    entry = DebtSV.resolve_creditor_entry_for_source(
        source_app="billing",
        source_model="ProviderReturn",
        source_id=str(pret.id),
        currency_code=currency_code,
        for_update=True,
    )
    if entry:
        return entry

    total_amt = _q_money(currency_code, pret.total_usd if currency_code == "USD" else pret.total_syp)
    return CreditorDebt.objects.create(
        provider=pret.provider,
        source_app="billing",
        source_model="ProviderReturn",
        source_id=str(pret.id),
        total=total_amt,
        collected=DEC0,
        status=CreditorDebt.Status.OPEN,
        party_type=PartyType.PROVIDER,
        party_name=pret.provider.name if pret.provider_id else "",
        doc_serial=pret.serial,
        currency_code=currency_code,
    )

@transaction.atomic
def pay_full(*, actor, bill_id: int, money_container_id: Optional[int] = None, currency_code: str = "SYP") -> Bill:
    bill = Bill.objects.select_for_update().get(pk=bill_id)
    cur = _normalize_supported_currency(currency_code)

    entry = _resolve_or_create_bill_debtor_entry(bill=bill, currency_code=cur)

    if not money_container_id:
        money_container_id = bill.money_container_id
    if not money_container_id:
        raise ValueError("money_container_id is required")

    DebtSV.pay_debt(
        actor=actor,
        entry_id=entry.id,
        full=True,
        money_container_id=money_container_id,
        currency_code=cur,
    )

    log_update(
        actor=actor,
        target=bill,
        title="Pay purchase bill",
        message=f"Pay full for bill #{bill.serial} ({cur})",
        meta={"bill_id": bill.id, "entry_id": entry.id, "mode": "full", "currency": cur},
    )

    return bill


@transaction.atomic
def pay_partial(*, actor, bill_id: int, amount: Decimal, money_container_id: Optional[int] = None, currency_code: str = "SYP") -> Bill:
    bill = Bill.objects.select_for_update().get(pk=bill_id)
    cur = _normalize_supported_currency(currency_code)
    amt = _q_money(cur, amount or DEC0)
    if amt <= 0:
        raise ValueError("amount must be positive")

    entry = _resolve_or_create_bill_debtor_entry(bill=bill, currency_code=cur)

    remaining = _q_money(cur, entry.remaining)
    if amt > remaining:
        raise ValueError(f"amount exceeds remaining ({remaining})")

    if not money_container_id:
        money_container_id = bill.money_container_id
    if not money_container_id:
        raise ValueError("money_container_id is required")

    DebtSV.pay_debt(
        actor=actor,
        entry_id=entry.id,
        amount=amt,
        full=False,
        money_container_id=money_container_id,
        currency_code=cur,
    )

    log_update(
        actor=actor,
        target=bill,
        title="Pay purchase bill",
        message=f"Pay partial for bill #{bill.serial} amount={amt} ({cur})",
        meta={"bill_id": bill.id, "entry_id": entry.id, "mode": "partial", "amount": str(amt), "currency": cur},
    )

    return bill


@transaction.atomic
def collect_full(*, actor, return_id: int, money_container_id: Optional[int] = None, currency_code: str = "SYP") -> ProviderReturn:
    pret = ProviderReturn.objects.select_for_update().get(pk=return_id)
    cur = _normalize_supported_currency(currency_code)

    entry = _resolve_or_create_return_creditor_entry(pret=pret, currency_code=cur)

    if not money_container_id:
        money_container_id = getattr(pret, "money_container_id", None)
    if not money_container_id:
        raise ValueError("money_container_id is required")

    DebtSV.collect_debt(
        actor=actor,
        entry_id=entry.id,
        full=True,
        money_container_id=money_container_id,
        currency_code=cur,
    )

    log_update(
        actor=actor,
        target=pret,
        title="Collect provider return",
        message=f"Collect full for return #{pret.serial} ({cur})",
        meta={"return_id": pret.id, "entry_id": entry.id, "mode": "full", "currency": cur},
    )

    return pret

@transaction.atomic
def collect_partial(*, actor, return_id: int, amount: Decimal, money_container_id: Optional[int] = None, currency_code: str = "SYP") -> ProviderReturn:
    pret = ProviderReturn.objects.select_for_update().get(pk=return_id)
    cur = _normalize_supported_currency(currency_code)
    amt = _q_money(cur, amount or DEC0)
    if amt <= 0:
        raise ValueError("amount must be positive")

    entry = _resolve_or_create_return_creditor_entry(pret=pret, currency_code=cur)

    remaining = _q_money(cur, entry.remaining)
    if amt > remaining:
        raise ValueError(f"amount exceeds remaining ({remaining})")

    if not money_container_id:
        money_container_id = getattr(pret, "money_container_id", None)
    if not money_container_id:
        raise ValueError("money_container_id is required")

    DebtSV.collect_debt(
        actor=actor,
        entry_id=entry.id,
        amount=amt,
        full=False,
        money_container_id=money_container_id,
        currency_code=cur,
    )

    log_update(
        actor=actor,
        target=pret,
        title="Collect provider return",
        message=f"Collect partial for return #{pret.serial} amount={amt} ({cur})",
        meta={"return_id": pret.id, "entry_id": entry.id, "mode": "partial", "amount": str(amt), "currency": cur},
    )

    return pret

@transaction.atomic
def pay_manual_debt_full(*, actor, entry_id: int) -> DebtorDebt:
    return DebtSV.pay_debt(actor=actor, entry_id=entry_id, full=True)

@transaction.atomic
def pay_manual_debt_partial(*, actor, entry_id: int, amount: Decimal) -> DebtorDebt:
    entry = DebtorDebt.objects.get(pk=entry_id)
    amt = _q_money((entry.currency_code or "SYP"), amount or DEC0)
    if amt <= 0:
        raise ValueError("amount must be positive")
    return DebtSV.pay_debt(actor=actor, entry_id=entry_id, amount=amt, full=False)

@transaction.atomic
def collect_manual_debt_full(*, actor, entry_id: int) -> CreditorDebt:
    return DebtSV.collect_debt(actor=actor, entry_id=entry_id, full=True)

@transaction.atomic
def collect_manual_debt_partial(*, actor, entry_id: int, amount: Decimal) -> CreditorDebt:
    entry = CreditorDebt.objects.get(pk=entry_id)
    amt = _q_money((entry.currency_code or "SYP"), amount or DEC0)
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
