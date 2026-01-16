# app/pos/views.py
from __future__ import annotations

from datetime import date, timedelta, time as dt_time
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db.models import Sum, Count, Q, Max, F
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseForbidden,
    JsonResponse,
)
from django.shortcuts import render , get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_GET
from django.template.loader import render_to_string

from .models import PosDay, PosShift, PosLoginSession, SalesBill, CustomerProfile, SalesReturn
from debts.models import DebtorDebt, PartyType

from inventory.models import ProductMovement , DEC0 , q3 , q4
from financials.models import MoneyContainer, MoneyContainerCurrency
from financials import services as FinSV

User = get_user_model()

# ============================================================
# Helper: build timeline events for a given day + filters
# ============================================================
def build_timeline_events_for_day(
    request: HttpRequest,
    *,
    selected_day: PosDay,
) -> tuple[list[dict], object, dict]:
    """
    Returns:
      - timeline_events (ordered, with bill_header injected)
      - actors_qs
      - state dict (filters)
    """

    # -----------------------
    # Timeline filters
    # -----------------------
    filters_applied = (request.GET.get("timeline_filters") or "").strip() == "1"

    if not filters_applied:
        show_logins = True
        show_shifts = True
        show_bills = True
        time_from = None
        time_to = None
        selected_actor_id = ""
    else:
        show_logins = "show_logins" in request.GET
        show_shifts = "show_shifts" in request.GET
        show_bills = "show_bills" in request.GET
        selected_actor_id = (request.GET.get("actor") or "").strip()

        def _parse_time(s: str) -> dt_time | None:
            try:
                return dt_time.fromisoformat(s) if s else None
            except Exception:
                return None

        time_from = _parse_time(request.GET.get("time_from") or "")
        time_to = _parse_time(request.GET.get("time_to") or "")

        if time_from and time_to and time_from > time_to:
            time_from, time_to = time_to, time_from

    # -----------------------
    # Actor list
    # -----------------------
    user_ids = set()

    user_ids |= set(
        PosLoginSession.objects
        .filter(day=selected_day)
        .values_list("user_id", flat=True)
    )

    user_ids |= set(
        PosShift.objects
        .filter(day=selected_day)
        .values_list("user_id", flat=True)
    )

    user_ids |= set(
        SalesBill.objects.filter(work_day=selected_day, is_deleted=False)
        .values_list("cashier_id", flat=True)
    )

    user_ids.discard(None)

    actors_qs = User.objects.filter(id__in=user_ids).order_by("username") if user_ids else User.objects.none()

    # -----------------------
    # Helpers
    # -----------------------
    def match_actor(uid: int | None) -> bool:
        if not selected_actor_id:
            return True
        try:
            return uid == int(selected_actor_id)
        except Exception:
            return True

    def in_time_range(dt) -> bool:
        if not (time_from or time_to):
            return True
        t = timezone.localtime(dt).time()
        if time_from and t < time_from:
            return False
        if time_to and t > time_to:
            return False
        return True

    # -----------------------
    # Collect events
    # -----------------------
    events: list[dict] = []

    if show_logins:
        for s in (
            PosLoginSession.objects
            .filter(day=selected_day)
            .select_related("user")
            .order_by("started_at")
        ):
            if not match_actor(s.user_id):
                continue
            if s.started_at and in_time_range(s.started_at):
                events.append({"type": "login_start", "time": s.started_at, "user": s.user, "session": s})
            if s.ended_at and in_time_range(s.ended_at):
                events.append({"type": "login_end", "time": s.ended_at, "user": s.user, "session": s})

    if show_shifts:
        for sh in (
            PosShift.objects
            .filter(day=selected_day)
            .select_related("user")
            .order_by("started_at")
        ):
            if not match_actor(sh.user_id):
                continue
            if sh.started_at and in_time_range(sh.started_at):
                events.append({"type": "shift_start", "time": sh.started_at, "user": sh.user, "shift": sh})
            if sh.ended_at and in_time_range(sh.ended_at):
                events.append({"type": "shift_end", "time": sh.ended_at, "user": sh.user, "shift": sh})

    if show_bills:
        for b in (
            SalesBill.objects.filter(work_day=selected_day, is_deleted=False)
            .select_related("cashier", "shift")
            .order_by("created_at")
        ):
            if not match_actor(b.cashier_id):
                continue
            if not in_time_range(b.created_at):
                continue
            events.append({"type": "bill", "time": b.created_at, "user": b.cashier, "bill": b})

    events.sort(key=lambda e: e["time"])

    # -----------------------
    # Inject bill_header rows
    # -----------------------
    grouped: list[dict] = []
    prev_bill = False
    for ev in events:
        is_bill = ev["type"] == "bill"
        if is_bill and not prev_bill:
            grouped.append({"type": "bill_header", "time": ev["time"], "user": None})
        grouped.append(ev)
        prev_bill = is_bill

    state = {
        "selected_actor_id": selected_actor_id,
        "show_logins": show_logins,
        "show_shifts": show_shifts,
        "show_bills": show_bills,
        "time_from": time_from,
        "time_to": time_to,
        "filters_applied": filters_applied,
    }

    return grouped, actors_qs, state


# ============================================================
# Cashier POS screen
# ============================================================
@login_required
def pos_screen(request: HttpRequest) -> HttpResponse:
    user = request.user

    containers_qs = (
        MoneyContainer.objects
        .filter(is_active=True, features__code="pos_sales", features__is_active=True)
        .distinct()
        .order_by("name")
    )

    if not (user.is_superuser or user.is_staff):
        containers_qs = (
            containers_qs
            .filter(Q(allowed_users__isnull=True) | Q(allowed_users=user))
            .distinct()
        )

    containers = []
    for c in containers_qs:
        enabled_codes = list(
            MoneyContainerCurrency.objects
            .filter(container=c, is_enabled=True)
            .values_list("currency__code", flat=True)
        )
        containers.append(
            {
                "id": c.id,
                "name": c.name,
                "currencies": enabled_codes,
            }
        )

    try:
        fx_rate = FinSV.get_current_fx_syp_per_usd()
    except Exception:
        fx_rate = None

    context = {
        "pos_containers": containers,
        "pos_fx_rate": fx_rate,
    }
    return render(request, "pos/screen.html", context)


# ============================================================
# Manager POS overview (FULL PAGE)
# ============================================================
@login_required
def pos_manager_overview(request: HttpRequest) -> HttpResponse:
    user = request.user
    if not (user.is_superuser or user.is_staff):
        return HttpResponseForbidden("غير مسموح لك.")

    today = timezone.localdate()

    def parse_date(s, default):
        if not s:
            return default
        try:
            return date.fromisoformat(str(s))
        except Exception:
            return default

    date_from = parse_date(request.GET.get("from"), today - timedelta(days=6))
    date_to = parse_date(request.GET.get("to"), today)

    if date_from > date_to:
        date_from, date_to = date_to, date_from

    days_qs = PosDay.objects.filter(date__range=(date_from, date_to)).order_by("-date")

    bills_qs = SalesBill.objects.filter(
        finalized=True,
        parked=False,
        work_day__in=days_qs,
    )

    stats = (
        bills_qs
        .values("work_day_id")
        .annotate(
            total_bills=Count("id"),
            total_amount=Sum("total_amount"),
            paid_amount=Sum("paid_amount"),
        )
    )

    stats_map = {s["work_day_id"]: s for s in stats}

    days_ctx = []
    for d in days_qs:
        st = stats_map.get(d.id, {})
        total = st.get("total_amount") or Decimal("0")
        paid = st.get("paid_amount") or Decimal("0")
        days_ctx.append({
            "obj": d,
            "total_bills": st.get("total_bills") or 0,
            "total_amount": total,
            "paid_amount": paid,
            "left_amount": total - paid,
        })

    selected_day = None
    if request.GET.get("day"):
        try:
            selected_day = PosDay.objects.get(pk=int(request.GET["day"]))
        except Exception:
            pass
    if not selected_day and days_qs.exists():
        selected_day = days_qs.first()

    timeline_events = []
    actors_qs = User.objects.none()
    state = {}

    if selected_day:
        timeline_events, actors_qs, state = build_timeline_events_for_day(
            request, selected_day=selected_day
        )

    context = {
        "date_from": date_from,
        "date_to": date_to,
        "days": days_ctx,
        "selected_day": selected_day,
        "timeline_events": timeline_events[:200],
        "has_more": len(timeline_events) > 200,
        "actors": actors_qs,
        **state,
    }

    return render(request, "pos/manager_overview.html", context)


# ============================================================
# Manager: Customer profiles (POS)
# ============================================================
@login_required
def pos_manager_customers(request: HttpRequest) -> HttpResponse:
    user = request.user
    if not (user.is_superuser or user.is_staff):
        return HttpResponseForbidden("Forbidden.")

    q = (request.GET.get("q") or "").strip()
    only_active = (request.GET.get("active") or "").strip() == "1"
    only_debt = (request.GET.get("debt_only") or "").strip() == "1"

    qs = CustomerProfile.objects.all()
    if q:
        if q.isdigit():
            qs = qs.filter(Q(id=int(q)) | Q(name__icontains=q))
        else:
            qs = qs.filter(Q(name__icontains=q))

    if only_active:
        qs = qs.filter(bills__finalized=True, bills__is_deleted=False).distinct()

    customers = list(qs.order_by("name")[:300])
    ids = [c.id for c in customers]

    # bills stats (last seen + count)
    bill_stats = {}
    for row in (
        SalesBill.objects
        .filter(customer_id__in=ids, finalized=True, is_deleted=False)
        .values("customer_id")
        .annotate(last_seen=Max("created_at"), purchases=Count("id"))
    ):
        bill_stats[row["customer_id"]] = row

    # debts per currency
    debt_map = {}
    for row in (
        DebtorDebt.objects
        .filter(customer_id__in=ids, party_type=PartyType.CUSTOMER, status="open")
        .values("customer_id", "currency_code")
        .annotate(rem=Sum(F("total") - F("paid_amount")))
    ):
        key = (row["customer_id"], (row["currency_code"] or "SYP").upper())
        debt_map[key] = row["rem"] or DEC0

    items = []
    for c in customers:
        debt_syp = debt_map.get((c.id, "SYP"), DEC0) or DEC0
        debt_usd = debt_map.get((c.id, "USD"), DEC0) or DEC0
        if only_debt and debt_syp <= DEC0 and debt_usd <= DEC0:
            continue
        st = bill_stats.get(c.id, {})
        items.append({
            "id": c.id,
            "name": c.name,
            "debt_syp": q3(debt_syp),
            "debt_usd": q3(debt_usd),
            "last_seen": st.get("last_seen"),
            "purchases": st.get("purchases") or 0,
        })

    context = {
        "items": items,
        "q": q,
        "only_active": only_active,
        "only_debt": only_debt,
    }
    return render(request, "pos/manager_customers.html", context)


# ============================================================
# Manager: Customer debts (POS)
# ============================================================
@login_required
def pos_manager_customer_debts(request: HttpRequest) -> HttpResponse:
    user = request.user
    if not (user.is_superuser or user.is_staff):
        return HttpResponseForbidden("Forbidden.")

    q = (request.GET.get("q") or "").strip()
    status = (request.GET.get("status") or "").strip().lower()
    currency = (request.GET.get("currency") or "").strip().upper()
    customer_id = request.GET.get("customer_id")

    def _parse_date(s):
        try:
            return date.fromisoformat(s) if s else None
        except Exception:
            return None

    date_from = _parse_date(request.GET.get("date_from") or "")
    date_to = _parse_date(request.GET.get("date_to") or "")

    qs = DebtorDebt.objects.select_related("customer").filter(party_type=PartyType.CUSTOMER)

    if customer_id:
        try:
            qs = qs.filter(customer_id=int(customer_id))
        except Exception:
            pass

    if q:
        if q.isdigit():
            qs = qs.filter(Q(customer_id=int(q)) | Q(source_id=str(q)) | Q(doc_serial=int(q)))
        else:
            qs = qs.filter(Q(customer__name__icontains=q) | Q(source_id__icontains=q))

    if status in {"open", "closed"}:
        qs = qs.filter(status=status)

    if currency in {"SYP", "USD"}:
        qs = qs.filter(currency_code=currency)

    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)

    qs = qs.annotate(last_payment=Max("payments__created_at"))

    items = []
    for e in qs.order_by("-created_at")[:400]:
        remaining = (e.total or DEC0) - (e.paid_amount or DEC0)
        items.append({
            "id": e.id,
            "customer_id": e.customer_id,
            "customer_name": e.customer.name if e.customer_id else "",
            "source_id": e.source_id,
            "doc_serial": e.doc_serial,
            "currency_code": (e.currency_code or "SYP").upper(),
            "total": q3(e.total or DEC0),
            "paid": q3(e.paid_amount or DEC0),
            "remaining": q3(remaining),
            "status": e.status,
            "created_at": e.created_at,
            "last_payment": getattr(e, "last_payment", None),
        })

    context = {
        "items": items,
        "q": q,
        "status": status,
        "currency": currency,
        "date_from": date_from,
        "date_to": date_to,
        "customer_id": customer_id or "",
    }
    return render(request, "pos/manager_customer_debts.html", context)


# ============================================================
# AJAX: timeline chunk loader
# ============================================================
@require_GET
@login_required
def pos_manager_overview_timeline(request: HttpRequest):
    user = request.user
    if not (user.is_superuser or user.is_staff):
        return JsonResponse({"ok": False, "error": "FORBIDDEN"}, status=403)

    try:
        day = PosDay.objects.get(pk=int(request.GET.get("day")))
    except Exception:
        return JsonResponse({"ok": False, "error": "INVALID_DAY"}, status=400)

    try:
        offset = int(request.GET.get("offset") or 0)
    except ValueError:
        offset = 0

    limit = max(50, min(int(request.GET.get("limit") or 200), 500))

    events, _actors, _state = build_timeline_events_for_day(
        request, selected_day=day
    )

    chunk = events[offset: offset + limit]
    next_offset = offset + len(chunk)
    has_more = next_offset < len(events)

    html = render_to_string(
        "pos/_manager_overview_timeline_items.html",
        {"timeline_events": chunk},
        request=request,
    )

    return JsonResponse({
        "ok": True,
        "html": html,
        "next_offset": next_offset,
        "has_more": has_more,
        "returned": len(chunk),
    })


@login_required
def pos_manager_bill_detail(request: HttpRequest, bill_id: int) -> HttpResponse:
    user = request.user
    if not (user.is_superuser or user.is_staff):
        return HttpResponseForbidden("Forbidden.")

    bill = get_object_or_404(
        SalesBill.objects
        .select_related("cashier", "shift", "login_session", "work_day", "customer")
        .prefetch_related("rows"),
        pk=bill_id,
    )

    created_dt = timezone.localtime(bill.created_at) if bill.created_at else None
    updated_dt = timezone.localtime(bill.updated_at) if bill.updated_at else None

    pay_map = {
        SalesBill.PAY_FULL: "Paid in full",
        SalesBill.PAY_NONE: "Unpaid",
        SalesBill.PAY_PARTIAL: "Partially paid",
    }

    cashier_name = (
        bill.cashier.get_full_name() or bill.cashier.username
        if bill.cashier else "—"
    )

    shift_id = bill.shift_id
    session_id = bill.login_session_id
    day_date = bill.work_day.date if bill.work_day else None

    total_amount = bill.total_amount or DEC0
    paid_amount = bill.paid_amount or DEC0
    left_amount = total_amount - paid_amount

    # =========================================================
    # Product + units
    # =========================================================
    from catalog.models import Product, UnitType
    from inventory.models import SaleCostPart
    from billing.models import BillItem
    from django.urls import reverse

    rows = list(bill.rows.all().order_by("id"))
    product_ids = {int(r.product_id) for r in rows if r.product_id}

    products = (
        Product.objects
        .filter(id__in=product_ids)
        .only("id", "unit_primary", "unit_secondary", "conversion_factor")
    )
    prod_map = {p.id: p for p in products}
    unit_label_map = dict(UnitType.choices)

    def conv_for(pid: int) -> Decimal:
        p = prod_map.get(pid)
        if not p:
            return Decimal("1")
        c = p.conversion_factor or Decimal("1")
        try:
            c = Decimal(str(c))
        except Exception:
            c = Decimal("1")
        return c if c > 0 else Decimal("1")

    def uom_label_for(pid: int, uom_index: int) -> str:
        p = prod_map.get(pid)
        if not p:
            return "—"
        if int(uom_index) == 2 and p.unit_secondary:
            return unit_label_map.get(p.unit_secondary, p.unit_secondary)
        return unit_label_map.get(p.unit_primary, p.unit_primary)

    # =========================================================
    # FIFO cost parts (grouped by product)
    # =========================================================
    parts_qs = (
        SaleCostPart.objects
        .filter(
            movement__source_app="pos",
            movement__source_model="SalesBill",
            movement__source_id=str(bill.id),
        )
        .select_related("movement", "fifo_layer")
        .order_by("movement_id", "id")
    )

    parts_by_product: dict[int, list[SaleCostPart]] = {}
    for part in parts_qs:
        pid = int(part.movement.product_id)
        parts_by_product.setdefault(pid, []).append(part)

    # =========================================================
    # Row factory
    # =========================================================
    def make_row(**kw):
        base = {
            "name": "",
            "qty": DEC0,
            "uom_label": "—",
            "unit_price": DEC0,
            "disc_amount": DEC0,
            "disc_pct": Decimal("0"),
            "line_total": DEC0,
            "notes": "",
            "unit_cost": None,
            "total_cost": None,
            "profit": None,
            "profit_is_pos": None,
            "purchase_bill_id": None,
            "purchase_bill_url": None,
        }
        base.update(kw)
        return base

    # =========================================================
    # Build rows
    # =========================================================
    display_rows: list[dict] = []

    for r in rows:
        pid = int(r.product_id or 0)
        if pid <= 0:
            continue

        uom_index = int(r.uom_index or 1)
        uom_label = uom_label_for(pid, uom_index)
        conv = conv_for(pid)

        qty_sold = q3(Decimal(str(r.qty or DEC0)))
        if qty_sold <= DEC0:
            continue

        unit_price_primary = q4(Decimal(str(r.unit_price or DEC0)))
        unit_price_display = (
            q4(unit_price_primary * conv)
            if uom_index == 2
            else unit_price_primary
        )

        disc_amount_total = q3(Decimal(str(r.disc_amount or DEC0)))
        disc_pct = Decimal(str(r.disc_pct or 0))

        qty_primary_total = (
            q3(qty_sold * conv)
            if uom_index == 2
            else q3(qty_sold)
        )

        parts = parts_by_product.get(pid, [])

        # ---------- no FIFO ----------
        if not parts:
            gross = q3(unit_price_display * qty_sold)
            net = q3(gross - disc_amount_total)

            display_rows.append(make_row(
                name=r.product_name,
                qty=qty_sold,
                uom_label=uom_label,
                unit_price=unit_price_display,
                disc_amount=disc_amount_total,
                disc_pct=disc_pct,
                line_total=net,
                notes=r.notes or "",
            ))
            continue

        # ---------- FIFO rows ----------
        for part in parts:
            part_qty_primary = q3(Decimal(str(part.qty_primary or DEC0)))
            if part_qty_primary <= DEC0:
                continue

            part_qty_display = (
                q3(part_qty_primary / conv)
                if uom_index == 2
                else part_qty_primary
            )

            unit_cost_primary = q4(Decimal(str(part.unit_cost or DEC0)))
            unit_cost_display = (
                q4(unit_cost_primary * conv)
                if uom_index == 2
                else unit_cost_primary
            )

            total_cost = q3(Decimal(str(part.total_cost or DEC0)))

            if qty_primary_total > DEC0 and disc_amount_total > DEC0:
                part_disc = q3(
                    disc_amount_total * (part_qty_primary / qty_primary_total)
                )
            else:
                part_disc = DEC0

            gross = q3(unit_price_display * part_qty_display)
            net = q3(gross - part_disc)
            profit = q3(net - total_cost)

            # 🔥 CORRECT purchase bill resolution (FIFO → BillItem → Bill)
            pb_id = None
            pb_url = None
            layer = part.fifo_layer

            if (
                layer
                and layer.source_app == "billing"
                and layer.source_model == "BillItem"
                and layer.source_id
            ):
                try:
                    item_id = int(layer.source_id)
                    bill_item = BillItem.objects.select_related("bill").get(id=item_id)
                    pb_id = bill_item.bill_id
                    pb_url = reverse("billing_bill_view", args=[pb_id])
                except Exception:
                    pass

            display_rows.append(make_row(
                name=r.product_name,
                qty=part_qty_display,
                uom_label=uom_label,
                unit_price=unit_price_display,
                disc_amount=part_disc,
                disc_pct=disc_pct,
                line_total=net,
                notes=r.notes or "",
                unit_cost=unit_cost_display,
                total_cost=total_cost,
                profit=profit,
                profit_is_pos=(profit >= DEC0),
                purchase_bill_id=pb_id,
                purchase_bill_url=pb_url,
            ))

    context = {
        "bill": bill,
        "created_dt": created_dt,
        "updated_dt": updated_dt,
        "cashier_name": cashier_name,
        "pay_label": pay_map.get(bill.pay_status, bill.pay_status),
        "day_date": day_date,
        "session_id": session_id,
        "shift_id": shift_id,
        "total_amount": total_amount,
        "paid_amount": paid_amount,
        "left_amount": left_amount,
        "sold_rows": display_rows,
        "has_cost_movements": parts_qs.exists(),
        "sale_returns": SalesReturn.objects.filter(sale_bill=bill).select_related("stock_container", "posted_by").order_by("-id"),
    }

    return render(request, "pos/manager_bill_detail.html", context)
