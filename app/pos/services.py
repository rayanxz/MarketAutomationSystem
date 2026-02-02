# app/pos/services.py
from __future__ import annotations

from decimal import Decimal
from collections import defaultdict
from typing import Tuple

from django.db import transaction
from django.utils import timezone

from catalog.models import Product
from inventory import services as InvSV
from inventory.models import ProductMovement, DEC0, q3
from stock.models import ProductContainer, StockEntry  # ⬅ added StockEntry
from .models import SalesBill, SalesBillRow, PosDay, PosLoginSession , PosShift
from core.currency import SYP, USD
from financials import services as FinSV
from financials.models import Counterparty, CounterpartyType


class InsufficientStockError(Exception):
    """
    Raised when trying to finalize a POS bill that would cause stock
    in the store container to go below zero.

    `items` is a list of dicts:
      {
        "product_id": int,
        "product_name": str,
        "needed": Decimal,
        "available": Decimal,
      }
    """
    def __init__(self, items: list[dict]):
        self.items = items
        super().__init__("INSUFFICIENT_STOCK")


def _ensure_customer_counterparty(*, customer) -> Counterparty:
    cp = Counterparty.objects.filter(type=CounterpartyType.CUSTOMER, customer_id=customer.id).first()
    if cp:
        if (cp.name or "").strip() != (customer.name or "").strip():
            cp.name = (customer.name or "").strip()
            cp.save(update_fields=["name"])
        return cp
    return Counterparty.objects.create(
        type=CounterpartyType.CUSTOMER,
        name=(customer.name or "").strip(),
        customer_id=customer.id,
        is_active=True,
    )


def get_or_create_work_day(now=None) -> PosDay:
    """
    Ensure we have a PosDay for the current local calendar date.
    """
    if now is None:
        now = timezone.now()
    today = timezone.localdate(now)
    day, _ = PosDay.objects.get_or_create(
        date=today,
        defaults={"opened_at": now},
    )
    return day


def get_or_create_login_session(user, now=None) -> PosLoginSession | None:
    """
    Best-effort "login → usage" session for this user and day.

    - If user is anonymous → returns None.
    - If there is an open session for this user+day → reuse it.
    - Otherwise create a new one starting now.
    """
    if not user or not getattr(user, "is_authenticated", False):
        return None

    if now is None:
        now = timezone.now()

    day = get_or_create_work_day(now)

    sess = (
        PosLoginSession.objects
        .filter(user=user, day=day, ended_at__isnull=True)
        .order_by("-started_at")
        .first()
    )
    if sess:
        return sess

    return PosLoginSession.objects.create(
        user=user,
        day=day,
        started_at=now,
    )


def close_login_session(user, *, now=None, reason: str = "logout") -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False

    now = now or timezone.now()

    sess = (
        PosLoginSession.objects
        .filter(user=user, ended_at__isnull=True)
        .order_by("-started_at")
        .first()
    )
    if not sess:
        return False

    sess.ended_at = now
    sess.closed_reason = (reason or "")[:32]
    sess.save(update_fields=["ended_at", "closed_reason"])

    # OPTIONAL but recommended: close any open shift too
    PosShift.objects.filter(user=user, ended_at__isnull=True).update(ended_at=now)

    return True



def _get_store_container() -> ProductContainer | None:
    """
    Main POS container: we prefer is_store=True; fallback to code='store'.
    """
    c = ProductContainer.objects.filter(is_store=True).first()
    if c:
        return c
    return ProductContainer.objects.filter(code="store").first()


def _qty_to_primary(
    product: Product,
    uom_index: int,
    qty: Decimal,
    conv_override: Decimal | None = None,
) -> Tuple[Decimal, int]:
    """
    Convert qty based on UOM index to primary units.
    Returns (qty_primary, actual_unit_index_used).
    """
    qty = q3(qty or DEC0)
    if qty <= 0:
        return DEC0, 1

    if getattr(product, "is_single_unit", False):
        uom_index = 1
        conv = Decimal("1")
    else:
        conv = conv_override if conv_override is not None else (getattr(product, "conversion_factor", None) or Decimal("1"))
    if uom_index == 2:
        # secondary → primary
        return q3(qty * conv), 2
    return qty, 1


def _calc_row_total(*, product: Product, row: SalesBillRow) -> Decimal:
    qty_primary, _unit_index = _qty_to_primary(
        product,
        row.uom_index,
        row.qty,
        conv_override=getattr(row, "conv_factor_at_txn", None),
    )
    if qty_primary <= 0:
        return DEC0
    base = q3(qty_primary * q3(row.unit_price or DEC0))
    disc_amt = q3(row.disc_amount or DEC0)
    if disc_amt <= 0 and (row.disc_pct or DEC0) > 0 and base > 0:
        disc_amt = q3((base * q3(row.disc_pct)) / Decimal("100"))
    if disc_amt < 0:
        disc_amt = DEC0
    if disc_amt > base:
        disc_amt = base
    return q3(base - disc_amt)


@transaction.atomic
def finalize_pos_bill(*, bill: SalesBill, actor) -> None:
    """
    Called when a POS bill is saved as FINAL (not parked).
    Creates ProductMovement rows (movement_type='sale') per SalesBillRow.

    - Only runs once per bill (idempotent via check on existing movements).
    - Uses 'pos' / 'SalesBill' as source.
    - Works only for the main store container.
    - NOW: validates that the store container has enough stock for all
      products in this bill. If not enough, raises InsufficientStockError
      and DOES NOT post any movements.
    """
    # Only act on finalized bills
    if not bill.finalized or bill.parked:
        return

    # Avoid double-posting if something calls us twice
    already = ProductMovement.objects.filter(
        source_app="pos",
        source_model="SalesBill",
        source_id=str(bill.id),
    ).exists()
    if already:
        return

    container = _get_store_container()
    if container is None:
        raise RuntimeError("NO_STORE_CONTAINER")

    rows: list[SalesBillRow] = list(bill.rows.all())
    if not rows:
        return

    # ==========================
    # 1) Aggregate required qty per product (in PRIMARY units)
    # ==========================
    needed_by_product: dict[int, Decimal] = defaultdict(lambda: DEC0)
    product_cache: dict[int, Product] = {}

    for row in rows:
        # Might be a product that was deleted from catalog; skip
        try:
            product = product_cache.get(row.product_id)
            if product is None:
                product = Product.objects.get(pk=row.product_id)
                product_cache[row.product_id] = product
        except Product.DoesNotExist:
            continue

        qty_primary, _unit_index_used = _qty_to_primary(
            product,
            row.uom_index,
            row.qty,
            conv_override=getattr(row, "conv_factor_at_txn", None),
        )
        if qty_primary <= 0:
            continue

        needed_by_product[product.id] = q3(
            (needed_by_product[product.id] or DEC0) + qty_primary
        )

    if not needed_by_product:
        # nothing actually sold
        return

    # ==========================
    # 2) Load current stock for these products in the store container
    #    and lock rows to avoid race conditions.
    # ==========================
    # Ensure StockEntry rows exist so select_for_update actually locks something
    for pid in needed_by_product.keys():
        StockEntry.objects.get_or_create(
            product_id=pid,
            container=container,
            defaults={"qty_primary": DEC0},
        )

    entries = (
        StockEntry.objects
        .select_for_update()
        .filter(
            product_id__in=needed_by_product.keys(),
            container=container,
        )
    )

    available_by_product: dict[int, Decimal] = {}
    for e in entries:
        available_by_product[e.product_id] = q3(e.qty_primary or DEC0)

    # ==========================
    # 3) Detect shortages
    # ==========================
    shortages: list[dict] = []

    for pid, required in needed_by_product.items():
        available = available_by_product.get(pid, DEC0)
        if required > available:
            prod = product_cache.get(pid)
            shortages.append(
                {
                    "product_id": pid,
                    "product_name": prod.name if prod else "",
                    "needed": required,
                    "available": available,
                }
            )

    if shortages:
        # DO NOT post any sale movements; let caller handle this
        raise InsufficientStockError(shortages)

    # ==========================
    # 4) All good → post SALE movements (FIFO cost handled in InvSV)
    # ==========================
    for row in rows:
        # Might be a product that was deleted from catalog; skip
        try:
            product = Product.objects.get(pk=row.product_id)
        except Product.DoesNotExist:
            continue

        qty_primary, unit_index_used = _qty_to_primary(
            product,
            row.uom_index,
            row.qty,
            conv_override=getattr(row, "conv_factor_at_txn", None),
        )
        if qty_primary <= 0:
            continue

        # For initial cost hint, we use product.cost if it exists, otherwise 0.
        # record_sale_item will override this with FIFO cost if container is set.
        unit_cost = getattr(product, "cost", None)
        if unit_cost is None:
            unit_cost = DEC0

        InvSV.record_sale_item(
            actor=actor,
            product=product,
            unit_index=unit_index_used,
            qty_primary=qty_primary,   # POSITIVE; wrapper will flip it to negative
            unit_cost=unit_cost,
            source_app="pos",
            source_model="SalesBill",
            source_id=str(bill.id),
            container=container,
        )

    # ==========================
    # 5) Financials: cash-in receipt (paid only)
    # ==========================
    if bill.pay_status == SalesBill.PAY_NONE:
        return

    if not bill.money_container_id:
        raise RuntimeError("POS_MISSING_MONEY_CONTAINER")

    total_syp = q3(bill.total_syp or DEC0)
    total_usd = q3(bill.total_usd or DEC0)

    # fallback for legacy bills
    if total_syp == 0 and total_usd == 0:
        for row in rows:
            try:
                product = Product.objects.get(pk=row.product_id)
            except Product.DoesNotExist:
                continue
            row_total = _calc_row_total(product=product, row=row)
            row_currency = (row.sale_currency or SYP).upper()
            if row_currency == USD:
                total_usd = q3(total_usd + row_total)
            else:
                total_syp = q3(total_syp + row_total)

    fx_rate = bill.fx_rate_used or FinSV.get_current_fx_syp_per_usd()

    paid_syp = DEC0
    paid_usd = DEC0
    mode = bill.settlement_mode or SalesBill.SETTLE_SPLIT

    if bill.pay_status == SalesBill.PAY_FULL:
        if mode == SalesBill.SETTLE_ALL_SYP:
            paid_syp = q3(total_syp + (total_usd * fx_rate))
        elif mode == SalesBill.SETTLE_ALL_USD:
            paid_usd = q3(total_usd + (total_syp / fx_rate))
        else:
            paid_syp = total_syp
            paid_usd = total_usd
    else:
        if mode == SalesBill.SETTLE_ALL_SYP:
            paid_syp = q3(bill.paid_amount or DEC0)
        elif mode == SalesBill.SETTLE_ALL_USD:
            paid_usd = q3(bill.paid_amount or DEC0)
        else:
            paid_syp = q3(bill.paid_amount or DEC0)

    amounts = {}
    if paid_syp > 0:
        amounts[SYP] = paid_syp
    if paid_usd > 0:
        amounts[USD] = paid_usd

    if amounts:
        if bill.pay_status == SalesBill.PAY_FULL:
            FinSV.post_pos_sale_receipt(
                actor=actor,
                container_id=bill.money_container_id,
                amounts_by_code=amounts,
                fx_syp_per_usd=fx_rate,
                note="POS sale receipt",
                source_app="pos",
                source_model="SalesBill",
                source_id=str(bill.id),
            )
        else:
            if not bill.customer_id:
                raise RuntimeError("POS_CUSTOMER_REQUIRED_FOR_DEBT")
            cp = _ensure_customer_counterparty(customer=bill.customer)
            if paid_syp > 0:
                FinSV.post_settlement_with_fx(
                    actor=actor,
                    container_id=bill.money_container_id,
                    counterparty_id=cp.id,
                    currency_code=SYP,
                    cash_amount_signed=+q3(paid_syp),
                    fx_syp_per_usd=fx_rate,
                    note="POS sale settlement (SYP)",
                    source_app="pos",
                    source_model="SalesBill",
                    source_id=str(bill.id),
                )
            if paid_usd > 0:
                FinSV.post_settlement_with_fx(
                    actor=actor,
                    container_id=bill.money_container_id,
                    counterparty_id=cp.id,
                    currency_code=USD,
                    cash_amount_signed=+q3(paid_usd),
                    fx_syp_per_usd=fx_rate,
                    note="POS sale settlement (USD)",
                    source_app="pos",
                    source_model="SalesBill",
                    source_id=str(bill.id),
                )
