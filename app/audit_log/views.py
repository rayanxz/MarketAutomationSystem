# app/audit_log/views.py
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Any, Tuple

from django.contrib.auth.decorators import login_required
from django.db.models import Q, Count
from django.db.models.functions import TruncDate
from django.shortcuts import render
from django.utils import timezone
from django.utils.dateparse import parse_date
from core.date_filters import parse_filter_date

from .models import AuditLog, AuditAction, AuditEntrypoint

from decimal import Decimal, InvalidOperation

def _has_diff(ev) -> bool:
    return bool(ev.before_json or ev.after_json)

def _is_session(ev) -> bool:
    a = (ev.action or "").lower()
    if a in ("login", "logout"):
        return True
    # later: cashier shift start/end detection
    t = (ev.title or "").lower()
    m = (ev.message or "").lower()
    if "shift" in t or "shift" in m:
        return True
    return False


def _arabic_action(action: str) -> str:
    return {
        "login":  "تسجيل دخول",
        "logout": "تسجيل خروج",
        "create": "إنشاء",
        "update": "تعديل",
        "delete": "حذف",
        "error":  "خطأ",
        "info":   "معلومة",
        "api":    "API",
    }.get(action, action)

def _arabic_entrypoint(ep: str) -> str:
    return {
        "pos": "نقاط البيع",
        "manager": "المدير",
        "owner": "المالك",
        "api": "API",
        "unknown": "غير معروف",
    }.get((ep or "").lower(), ep or "—")

def _role_ar(user) -> str:
    # same logic you already use in base_dash.html
    if not user:
        return "—"
    prof = getattr(user, "account_profile", None)
    if prof:
        # your display is Arabic already, so use it
        try:
            return prof.get_role_display()
        except Exception:
            pass
        return str(getattr(prof, "role", "") or "—")
    if getattr(user, "is_superuser", False):
        return "مالك"
    if getattr(user, "is_staff", False):
        return "مدير"
    return "كاشير"

def _fmt_money(x) -> str:
    if x is None:
        return "—"
    try:
        d = Decimal(str(x))
    except (InvalidOperation, ValueError):
        return str(x)
    # keep your style, no forced decimals
    return f"{d:,}".replace(",", "٬")

def _ar_pay_status(code: str) -> str:
    c = (code or "").lower()
    return {
        "full": "مدفوعة",
        "pay_full": "مدفوعة",
        "partial": "مدفوعة جزئياً",
        "pay_partial": "مدفوعة جزئياً",
        "debt": "دين",
        "pay_debt": "دين",
        "unpaid": "غير مدفوعة",
        "pay_unpaid": "غير مدفوعة",
    }.get(c, code or "—")

def _ui_for_event(ev) -> dict:
    kind = "ref"
    tone = "warn"
    btn_label = "عرض"

    # decide diff/ref first
    if _has_diff(ev):
        kind = "diff"
        tone = "warn"
        btn_label = "عرض المقارنة"

    # session overrides
    if _is_session(ev):
        kind = "session"
        btn_label = ""
        a = (ev.action or "").lower()
        tone = "start" if a in ("login", "shift_start") else "end"

    actor_name = ev.actor.username if ev.actor else "—"
    actor_role = _role_ar(ev.actor)

    ep_raw = getattr(getattr(ev, "session", None), "entrypoint", "") or ""
    ep = _arabic_entrypoint(ep_raw)

    act = (ev.action or "").lower()

    # Arabic labels
    action_label = _arabic_action(act)
    title = action_label  # default title
    subtitle = ""
    details: list[tuple[str, str]] = []

    meta = ev.meta or {}
    meta_kind = (meta.get("kind") or "").strip()
    summary = meta.get("summary") or {}


    # -------------------------
    # 1) Sessions (login/logout/shift)
    # -------------------------
    if kind == "session":
        # NO PATH SHIT HERE.
        # Just a tiny hint of where (optional)
        if act == "login":
            subtitle = f"إلى: {ep}"
        elif act == "logout":
            subtitle = f"من: {ep}"
        else:
            # if later you add shift start/end
            subtitle = ep or ""

        return {
            "kind": kind,
            "tone": tone,
            "btn_label": btn_label,
            "title": title,
            "action_label": action_label,
            "subtitle": subtitle,
            "actor_name": actor_name,
            "actor_role": actor_role,
            "entrypoint": ep,
            "details": [],  # sessions stay thin
        }
    
    # =====================
    # POS SALE EVENTS (meta-driven)
    # =====================
    if meta_kind in ("pos.sale_bill_saved", "pos.sale_bill_pended", "pos.sale_bill_deleted"):
        bill_id = summary.get("bill_id") or ev.target_id or "—"
        rows_count = summary.get("rows_count")
        customer = (summary.get("customer_name") or "").strip() or "—"
        pay_status = _ar_pay_status(summary.get("pay_status") or "")
        total = _fmt_money(summary.get("total_amount"))
        paid = _fmt_money(summary.get("paid_amount"))

        # title in Arabic (card header)
        if meta_kind == "pos.sale_bill_saved":
            title = "حفظ فاتورة مبيعات (POS)"
        elif meta_kind == "pos.sale_bill_pended":
            title = "تعليق فاتورة مبيعات (POS)"
        else:
            title = "حذف فاتورة مبيعات (معلقة)"

        # card type: these are "ref" events (yellow) with a Show button later
        kind_ui = "ref"
        tone = "warn"
        btn_label = "عرض"

        details: list[tuple[str, str]] = []
        details.append(("رقم الفاتورة", f"#{bill_id}"))
        details.append(("العميل", customer))
        details.append(("حالة الدفع", pay_status))
        details.append(("الإجمالي", total))
        details.append(("المدفوع", paid))
        if rows_count is not None:
            details.append(("عدد الأصناف", str(rows_count)))

        return {
            "kind": kind_ui,
            "tone": tone,
            "btn_label": btn_label,
            "title": title,
            "subtitle": "",

            "actor_name": actor_name,
            "actor_role": actor_role,
            "entrypoint": ep,

            # 👇 NEW: tell template we want the POS "table row" style
            "layout": "pos_sale_row",

            # 👇 single-row “table” cells (order matters)
            "cols": [
                ("رقم", f"#{bill_id}"),
                ("العميل", customer),
                ("الدفع", pay_status),
                ("الأصناف", str(rows_count) if rows_count is not None else "—"),
                ("الإجمالي", total),
                ("المدفوع", paid),
            ],

            # keep details if you want as fallback (optional)
            "details": [],
        }

    # =====================
    # PURCHASE BILL CREATED
    # =====================
    if meta_kind == "billing.purchase_bill_created":
        s = summary
        cols = []
        cols.append(("ID", f"#{s.get('serial') or s.get('bill_id') or '—'}"))
        cols.append(("المورد", (s.get("provider_name") or "—")))
        cols.append(("الحالة", _ar_pay_status(s.get("status") or "")))
        cols.append(("الأصناف", str(s.get("items_count") or "—")))
        cols.append(("الإجمالي", _fmt_money(s.get("total"))))
        cols.append(("المدفوع", _fmt_money(s.get("paid_amount"))))
        ccode = (s.get("container") or "").strip()
        subtitle = f"المخزن: {ccode}" if ccode else ""

        return {
            "kind": "ref",
            "tone": "warn",
            "btn_label": "عرض",
            "title": "إنشاء فاتورة مشتريات",
            "subtitle": subtitle,
            "actor_name": actor_name,
            "actor_role": actor_role,
            "entrypoint": ep,
            "layout": "pos_sale_row",
            "cols": cols,
            "details": [],
        }
    
    # =====================
    # PURCHASE BILL DELETED
    # =====================
    if meta_kind == "billing.purchase_bill_deleted":
        s = summary
        cols = []
        cols.append(("ID", f"#{s.get('serial') or s.get('bill_id') or '—'}"))
        cols.append(("المورد", (s.get("provider_name") or "—")))
        cols.append(("الحالة", _ar_pay_status(s.get("status") or "")))
        cols.append(("الأصناف", str(s.get("items_count") or "—")))
        cols.append(("الإجمالي", _fmt_money(s.get("total"))))
        cols.append(("المدفوع", _fmt_money(s.get("paid_amount"))))

        ccode = (s.get("container") or "").strip()
        subtitle = f"المخزن: {ccode}" if ccode else ""

        return {
            "kind": "ref",
            "tone": "warn",
            "btn_label": "عرض",
            "title": "حذف فاتورة مشتريات",
            "subtitle": subtitle,
            "actor_name": actor_name,
            "actor_role": actor_role,
            "entrypoint": ep,
            "layout": "pos_sale_row",
            "cols": cols,
            "details": [],
        }

    # =====================
    # PROVIDER RETURN CREATED
    # =====================
    if meta_kind == "billing.provider_return_created":
        s = summary
        cols = []
        cols.append(("ID", f"#{s.get('serial') or s.get('return_id') or '—'}"))
        cols.append(("المورد", (s.get("provider_name") or "—")))
        cols.append(("الحالة", _ar_pay_status(s.get("status") or "")))
        cols.append(("الأصناف", str(s.get("items_count") or "—")))
        cols.append(("الإجمالي", _fmt_money(s.get("total"))))
        cols.append(("المستلم", _fmt_money(s.get("collected_amount"))))

        sub_parts = []

        if s.get("source_bill_serial"):
            sub_parts.append(f"فاتورة أصل: #{s.get('source_bill_serial')}")

        if s.get("wizard_mode"):
            sub_parts.append("وضع: موزّع")
        else:
            lc = (s.get("legacy_container") or "").strip()
            if lc:
                sub_parts.append(f"المخزن: {lc}")

        subtitle = " • ".join(sub_parts)


        return {
            "kind": "ref",
            "tone": "warn",
            "btn_label": "عرض",
            "title": "إنشاء مرتجع مورد",
            "subtitle": subtitle,
            "actor_name": actor_name,
            "actor_role": actor_role,
            "entrypoint": ep,
            "layout": "pos_sale_row",
            "cols": cols,
            "details": [],
        }

    # =====================
    # PROVIDER CREATED
    # =====================
    if meta_kind == "billing.provider_created":
        s = summary
        cols = []
        cols.append(("ID", f"#{s.get('provider_id') or '—'}"))
        cols.append(("الاسم", (s.get("name") or "—")))
        phone = (s.get("phone") or "").strip()
        if phone:
            cols.append(("الهاتف", phone))
        cols.append(("الحالة", "نشط" if s.get("is_active") else "غير نشط"))

        return {
            "kind": "ref",
            "tone": "warn",
            "btn_label": "عرض",
            "title": "إنشاء مورد",
            "subtitle": "",
            "actor_name": actor_name,
            "actor_role": actor_role,
            "entrypoint": ep,
            "layout": "pos_sale_row",
            "cols": cols,
            "details": [],
        }
    # =====================
    # PROVIDER DELETED
    # =====================
    if meta_kind == "billing.provider_deleted":
        s = summary
        cols = []
        cols.append(("ID", f"#{s.get('provider_id') or '—'}"))
        cols.append(("الاسم", (s.get("name") or "—")))
        phone = (s.get("phone") or "").strip()
        if phone:
            cols.append(("الهاتف", phone))
        cols.append(("الحالة", "غير نشط"))

        return {
            "kind": "ref",
            "tone": "warn",
            "btn_label": "عرض",
            "title": "حذف مورد",
            "subtitle": "",
            "actor_name": actor_name,
            "actor_role": actor_role,
            "entrypoint": ep,
            "layout": "pos_sale_row",
            "cols": cols,
            "details": [],
        }

    # -------------------------
    # 2) Billing purchase bill (ref style)
    # -------------------------
    if (ev.target_app or "").lower() == "billing" and (ev.target_model or "").lower() in ("bill", "billitem"):
        msg = (ev.message or "") + " " + (ev.title or "")
        if "purchase" in msg.lower() or "مشتريات" in msg:
            title = {
                "create": "إنشاء فاتورة مشتريات",
                "update": "تعديل فاتورة مشتريات",
                "delete": "حذف فاتورة مشتريات",
            }.get(act, "فاتورة مشتريات")

        if ev.target_id:
            details.append(("رقم الفاتورة", f"#{ev.target_id}"))

        lower = (ev.message or "").lower()
        if "total=" in lower:
            try:
                part = (ev.message or "").split("total=", 1)[1].strip()
                token = part.split()[0].strip().strip(",")
                details.append(("الإجمالي", _fmt_money(token)))
            except Exception:
                pass

        # keep message as fallback (later we’ll parse better)
        if ev.message:
            details.append(("ملاحظة", ev.message))

        return {
            "kind": kind,
            "tone": "warn",  # always yellow for ref/diff
            "btn_label": btn_label,
            "title": title,
            "action_label": action_label,
            "subtitle": "",
            "actor_name": actor_name,
            "actor_role": actor_role,
            "entrypoint": ep,
            "details": details,
        }


    # =====================
    # STOCK CONTAINER TRANSFER
    # =====================
    if meta_kind == "stock.container_transfer":
        m = meta
        ref = (m.get("ref") or "").strip() or "—"

        f = m.get("from") or {}
        t = m.get("to") or {}

        from_code = (f.get("name") or f.get("code") or "").strip() or "—"
        to_code   = (t.get("name") or t.get("code") or "").strip() or "—"


        # "products types included" = distinct product IDs in rows
        rows_meta = m.get("rows") or []
        prod_ids = set()
        for r in rows_meta:
            pid = r.get("product_id")
            if pid is not None:
                try:
                    prod_ids.add(int(pid))
                except Exception:
                    pass

        product_types_count = len(prod_ids) if prod_ids else 0

        title = "نقل مخزون بين الحاويات"

        note = (m.get("note") or "").strip()
        subtitle = f"ملاحظة: {note}" if note else ""

        cols = [
            ("ID", ref),
            ("من", from_code),
            ("إلى", to_code),
            ("أنواع المواد", str(product_types_count) if product_types_count else "—"),
        ]

        return {
            "kind": "ref",
            "tone": "warn",
            "btn_label": "عرض",
            "title": title,
            "subtitle": subtitle,
            "actor_name": actor_name,
            "actor_role": actor_role,
            "entrypoint": ep,
            "layout": "pos_sale_row",
            "cols": cols,
            "details": [],
        }


    # -------------------------
    # 3) Generic fallback (ref/diff)
    # -------------------------
    if ev.title:
        subtitle = ev.title

    if ev.message:
        details.append(("التفاصيل", ev.message))

    if ev.target_model:
        details.append(("الهدف", f"{ev.target_app}.{ev.target_model}#{ev.target_id}"))

    # IMPORTANT: do NOT include path by default
    # if ev.path: details.append(("المسار", f"{ev.method} {ev.path}"))

    return {
        "kind": kind,
        "tone": "warn",
        "btn_label": btn_label,
        "title": title,
        "action_label": action_label,
        "subtitle": subtitle,
        "actor_name": actor_name,
        "actor_role": actor_role,
        "entrypoint": ep,
        "details": details,
    }

def _is_owner(user) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    if getattr(user, "is_owner", False):
        return True
    prof = getattr(user, "account_profile", None)
    if prof is not None:
        role = getattr(prof, "role", "") or ""
        if str(role).lower() == "owner":
            return True
    return False


def owner_only(view_func):
    def _wrapped(request, *args, **kwargs):
        if not _is_owner(request.user):
            from django.http import HttpResponseForbidden
            return HttpResponseForbidden("Owners only.")
        return view_func(request, *args, **kwargs)
    return _wrapped


def _parse_dt_range(request) -> Tuple[timezone.datetime, timezone.datetime]:
    tz = timezone.get_current_timezone()
    today = timezone.localdate()

    d_from = parse_filter_date(request.GET.get("from", "") or "")
    d_to = parse_filter_date(request.GET.get("to", "") or "")

    if not d_to:
        d_to = today
    if not d_from:
        d_from = d_to - timedelta(days=6)

    start = timezone.make_aware(datetime(d_from.year, d_from.month, d_from.day, 0, 0, 0), tz)
    end = timezone.make_aware(datetime(d_to.year, d_to.month, d_to.day, 0, 0, 0), tz) + timedelta(days=1)
    return start, end


@login_required
@owner_only
def owner_audit_dashboard(request):
    start, end = _parse_dt_range(request)

    actor = (request.GET.get("actor") or "").strip()
    entrypoint = (request.GET.get("entrypoint") or "").strip()
    action = (request.GET.get("action") or "").strip()
    target_app = (request.GET.get("app") or "").strip()
    target_model = (request.GET.get("model") or "").strip()
    q = (request.GET.get("q") or "").strip()

    selected_day = parse_date((request.GET.get("day") or "").strip() or "")

    base_qs = (
        AuditLog.objects
        .select_related("actor", "session")
        .filter(created_at__gte=start, created_at__lt=end)
    )

    if actor:
        base_qs = base_qs.filter(actor_id=actor)
    if entrypoint:
        base_qs = base_qs.filter(session__entrypoint=entrypoint)
    if action:
        base_qs = base_qs.filter(action=action)
    if target_app:
        base_qs = base_qs.filter(target_app__iexact=target_app)
    if target_model:
        base_qs = base_qs.filter(target_model__iexact=target_model)
    if q:
        base_qs = base_qs.filter(
            Q(title__icontains=q)
            | Q(message__icontains=q)
            | Q(path__icontains=q)
            | Q(target_id__icontains=q)
            | Q(target_app__icontains=q)
            | Q(target_model__icontains=q)
            | Q(actor__username__icontains=q)
        )

    # ---- Sidebar days summary (accurate, not limited by LIMIT) ----
    # Total per day
    day_rows = list(
        base_qs
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(total=Count("id"))
        .order_by("-day")
    )

    # Action counts per day (small range; cheap enough)
    action_keys = [k for k, _ in AuditAction.choices]
    per_day_action = (
        base_qs
        .annotate(day=TruncDate("created_at"))
        .values("day", "action")
        .annotate(c=Count("id"))
    )

    counts_map: Dict[Any, Dict[str, int]] = {}
    for r in per_day_action:
        d = r["day"]
        counts_map.setdefault(d, {})
        counts_map[d][r["action"]] = int(r["c"])

    sidebar_days = []
    for r in day_rows:
        d = r["day"]
        sidebar_days.append({
            "date": d,
            "total": int(r["total"]),
            "counts": counts_map.get(d, {}),
        })

    # Default selected day = newest day that exists
    if selected_day is None and sidebar_days:
        selected_day = sidebar_days[0]["date"]

    # ---- Main panel events: only selected day ----
    main_qs = base_qs
    if selected_day is not None:
        tz = timezone.get_current_timezone()
        day_start = timezone.make_aware(datetime(selected_day.year, selected_day.month, selected_day.day, 0, 0, 0), tz)
        day_end = day_start + timedelta(days=1)
        main_qs = main_qs.filter(created_at__gte=day_start, created_at__lt=day_end)

    main_qs = main_qs.order_by("-created_at", "-id")

    LIMIT = 800
    rows = list(main_qs[:LIMIT])

    for ev in rows:
        ev.ui = _ui_for_event(ev)


    # Grouping for main panel: single day, but keep structure same
    days = []
    if selected_day is not None:
        # create one block
        days = [{
            "date": selected_day,
            "rows": rows,
        }]

    # ---- Dropdown data (keep from filtered base, not day-limited) ----
    actor_options = (
        base_qs.exclude(actor__isnull=True)
        .values("actor_id", "actor__username")
        .distinct()[:200]
    )
    app_options = (
        base_qs.exclude(target_app="")
        .values_list("target_app", flat=True)
        .distinct()[:200]
    )
    model_options = (
        base_qs.exclude(target_model="")
        .values_list("target_model", flat=True)
        .distinct()[:200]
    )

    # Build base querystring for sidebar day links (preserve filters but not day)
    qp = request.GET.copy()
    qp.pop("day", None)
    base_qs_str = qp.urlencode()

    ctx = {
        "days": days,
        "sidebar_days": sidebar_days,
        "selected_day": selected_day,
        "base_qs": base_qs_str,
        "limit": LIMIT,
        "filters": {
            "actor": actor,
            "entrypoint": entrypoint,
            "action": action,
            "app": target_app,
            "model": target_model,
            "q": q,
            "from": timezone.localdate(start),
            "to": timezone.localdate(end - timedelta(days=1)),
        },
        "actions": AuditAction.choices,
        "entrypoints": AuditEntrypoint.choices,
        "actor_options": actor_options,
        "app_options": app_options,
        "model_options": model_options,
        "shown_count": len(rows),
    }
    return render(request, "audit_log/owner_dashboard.html", ctx)
