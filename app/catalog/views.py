# catalog/views.py
from __future__ import annotations

from functools import wraps
from math import ceil
from decimal import Decimal
from typing import Iterable

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.models import Q
from django.db.models.functions import Lower
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseForbidden,
    HttpResponseRedirect,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from accounts.models import AccountProfile
from accounts.utils import profile_for
from catalog.forms import CollectionCreateForm, ProductCreateForm
from catalog.models import (
    ProductCollection,
    ProductSet,
    Product,
    ProductBarcode,
    ProductUnitId,
)

from audit_log.services import log_create, log_update, log_delete, snap_instance
from inventory.models import q3
from financials import services as FinancialsSV
from catalog.services.deletion_policy import (
    CollectionHardDeleteBlockedError,
    ProductDisableBlockedError,
    ProductHardDeleteBlockedError,
    can_hard_delete,
    hard_delete_collection,
    disable_product,
    hard_delete_product,
    reactivate_product,
)


# -------- constants --------
PAGE_SIZE = 20  # products per "slide" in the left pane


# ---------- helpers ----------
def _predicted_next_product_id() -> int:
    def _fallback_max_plus_one() -> int:
        max_id = Product.objects.order_by("-id").values_list("id", flat=True).first()
        return (int(max_id) + 1) if max_id is not None else 1

    vendor = (connection.vendor or "").lower()
    table_name = Product._meta.db_table

    try:
        with connection.cursor() as cur:
            if vendor == "postgresql":
                cur.execute("SELECT pg_get_serial_sequence(%s, %s)", [table_name, "id"])
                row = cur.fetchone() or ()
                seq_name = row[0] if row else None
                if not seq_name:
                    return _fallback_max_plus_one()
                cur.execute(f"SELECT last_value, is_called FROM {seq_name}")
                seq_row = cur.fetchone() or ()
                if not seq_row:
                    return _fallback_max_plus_one()
                last_value = int(seq_row[0])
                is_called = bool(seq_row[1])
                return last_value + (1 if is_called else 0)

            if vendor == "sqlite":
                cur.execute("SELECT seq FROM sqlite_sequence WHERE name = %s", [table_name])
                row = cur.fetchone()
                if not row or row[0] is None:
                    return 1
                return int(row[0]) + 1
    except Exception:
        return _fallback_max_plus_one()

    return _fallback_max_plus_one()


def _post_list(request: HttpRequest, base: str) -> list[str]:
    """
    Read repeated inputs named like base[] and return a trimmed, de-duplicated list.
    """
    seen, out = set(), []
    vals = request.POST.getlist(f"{base}[]") or request.POST.getlist(base)
    for v in vals:
        v = (v or "").strip()
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _go(url_name: str, qs: str | None = None) -> HttpResponseRedirect:
    """small redirect helper (handles query strings safely)"""
    base = reverse(url_name)
    return HttpResponseRedirect(f"{base}?{qs}" if qs else base)



def _current_fx_rate_for_ui() -> str:
    try:
        return str(FinancialsSV.get_current_fx_syp_per_usd())
    except Exception:
        return ""


def _render_product_new_form(request: HttpRequest, context: dict) -> HttpResponse:
    context.setdefault("fx_rate_syp_per_usd", _current_fx_rate_for_ui())
    return render(request, "manager/product_new.html", context)


def _snap_collection(col: ProductCollection) -> dict:
    d = snap_instance(col, ["name", "code"])
    return d


def _snap_set(st: ProductSet) -> dict:
    d = snap_instance(st, ["name", "code", "collection_id"])
    d["collection_code"] = getattr(st.collection, "code", None) if getattr(st, "collection", None) else None
    d["collection_name"] = getattr(st.collection, "name", None) if getattr(st, "collection", None) else None
    return d


def _snap_product(p: Product) -> dict:
    eff_purchase_cur = (
        p.get_effective_default_purchase_currency()
        if hasattr(p, "get_effective_default_purchase_currency")
        else (getattr(p, "default_purchase_currency", None) or "SYP")
    )
    eff_sale_cur = (
        p.get_effective_default_sale_currency()
        if hasattr(p, "get_effective_default_sale_currency")
        else (getattr(p, "default_sale_currency", None) or "SYP")
    )
    eff_cost = (
        p.get_default_cost_for_currency(eff_purchase_cur)
        if hasattr(p, "get_default_cost_for_currency")
        else Decimal("0")
    )
    eff_price = (
        p.get_default_price_for_currency(eff_sale_cur)
        if hasattr(p, "get_default_price_for_currency")
        else Decimal("0")
    )
    d = snap_instance(
        p,
        [
            "name",
            "is_active",
            "set_id",
            "unit_primary",
            "unit_secondary",
            "conversion_factor",
            "cost_syp",
            "cost_usd",
            "price_syp",
            "price_usd",
            "allow_syp_sales",
            "allow_syp_purchasing",
            "allow_usd_sales",
            "allow_usd_purchasing",
            "default_purchase_currency",
            "default_sale_currency",
            "default_cost_syp",
            "default_cost_usd",
            "default_price_syp",
            "default_price_usd",
            "latest_cost_syp",
            "latest_cost_usd",
            "latest_price_syp",
            "latest_price_usd",
            "enable_syp",
            "enable_usd",
            "default_currency",
            "notes",
        ],
    )
    d["cost"] = eff_cost
    d["price"] = eff_price
    d["effective_default_purchase_currency"] = eff_purchase_cur
    d["effective_default_sale_currency"] = eff_sale_cur
    d["code"] = p.id
    try:
        d["set_code"] = getattr(p.set, "code", None)
        d["set_name"] = getattr(p.set, "name", None)
        d["collection_id"] = getattr(p.set, "collection_id", None)
        d["collection_code"] = getattr(p.set.collection, "code", None)
        d["collection_name"] = getattr(p.set.collection, "name", None)
    except Exception:
        pass
    try:
        d["barcodes_count"] = p.barcodes.count()
    except Exception:
        pass
    try:
        d["unit_ids_count"] = p.unit_ids.count()
    except Exception:
        pass
    return d


def _collect_ids_barcodes_from_post(request: HttpRequest) -> tuple[list[str], list[str], list[str], list[str]]:
    u1_ids = _post_list(request, "unit_primary_ids")
    u2_ids = _post_list(request, "unit_secondary_ids")
    bar_u1 = _post_list(request, "barcodes_u1")
    bar_u2 = _post_list(request, "barcodes_u2")
    return u1_ids, u2_ids, bar_u1, bar_u2


def _collect_ids_barcodes_from_product(p: Product) -> tuple[list[str], list[str], list[str], list[str]]:
    u1_ids = list(
        ProductUnitId.objects
        .filter(product=p, unit_index=ProductUnitId.UnitIndex.PRIMARY)
        .order_by("id")
        .values_list("value", flat=True)
    )
    u2_ids = list(
        ProductUnitId.objects
        .filter(product=p, unit_index=ProductUnitId.UnitIndex.SECONDARY)
        .order_by("id")
        .values_list("value", flat=True)
    )
    bar_u1 = list(
        ProductBarcode.objects
        .filter(product=p, unit_index=ProductBarcode.UnitIndex.PRIMARY)
        .order_by("id")
        .values_list("barcode", flat=True)
    )
    bar_u2 = list(
        ProductBarcode.objects
        .filter(product=p, unit_index=ProductBarcode.UnitIndex.SECONDARY)
        .order_by("id")
        .values_list("barcode", flat=True)
    )
    return u1_ids, u2_ids, bar_u1, bar_u2


def _validate_unit_ids_and_barcodes(
    *,
    form: ProductCreateForm,
    product: Product | None,
    u1_ids: list[str],
    u2_ids: list[str],
    bar_u1: list[str],
    bar_u2: list[str],
) -> bool:
    ok = True

    # Intra-product uniqueness (U1 vs U2)
    dup_ids = sorted(set(u1_ids) & set(u2_ids))
    if dup_ids:
        msg = f"لا يمكن استخدام نفس المعرّف في الوحدتين: {', '.join(dup_ids)}"
        form.add_error("unit_primary_ids", msg)
        form.add_error("unit_secondary_ids", msg)
        ok = False

    dup_barcodes = sorted(set(bar_u1) & set(bar_u2))
    if dup_barcodes:
        msg = f"لا يمكن استخدام نفس الباركود في الوحدتين: {', '.join(dup_barcodes)}"
        form.add_error("barcodes_u1", msg)
        form.add_error("barcodes_u2", msg)
        ok = False

    # Global uniqueness (exclude current product on edit)
    all_ids = list(dict.fromkeys(u1_ids + u2_ids))
    if all_ids:
        qs = ProductUnitId.objects.filter(value__in=all_ids)
        if product is not None:
            qs = qs.exclude(product=product)
        taken_ids = set(qs.values_list("value", flat=True))
        if taken_ids:
            offending_u1 = [v for v in u1_ids if v in taken_ids]
            offending_u2 = [v for v in u2_ids if v in taken_ids]
            if offending_u1:
                form.add_error("unit_primary_ids", f"هذه المعرّفات مستخدمة مسبقاً: {', '.join(offending_u1)}")
                ok = False
            if offending_u2:
                form.add_error("unit_secondary_ids", f"هذه المعرّفات مستخدمة مسبقاً: {', '.join(offending_u2)}")
                ok = False

    all_barcodes = list(dict.fromkeys(bar_u1 + bar_u2))
    if all_barcodes:
        qs = ProductBarcode.objects.filter(barcode__in=all_barcodes)
        if product is not None:
            qs = qs.exclude(product=product)
        taken_bc = set(qs.values_list("barcode", flat=True))
        if taken_bc:
            offending_u1 = [v for v in bar_u1 if v in taken_bc]
            offending_u2 = [v for v in bar_u2 if v in taken_bc]
            if offending_u1:
                form.add_error("barcodes_u1", f"الباركودات التالية مستخدمة مسبقاً: {', '.join(offending_u1)}")
                ok = False
            if offending_u2:
                form.add_error("barcodes_u2", f"الباركودات التالية مستخدمة مسبقاً: {', '.join(offending_u2)}")
                ok = False

    return ok


def _validation_messages(exc: ValidationError) -> list[str]:
    msgs: list[str] = []
    error_dict = getattr(exc, "error_dict", None)
    if error_dict:
        for errs in error_dict.values():
            for e in errs:
                msg = getattr(e, "message", None) or str(e)
                if msg:
                    msgs.append(str(msg))
    if not msgs:
        for msg in getattr(exc, "messages", []) or []:
            if msg:
                msgs.append(str(msg))
    if not msgs:
        msgs = [str(exc)]
    return msgs


def _bind_validation_error_to_form(
    form: ProductCreateForm,
    exc: ValidationError,
    *,
    mapping: dict[str, str] | None = None,
) -> None:
    mapping = mapping or {}
    error_dict = getattr(exc, "error_dict", None)
    if error_dict:
        for field, errs in error_dict.items():
            target = mapping.get(field, field if field in form.fields else None)
            for e in errs:
                msg = getattr(e, "message", None) or str(e)
                form.add_error(target, msg)
        return
    for msg in _validation_messages(exc):
        form.add_error(None, msg)

# ---------- role gate (manager; owner allowed by default) ----------
def role_required(*roles: Iterable[str], allow_owner: bool = True):
    if not roles:
        roles = (
            AccountProfile.Role.OWNER,
            AccountProfile.Role.MANAGER,
            AccountProfile.Role.CASHIER,
        )
    else:
        roles = tuple(roles)

    def decorator(view_func):
        @wraps(view_func)
        def wrapped(request: HttpRequest, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect("login")
            profile = profile_for(request.user)
            if profile is None:
                return redirect("login")
            if profile.has_any_role(roles, allow_owner=allow_owner):
                return view_func(request, *args, **kwargs)
            return HttpResponseForbidden("ليست لديك صلاحية للوصول إلى هذه الصفحة.")
        return wrapped
    return decorator


# ---------- Collections (زمر) ----------
@role_required(AccountProfile.Role.MANAGER)
def manager_collections(request: HttpRequest) -> HttpResponse:
    show_add = (request.GET.get("add") in {"1", "true", "yes"})

    if request.method == "POST":
        form = CollectionCreateForm(request.POST)
        show_add = True
        if form.is_valid():
            col = form.save()

            # AUDIT: create collection
            try:
                log_create(
                    actor=request.user,
                    request=request,
                    target=col,
                    title="Create collection",
                    message=f"Collection created: {col.name}",
                    before=None,
                    after=_snap_collection(col),
                    meta={"source": "catalog.manager_collections"},
                )
            except Exception:
                # never break UX if audit fails
                pass

            messages.success(request, "تم إنشاء الزمرة.")
            return _go("manager_collections")
        messages.error(request, "حدث خطأ. يرجى التحقق من الاسم.")
    else:
        form = CollectionCreateForm()

    collections = (
        ProductCollection.objects
        .only("id", "name", "code")
        .order_by("name")
        .values("id", "name", "code")
    )
    ctx = {
        "form": form,
        "collections": list(collections),
        "show_add": show_add,
    }
    return render(request, "manager/collections_list.html", ctx)

@role_required(AccountProfile.Role.MANAGER)
def collection_rename(request: HttpRequest, pk: int) -> HttpResponse:
    if request.method != "POST":
        return _go("manager_collections")

    col = get_object_or_404(ProductCollection, pk=pk)
    before = _snap_collection(col)

    new_name = (request.POST.get("name") or "").strip()
    if not new_name:
        messages.error(request, "يرجى إدخال اسم صالح.")
        return _go("manager_collections", "edit=1")

    if new_name.lower() == col.name.lower():
        messages.info(request, "لا يوجد أي تغيير في الاسم.")
        return _go("manager_collections", "edit=1")

    exists = (
        ProductCollection.objects
        .annotate(n=Lower("name"))
        .filter(n=new_name.lower())
        .exists()
    )
    if exists:
        messages.error(request, "اسم الزمرة موجود مسبقاً.")
        return _go("manager_collections", "edit=1")

    col.name = new_name
    try:
        col.save()

        # AUDIT: rename/update collection
        try:
            log_update(
                actor=request.user,
                request=request,
                target=col,
                title="Rename collection",
                message=f"Collection renamed to: {col.name}",
                before=before,
                after=_snap_collection(col),
                meta={"source": "catalog.collection_rename"},
            )
        except Exception:
            pass

        messages.success(request, "تم تعديل اسم الزمرة.")
    except IntegrityError:
        messages.error(request, "اسم الزمرة موجود مسبقاً.")
    return _go("manager_collections", "edit=1")


@role_required(AccountProfile.Role.MANAGER)
def collection_delete(request: HttpRequest, pk: int) -> HttpResponse:
    if request.method != "POST":
        return _go("manager_collections")
    col = get_object_or_404(ProductCollection, pk=pk)

    before = _snap_collection(col)
    try:
        deleted_sets, deleted_products = hard_delete_collection(col)
    except CollectionHardDeleteBlockedError:
        messages.error(
            request,
            "لا يمكن الحذف النهائي، بعض عناصر الزمرة/المجموعة الأب تحتوي على بيانات في النظام.",
        )
        return _go("manager_collections", "edit=1")

    try:
        log_delete(
            actor=request.user,
            request=request,
            target=col,
            title="Hard delete collection",
            message=f"Collection hard deleted: {before.get('name')}",
            before=before,
            after=None,
            meta={
                "source": "catalog.collection_delete",
                "hard_delete": True,
                "deleted_sets": deleted_sets,
                "deleted_products": deleted_products,
            },
        )
    except Exception:
        pass
    messages.success(request, "Collection hard deleted.")

    return _go("manager_collections", "edit=1")


# =========================
#     JSON API (UI)
# =========================
def _products_qs_for_collection(cid: int, *, show_disabled: bool = False):
    qs = (
        Product.objects
        .filter(set__collection_id=cid)
        .select_related("set", "set__collection")
        .order_by("id")
    )
    if not show_disabled:
        qs = qs.filter(is_active=True)
    return qs


@role_required(AccountProfile.Role.MANAGER)
def api_collection_products(request: HttpRequest, cid: int) -> JsonResponse:
    """
    Returns products of a collection.
    - Default: paginated (PAGE_SIZE).
    - If ?all=1 -> returns ALL items (no pagination). JS will group by parent set.
    """
    show_disabled = (request.GET.get("show_disabled") in {"1", "true", "yes"})
    qs = _products_qs_for_collection(cid, show_disabled=show_disabled)

    # --- NEW: all=1 returns everything (for tree view) ---
    all_flag = (request.GET.get("all") in {"1", "true", "yes"})
    if all_flag:
        items = [{
            "id": p.id,
            "code": p.id,
            "name": p.name,
            "is_active": bool(p.is_active),
            "set": {"id": p.set_id, "code": p.set.code, "name": p.set.name},
            "collection": {"id": cid},
        } for p in qs]
        return JsonResponse({"ok": True, "items": items, "total": len(items), "page": 1, "total_pages": 1})

    # --- existing paginated response (kept for your old list/pager) ---
    try:
        page = max(1, int(request.GET.get("page", "1")))
    except ValueError:
        page = 1

    total = qs.count()
    total_pages = max(1, ceil(total / PAGE_SIZE))
    page = min(page, total_pages)
    off = (page - 1) * PAGE_SIZE

    items = [{
        "id": p.id,
        "code": p.id,
        "name": p.name,
        "is_active": bool(p.is_active),
        "set": {"id": p.set_id, "code": p.set.code, "name": p.set.name},
        "collection": {"id": cid},
    } for p in qs[off:off + PAGE_SIZE]]

    return JsonResponse({
        "ok": True,
        "items": items,
        "total": total,
        "page": page,
        "total_pages": total_pages
    })


def _page_for_product_in_collection(prod: Product, *, show_disabled: bool = False) -> int:
    n = (
        _products_qs_for_collection(prod.set.collection_id, show_disabled=show_disabled)
        .filter(id__lte=prod.id)
        .count()
    )
    return max(1, ceil(n / PAGE_SIZE))


@role_required(AccountProfile.Role.MANAGER)
def api_product_search(request: HttpRequest) -> JsonResponse:
    q = (request.GET.get("q") or "").strip()
    mode = (request.GET.get("mode") or "barcode").strip().lower()
    show_disabled = (request.GET.get("show_disabled") in {"1", "true", "yes"})

    def fmt(p: Product) -> dict:
        single_unit = bool(getattr(p, "is_single_unit", False))
        effective_purchase_currency = (
            p.get_effective_default_purchase_currency()
            if hasattr(p, "get_effective_default_purchase_currency")
            else "SYP"
        )
        effective_sale_currency = (
            p.get_effective_default_sale_currency()
            if hasattr(p, "get_effective_default_sale_currency")
            else "SYP"
        )
        effective_cost = (
            p.get_default_cost_for_currency(effective_purchase_currency)
            if hasattr(p, "get_default_cost_for_currency")
            else Decimal("0")
        )
        effective_price = (
            p.get_default_price_for_currency(effective_sale_currency)
            if hasattr(p, "get_default_price_for_currency")
            else Decimal("0")
        )
        return {
            "type": "product",
            "id": p.id,
            "code": p.id,
            "name": p.name,
            "set_id": p.set_id,
            "set_code": p.set.code,
            "set_name": p.set.name,
            "col_code": p.set.collection.code,
            "col_id": p.set.collection_id,
            "col_name": p.set.collection.name,
            "page": _page_for_product_in_collection(p, show_disabled=show_disabled),
            "is_active": bool(p.is_active),

            # pricing
            "cost": str(effective_cost),
            "price": str(effective_price),

            # unit info (codes + human labels)
            "unit_primary": p.unit_primary,
            "unit_primary_label": p.get_unit_primary_display(),
            "unit_secondary": "" if single_unit else (p.unit_secondary or ""),
            "unit_secondary_label": "" if single_unit else (p.get_unit_secondary_display() if p.unit_secondary else ""),
            "conversion_factor": "1" if single_unit else str(p.conversion_factor or ""),
        }

    if not q:
        return JsonResponse({"ok": True, "items": []})

    items: list[dict] = []

    try:
        if mode == "barcode":
            pb_qs = (
                ProductBarcode.objects
                .select_related("product__set__collection")
                .filter(barcode=q)
            )
            if not show_disabled:
                pb_qs = pb_qs.filter(product__is_active=True)
            pb = pb_qs.first()
            if pb:
                item = fmt(pb.product)
                item["matched_unit"] = 1 if pb.product.is_single_unit else int(pb.unit_index)  # 1 or 2
                items = [item]

        elif mode == "name":
            max_total = 20
            scope = (request.GET.get("scope") or "all").strip().lower()

            if scope == "product":
                prods = Product.objects.select_related("set__collection").filter(name__icontains=q)
                if not show_disabled:
                    prods = prods.filter(is_active=True)
                prods = prods.order_by("name")[:max_total]
                items = [fmt(p) for p in prods]
            elif scope == "set":
                sets = (
                    ProductSet.objects
                    .select_related("collection")
                    .filter(name__icontains=q)
                    .order_by("name")[:max_total]
                )
                items = [{
                    "type": "set",
                    "id": s.id,
                    "name": s.name,
                    "set_code": s.code,
                    "col_id": s.collection_id,
                    "col_code": s.collection.code,
                    "col_name": s.collection.name,
                } for s in sets]
            elif scope == "collection":
                cols = (
                    ProductCollection.objects
                    .filter(name__icontains=q)
                    .order_by("name")
                    .values("id", "name", "code")[:max_total]
                )
                items = [{
                    "type": "collection",
                    "id": c["id"],
                    "name": c["name"],
                    "col_code": c["code"],
                    "col_id": c["id"],
                } for c in cols]
            else:
                mix_each = 6
                prod_limit = max_total - (mix_each * 2)

                prods = Product.objects.select_related("set__collection").filter(name__icontains=q)
                if not show_disabled:
                    prods = prods.filter(is_active=True)
                prods = prods.order_by("name")[:prod_limit]
                prod_items = [fmt(p) for p in prods]

                cols = (
                    ProductCollection.objects
                    .filter(name__icontains=q)
                    .order_by("name")
                    .values("id", "name", "code")[:mix_each]
                )
                col_items = [{
                    "type": "collection",
                    "id": c["id"],
                    "name": c["name"],
                    "col_code": c["code"],
                    "col_id": c["id"],
                } for c in cols]

                sets = (
                    ProductSet.objects
                    .select_related("collection")
                    .filter(name__icontains=q)
                    .order_by("name")[:mix_each]
                )
                set_items = [{
                    "type": "set",
                    "id": s.id,
                    "name": s.name,
                    "set_code": s.code,
                    "col_id": s.collection_id,
                    "col_code": s.collection.code,
                    "col_name": s.collection.name,
                } for s in sets]

                items = (prod_items + col_items + set_items)[:max_total]

        elif mode == "id":
            uid_qs = (
                ProductUnitId.objects
                .select_related("product__set__collection")
                .filter(value__iexact=q)
            )
            if not show_disabled:
                uid_qs = uid_qs.filter(product__is_active=True)
            uid = uid_qs.first()
            if uid:
                item = fmt(uid.product)
                item["matched_unit"] = 1 if uid.product.is_single_unit else int(uid.unit_index)  # 1 or 2
                items = [item]

        elif mode == "code":
            items = []
            got = False

            if q.isdigit():
                p_qs = Product.objects.select_related("set__collection").filter(id=int(q))
                if not show_disabled:
                    p_qs = p_qs.filter(is_active=True)
                p = p_qs.first()
                if p:
                    items = [fmt(p)]
                    got = True

            if not got and q.startswith("#") and "-" not in q:
                col = (
                    ProductCollection.objects
                    .annotate(n=Lower("code"))
                    .filter(n=q.lower())
                    .first()
                )
                if col:
                    p = _products_qs_for_collection(col.id, show_disabled=show_disabled).first()
                    if p:
                        items = [fmt(p)]
                        got = True

            if not got and q.startswith("@") and "-" not in q:
                st = (
                    ProductSet.objects.select_related("collection")
                    .annotate(n=Lower("code"))
                    .filter(n=q.lower())
                    .first()
                )
                if st:
                    p_qs = (
                        Product.objects.select_related("set__collection")
                        .filter(set=st)
                        .order_by("id")
                    )
                    if not show_disabled:
                        p_qs = p_qs.filter(is_active=True)
                    p = p_qs.first()
                    if p:
                        items = [fmt(p)]
                        got = True

            if not got and "-" in q:
                parts = q.split("-")
                if len(parts) == 3 and parts[0].startswith("#") and parts[1].startswith("@"):
                    col = (
                        ProductCollection.objects
                        .annotate(n=Lower("code"))
                        .filter(n=parts[0].lower())
                        .first()
                    )
                    if col:
                        st = (
                            ProductSet.objects
                            .annotate(n=Lower("code"))
                            .filter(n=parts[1].lower(), collection=col)
                            .first()
                        )
                        if st:
                            try:
                                pid = int(parts[2])
                                p_qs = (
                                    Product.objects.select_related("set__collection")
                                    .filter(id=pid, set=st)
                                )
                                if not show_disabled:
                                    p_qs = p_qs.filter(is_active=True)
                                p = p_qs.first()
                                if p:
                                    items = [fmt(p)]
                            except ValueError:
                                pass
        else:
            return JsonResponse({"ok": False, "error": "bad mode"}, status=400)

    except Exception:
        items = []

    return JsonResponse({"ok": True, "items": items[:20]})


# =========================
#   Product Create + Edit
# =========================
@role_required(AccountProfile.Role.MANAGER)
def manager_product_new(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = ProductCreateForm(request.POST)
        if not form.is_valid():
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
            u1_ids, u2_ids, bar_u1, bar_u2 = _collect_ids_barcodes_from_post(request)
            return _render_product_new_form(request, {
                    "form": form,
                    "editing": False,
                    "u1_ids": u1_ids,
                    "u2_ids": u2_ids,
                    "bar_u1": bar_u1,
                    "bar_u2": bar_u2,
                    "predicted_product_id": _predicted_next_product_id(),
                },
            )

        # Resolve collection (CI)
        c_name = form.cleaned_data["collection_name"].strip()
        col = (
            ProductCollection.objects
            .annotate(n=Lower("name"))
            .filter(n=c_name.lower())
            .first()
        )
        if not col:
            form.add_error("collection_name", "الزمرة غير موجودة.")
            u1_ids, u2_ids, bar_u1, bar_u2 = _collect_ids_barcodes_from_post(request)
            return _render_product_new_form(request, {
                    "form": form,
                    "editing": False,
                    "u1_ids": u1_ids,
                    "u2_ids": u2_ids,
                    "bar_u1": bar_u1,
                    "bar_u2": bar_u2,
                    "predicted_product_id": _predicted_next_product_id(),
                },
            )

        # Resolve or create set
        s_name = (form.cleaned_data["set_name"] or "").strip()
        create_parent = form.cleaned_data["create_parent"]
        st = None
        if s_name:
            st = (
                ProductSet.objects
                .annotate(n=Lower("name"))
                .filter(n=s_name.lower(), collection=col)
                .first()
            )
        if not st and not create_parent:
            form.add_error("set_name", "المجموعة الأب غير موجودة. حدِّد اسماً صحيحاً أو فعّل خيار الإنشاء.")
            u1_ids, u2_ids, bar_u1, bar_u2 = _collect_ids_barcodes_from_post(request)
            return _render_product_new_form(request, {
                    "form": form,
                    "editing": False,
                    "u1_ids": u1_ids,
                    "u2_ids": u2_ids,
                    "bar_u1": bar_u1,
                    "bar_u2": bar_u2,
                    "predicted_product_id": _predicted_next_product_id(),
                },
            )

        # Build product instance
        u1_ids, u2_ids, bar_u1, bar_u2 = _collect_ids_barcodes_from_post(request)
        if not _validate_unit_ids_and_barcodes(
            form=form,
            product=None,
            u1_ids=u1_ids,
            u2_ids=u2_ids,
            bar_u1=bar_u1,
            bar_u2=bar_u2,
        ):
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
            return _render_product_new_form(request, {
                    "form": form,
                    "editing": False,
                    "u1_ids": u1_ids,
                    "u2_ids": u2_ids,
                    "bar_u1": bar_u1,
                    "bar_u2": bar_u2,
                    "predicted_product_id": _predicted_next_product_id(),
                },
            )
        default_cost_syp = form.cleaned_data.get("default_cost_syp") or Decimal("0")
        default_cost_usd = form.cleaned_data.get("default_cost_usd") or Decimal("0")
        default_price_syp = form.cleaned_data.get("default_price_syp") or Decimal("0")
        default_price_usd = form.cleaned_data.get("default_price_usd") or Decimal("0")
        created_set = None
        p: Product | None = None

        try:
            with transaction.atomic():
                if not st and create_parent:
                    try:
                        st = ProductSet.objects.create(collection=col, name=s_name or "مجموعة جديدة")
                        created_set = st
                    except ValidationError as ve:
                        raise ValidationError({"set_name": _validation_messages(ve)})
                p = Product(
                    name=form.cleaned_data["name"],
                    set=st,
                    unit_primary=form.cleaned_data["unit_primary"],
                    unit_secondary=form.cleaned_data["unit_secondary"] or "",
                    conversion_factor=form.cleaned_data["conversion_factor"],
                    cost_syp=default_cost_syp,
                    cost_usd=default_cost_usd,
                    price_syp=default_price_syp,
                    price_usd=default_price_usd,
                    allow_syp_sales=bool(form.cleaned_data.get("allow_syp_sales")),
                    allow_syp_purchasing=bool(form.cleaned_data.get("allow_syp_purchasing")),
                    allow_usd_sales=bool(form.cleaned_data.get("allow_usd_sales")),
                    allow_usd_purchasing=bool(form.cleaned_data.get("allow_usd_purchasing")),
                    default_purchase_currency=form.cleaned_data.get("default_purchase_currency") or None,
                    default_sale_currency=form.cleaned_data.get("default_sale_currency") or None,
                    default_cost_syp=default_cost_syp,
                    default_cost_usd=default_cost_usd,
                    default_price_syp=default_price_syp,
                    default_price_usd=default_price_usd,
                    notes=form.cleaned_data["notes"] or "",
                )
                p.full_clean()
                p.save()

                # ---- Unit IDs (lists) ----
                single_unit = not bool(form.cleaned_data.get("unit_secondary"))

                all_ids = list(dict.fromkeys(u1_ids + (u2_ids if not single_unit else [])))
                if all_ids:
                    existing_ids = set(
                        ProductUnitId.objects
                        .filter(value__in=all_ids)
                        .values_list("value", flat=True)
                    )
                    for val in all_ids:
                        if val in existing_ids:
                            messages.error(request, f"معرّف الوحدة {val} مستخدم مسبقاً.")
                            raise IntegrityError("duplicate unit id")

                for val in u1_ids:
                    ProductUnitId.objects.create(
                        product=p,
                        unit_index=ProductUnitId.UnitIndex.PRIMARY,
                        value=val,
                        is_active=p.is_active,
                    )
                if not single_unit:
                    for val in u2_ids:
                        ProductUnitId.objects.create(
                            product=p,
                            unit_index=ProductUnitId.UnitIndex.SECONDARY,
                            value=val,
                            is_active=p.is_active,
                        )

                # ---- Barcodes (lists) ----

                all_bcs = list(dict.fromkeys(bar_u1 + (bar_u2 if not single_unit else [])))
                if all_bcs:
                    existing_bcs = set(
                        ProductBarcode.objects
                        .filter(barcode__in=all_bcs)
                        .values_list("barcode", flat=True)
                    )
                    for bc in all_bcs:
                        if bc in existing_bcs:
                            messages.error(request, f"الباركود {bc} مستخدم مسبقاً.")
                            raise IntegrityError("duplicate barcode")

                for bc in bar_u1:
                    ProductBarcode.objects.create(
                        product=p,
                        unit_index=ProductBarcode.UnitIndex.PRIMARY,
                        barcode=bc,
                        is_active=p.is_active,
                    )
                if not single_unit:
                    for bc in bar_u2:
                        ProductBarcode.objects.create(
                            product=p,
                            unit_index=ProductBarcode.UnitIndex.SECONDARY,
                            barcode=bc,
                            is_active=p.is_active,
                        )

            if created_set is not None:
                # AUDIT: create set via product page (only when actually created)
                try:
                    log_create(
                        actor=request.user,
                        request=request,
                        target=created_set,
                        title="Create set",
                        message=f"Set created: {created_set.name}",
                        before=None,
                        after=_snap_set(created_set),
                        meta={"source": "catalog.manager_product_new", "via": "create_parent"},
                    )
                except Exception:
                    pass

            # AUDIT: create product (after everything is done)
            try:
                # refresh relations counts if needed
                p = Product.objects.select_related("set__collection").prefetch_related("barcodes", "unit_ids").get(pk=p.pk)
                log_create(
                    actor=request.user,
                    request=request,
                    target=p,
                    title="Create product",
                    message=f"Product created: {p.name} ({p.id})",
                    before=None,
                    after=_snap_product(p),
                    meta={"source": "catalog.manager_product_new"},
                )
            except Exception:
                pass

        except ValidationError as ve:
            _bind_validation_error_to_form(
                form,
                ve,
                mapping={
                    "conversion_factor": "conversion_factor",
                    "unit_secondary": "unit_secondary",
                    "name": "name",
                    "set": "set_name",
                },
            )
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
            return _render_product_new_form(request, {
                    "form": form,
                    "editing": False,
                    "u1_ids": u1_ids,
                    "u2_ids": u2_ids,
                    "bar_u1": bar_u1,
                    "bar_u2": bar_u2,
                    "predicted_product_id": _predicted_next_product_id(),
                },
            )

        except IntegrityError as e:
            emsg = str(e).lower()
            if ("productset" in emsg) or ("uq_set_name_ci_per_collection" in emsg):
                form.add_error("set_name", "اسم المجموعة الأب موجود مسبقاً ضمن نفس الزمرة.")
            elif ("catalog_product.name" in emsg) or ("product.name" in emsg) or ("unique" in emsg and "name" in emsg):
                form.add_error("name", "اسم المنتج موجود مسبقاً.")
            elif "productunitid" in emsg or "unit id" in emsg:
                messages.error(request, "أحد معرّفات الوحدات مستخدم مسبقاً.")
            elif "productbarcode" in emsg or "barcode" in emsg:
                messages.error(request, "أحد الباركودات مستخدم مسبقاً.")
            else:
                messages.error(request, "تعذّر حفظ المنتج بسبب تضارب في البيانات.")
            return _render_product_new_form(request, {
                    "form": form,
                    "editing": False,
                    "u1_ids": u1_ids,
                    "u2_ids": u2_ids,
                    "bar_u1": bar_u1,
                    "bar_u2": bar_u2,
                    "predicted_product_id": _predicted_next_product_id(),
                },
            )

        messages.success(request, f"تم إنشاء المنتج «{p.name}».")
        return redirect("manager_collections")

    form = ProductCreateForm()
    return _render_product_new_form(request, {
            "form": form,
            "editing": False,
            "u1_ids": [],
            "u2_ids": [],
            "bar_u1": [],
            "bar_u2": [],
            "predicted_product_id": _predicted_next_product_id(),
        },
    )


@require_POST
@role_required(AccountProfile.Role.MANAGER)
def manager_product_delete(request: HttpRequest, pk: int) -> HttpResponse:
    p = get_object_or_404(
        Product.objects.select_related("set__collection"),
        pk=pk,
    )
    before = _snap_product(p)

    try:
        p_after, stock_map = disable_product(p)
        stock_meta = {k: str(q3(v)) for k, v in stock_map.items()}
        try:
            log_delete(
                actor=request.user,
                request=request,
                target=p_after,
                title="Disable product",
                message=f"Product disabled: {p_after.name}",
                before=before,
                after=_snap_product(p_after),
                meta={
                    "source": "catalog.manager_product_delete",
                    "disable": True,
                    "stock_by_container": stock_meta,
                },
            )
        except Exception:
            pass
        messages.info(request, f"?? ????? ?????? ?{p_after.name}?.")
        return redirect("manager_collections")
    except ProductDisableBlockedError:
        return redirect(f"{reverse('manager_product_edit', args=[p.id])}?delete_blocked=1")
    except Exception:
        messages.error(request, "????? ??? ??????.")
        return redirect("manager_collections")


@require_POST
@role_required(AccountProfile.Role.MANAGER)
def manager_product_reactivate(request: HttpRequest, pk: int) -> HttpResponse:
    p = get_object_or_404(Product, pk=pk)
    before = _snap_product(p)
    try:
        p_after = reactivate_product(p)
        try:
            log_update(
                actor=request.user,
                request=request,
                target=p_after,
                title="Reactivate product",
                message=f"Product reactivated: {p_after.name}",
                before=before,
                after=_snap_product(p_after),
                meta={
                    "source": "catalog.manager_product_reactivate",
                    "reactivate": True,
                },
            )
        except Exception:
            pass
        messages.success(request, f"?? ????? ????? ?????? ?{p_after.name}?.")
        return redirect("manager_collections")
    except IntegrityError:
        messages.error(request, "????? ????? ????? ?????? ???? ????? ?? ?????????/????????.")
        return redirect(f"{reverse('manager_product_edit', args=[p.id])}")
    except Exception:
        messages.error(request, "????? ????? ????? ??????.")
        return redirect("manager_collections")


@require_POST
@role_required(AccountProfile.Role.MANAGER)
def manager_product_hard_delete(request: HttpRequest, pk: int) -> HttpResponse:
    p = get_object_or_404(Product, pk=pk)
    before = _snap_product(p)
    pname = before.get("name", p.name)
    try:
        stock_map, has_history = hard_delete_product(p)
        stock_meta = {k: str(q3(v)) for k, v in stock_map.items()}
        try:
            log_delete(
                actor=request.user,
                request=request,
                target=p,
                title="Hard delete product",
                message=f"Product hard deleted: {pname}",
                before=before,
                after=None,
                meta={
                    "source": "catalog.manager_product_hard_delete",
                    "hard_delete": True,
                    "has_history": bool(has_history),
                    "stock_by_container": stock_meta,
                },
            )
        except Exception:
            pass
        messages.success(request, f"?? ????? ??????? ?????? ?{pname}?.")
        return redirect("manager_collections")
    except ProductHardDeleteBlockedError:
        messages.error(
            request,
            "????? ??????? ??? ?????: ???? ?? ??? ???? ?????/????? ??? ?????? ??? ?? ?? ???????.",
        )
        return redirect(f"{reverse('manager_product_edit', args=[pk])}")
    except Exception:
        messages.error(request, "????? ????? ??????? ??????.")
        return redirect("manager_collections")


@role_required(AccountProfile.Role.MANAGER)
def manager_product_edit(request: HttpRequest, pk: int) -> HttpResponse:
    p = get_object_or_404(
        Product.objects
        .select_related("set__collection")
        .prefetch_related("unit_ids", "barcodes"),
        pk=pk
    )

    if request.method == "POST":
        before = _snap_product(p)

        form = ProductCreateForm(request.POST, instance=p)
        if not form.is_valid():
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
            u1_ids, u2_ids, bar_u1, bar_u2 = _collect_ids_barcodes_from_post(request)
            return _render_product_new_form(request, {
                    "form": form,
                    "editing": True,
                    "product": p,
                    "product_has_history": p.has_history(),
                    "u1_ids": u1_ids,
                    "u2_ids": u2_ids,
                    "bar_u1": bar_u1,
                    "bar_u2": bar_u2,
                },
            )

        c_name = form.cleaned_data["collection_name"].strip()
        s_name = (form.cleaned_data["set_name"] or "").strip()
        create_parent = form.cleaned_data["create_parent"]

        # Resolve collection (CI)
        col = (
            ProductCollection.objects
            .annotate(n=Lower("name"))
            .filter(n=c_name.lower())
            .first()
        )
        if not col:
            form.add_error("collection_name", "الزمرة غير موجودة.")
            u1_ids, u2_ids, bar_u1, bar_u2 = _collect_ids_barcodes_from_post(request)
            return _render_product_new_form(request, {
                    "form": form,
                    "editing": True,
                    "product": p,
                    "product_has_history": p.has_history(),
                    "u1_ids": u1_ids,
                    "u2_ids": u2_ids,
                    "bar_u1": bar_u1,
                    "bar_u2": bar_u2,
                },
            )

        # Resolve or create set
        st = None
        if s_name:
            st = (
                ProductSet.objects
                .annotate(n=Lower("name"))
                .filter(n=s_name.lower(), collection=col)
                .first()
            )
        if not st and not create_parent:
            form.add_error("set_name", "المجموعة الأب غير موجودة. حدِّد اسماً صحيحاً أو فعّل خيار الإنشاء.")
            u1_ids, u2_ids, bar_u1, bar_u2 = _collect_ids_barcodes_from_post(request)
            return _render_product_new_form(request, {
                    "form": form,
                    "editing": True,
                    "product": p,
                    "product_has_history": p.has_history(),
                    "u1_ids": u1_ids,
                    "u2_ids": u2_ids,
                    "bar_u1": bar_u1,
                    "bar_u2": bar_u2,
                },
            )

        # Update fields
        u1_ids, u2_ids, bar_u1, bar_u2 = _collect_ids_barcodes_from_post(request)
        if not _validate_unit_ids_and_barcodes(
            form=form,
            product=p,
            u1_ids=u1_ids,
            u2_ids=u2_ids,
            bar_u1=bar_u1,
            bar_u2=bar_u2,
        ):
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
            return _render_product_new_form(request, {
                    "form": form,
                    "editing": True,
                    "product": p,
                    "product_has_history": p.has_history(),
                    "u1_ids": u1_ids,
                    "u2_ids": u2_ids,
                    "bar_u1": bar_u1,
                    "bar_u2": bar_u2,
                },
            )

        p.name = form.cleaned_data["name"].strip()
        p.set = st
        p.unit_primary = form.cleaned_data["unit_primary"]
        p.unit_secondary = form.cleaned_data["unit_secondary"] or ""
        p.conversion_factor = form.cleaned_data["conversion_factor"]
        default_cost_syp = form.cleaned_data.get("default_cost_syp") or Decimal("0")
        default_cost_usd = form.cleaned_data.get("default_cost_usd") or Decimal("0")
        default_price_syp = form.cleaned_data.get("default_price_syp") or Decimal("0")
        default_price_usd = form.cleaned_data.get("default_price_usd") or Decimal("0")
        created_set = None

        p.cost_syp = default_cost_syp
        p.cost_usd = default_cost_usd
        p.price_syp = default_price_syp
        p.price_usd = default_price_usd
        p.allow_syp_sales = bool(form.cleaned_data.get("allow_syp_sales"))
        p.allow_syp_purchasing = bool(form.cleaned_data.get("allow_syp_purchasing"))
        p.allow_usd_sales = bool(form.cleaned_data.get("allow_usd_sales"))
        p.allow_usd_purchasing = bool(form.cleaned_data.get("allow_usd_purchasing"))
        p.default_purchase_currency = form.cleaned_data.get("default_purchase_currency") or None
        p.default_sale_currency = form.cleaned_data.get("default_sale_currency") or None
        p.default_cost_syp = default_cost_syp
        p.default_cost_usd = default_cost_usd
        p.default_price_syp = default_price_syp
        p.default_price_usd = default_price_usd
        # latest_* are read-only (do not override here)
        p.notes = form.cleaned_data["notes"] or ""

        try:
            with transaction.atomic():
                if not st and create_parent:
                    try:
                        st = ProductSet.objects.create(collection=col, name=s_name or "مجموعة جديدة")
                        created_set = st
                    except ValidationError as ve:
                        raise ValidationError({"set_name": _validation_messages(ve)})
                p.set = st
                p.full_clean()
                p.save()

                # ---- Unit IDs (replace when lists posted) ----
                # Already validated + parsed above
                single_unit = not bool(form.cleaned_data.get("unit_secondary"))

                ProductUnitId.objects.filter(
                    product=p, unit_index=ProductUnitId.UnitIndex.PRIMARY
                ).delete()
                for val in u1_ids:
                    if ProductUnitId.objects.filter(value=val).exclude(product=p).exists():
                        messages.error(request, f"معرّف الوحدة {val} مستخدم مسبقاً.")
                        raise IntegrityError("duplicate unit id")
                    ProductUnitId.objects.create(
                        product=p,
                        unit_index=ProductUnitId.UnitIndex.PRIMARY,
                        value=val,
                        is_active=p.is_active,
                    )

                ProductUnitId.objects.filter(
                    product=p, unit_index=ProductUnitId.UnitIndex.SECONDARY
                ).delete()
                if not single_unit:
                    for val in u2_ids:
                        if ProductUnitId.objects.filter(value=val).exclude(product=p).exists():
                            messages.error(request, f"معرّف الوحدة {val} مستخدم مسبقاً.")
                            raise IntegrityError("duplicate unit id")
                        ProductUnitId.objects.create(
                            product=p,
                            unit_index=ProductUnitId.UnitIndex.SECONDARY,
                            value=val,
                            is_active=p.is_active,
                        )

                # ---- Barcodes: replace when lists or textarea posted ----
                list_u1 = bar_u1
                list_u2 = bar_u2

                ProductBarcode.objects.filter(
                    product=p, unit_index=ProductBarcode.UnitIndex.PRIMARY
                ).delete()
                for bc in list_u1:
                    if ProductBarcode.objects.filter(barcode=bc).exclude(product=p).exists():
                        messages.error(request, f"الباركود {bc} مستخدم مسبقاً.")
                        raise IntegrityError("duplicate barcode")
                    ProductBarcode.objects.create(
                        product=p,
                        unit_index=ProductBarcode.UnitIndex.PRIMARY,
                        barcode=bc,
                        is_active=p.is_active,
                    )

                ProductBarcode.objects.filter(
                    product=p, unit_index=ProductBarcode.UnitIndex.SECONDARY
                ).delete()
                if not single_unit:
                    for bc in list_u2:
                        if ProductBarcode.objects.filter(barcode=bc).exclude(product=p).exists():
                            messages.error(request, f"الباركود {bc} مستخدم مسبقاً.")
                            raise IntegrityError("duplicate barcode")
                        ProductBarcode.objects.create(
                            product=p,
                            unit_index=ProductBarcode.UnitIndex.SECONDARY,
                            barcode=bc,
                            is_active=p.is_active,
                        )

            if created_set is not None:
                # AUDIT: create set via edit page
                try:
                    log_create(
                        actor=request.user,
                        request=request,
                        target=created_set,
                        title="Create set",
                        message=f"Set created: {created_set.name}",
                        before=None,
                        after=_snap_set(created_set),
                        meta={"source": "catalog.manager_product_edit", "via": "create_parent"},
                    )
                except Exception:
                    pass

            # AUDIT: update product
            try:
                p2 = Product.objects.select_related("set__collection").prefetch_related("barcodes", "unit_ids").get(pk=p.pk)
                log_update(
                    actor=request.user,
                    request=request,
                    target=p2,
                    title="Update product",
                    message=f"Product updated: {p2.name} ({p2.id})",
                    before=before,
                    after=_snap_product(p2),
                    meta={"source": "catalog.manager_product_edit"},
                )
            except Exception:
                pass

        except ValidationError as ve:
            _bind_validation_error_to_form(
                form,
                ve,
                mapping={
                    "conversion_factor": "conversion_factor",
                    "unit_secondary": "unit_secondary",
                    "name": "name",
                    "set": "set_name",
                },
            )
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
            return _render_product_new_form(request, {
                    "form": form,
                    "editing": True,
                    "product": p,
                    "product_has_history": p.has_history(),
                    "u1_ids": u1_ids,
                    "u2_ids": u2_ids,
                    "bar_u1": bar_u1,
                    "bar_u2": bar_u2,
                },
            )

        except IntegrityError as e:
            emsg = str(e).lower()
            if ("productset" in emsg) or ("uq_set_name_ci_per_collection" in emsg):
                form.add_error("set_name", "اسم المجموعة الأب موجود مسبقاً ضمن نفس الزمرة.")
            elif "product.name" in emsg:
                form.add_error("name", "اسم المنتج موجود مسبقاً.")
            else:
                messages.error(request, "تعذّر حفظ التعديلات. تحقّق من المعرّفات/الباركودات المتكررة.")
            return _render_product_new_form(request, {
                    "form": form,
                    "editing": True,
                    "product": p,
                    "product_has_history": p.has_history(),
                    "u1_ids": u1_ids,
                    "u2_ids": u2_ids,
                    "bar_u1": bar_u1,
                    "bar_u2": bar_u2,
                },
            )

        messages.success(request, f"تم حفظ التعديلات للمنتج «{p.name}».")
        return redirect("manager_collections")

    # GET → prefill
    initial = {
        "collection_name": p.set.collection.name,
        "set_name": p.set.name,
        "create_parent": False,
        "name": p.name,
        "unit_primary": p.unit_primary,
        "unit_secondary": p.unit_secondary or "",
        "conversion_factor": p.conversion_factor,
        "cost_syp": p.cost_syp,
        "cost_usd": p.cost_usd,
        "price_syp": p.price_syp,
        "price_usd": p.price_usd,
        "allow_syp_sales": p.allow_syp_sales,
        "allow_syp_purchasing": p.allow_syp_purchasing,
        "allow_usd_sales": p.allow_usd_sales,
        "allow_usd_purchasing": p.allow_usd_purchasing,
        "default_purchase_currency": p.default_purchase_currency or "",
        "default_sale_currency": p.default_sale_currency or "",
        "default_cost_syp": p.default_cost_syp,
        "default_cost_usd": p.default_cost_usd,
        "default_price_syp": p.default_price_syp,
        "default_price_usd": p.default_price_usd,
        "notes": p.notes or "",
    }
    form = ProductCreateForm(initial=initial, instance=p)
    u1_ids, u2_ids, bar_u1, bar_u2 = _collect_ids_barcodes_from_product(p)
    delete_blocked = (request.GET.get("delete_blocked") in {"1", "true", "yes"})
    hard_delete_allowed = can_hard_delete(p)
    return _render_product_new_form(request, {
            "form": form,
            "editing": True,
            "product": p,
            "product_has_history": p.has_history(),
            "u1_ids": u1_ids,
            "u2_ids": u2_ids,
            "bar_u1": bar_u1,
            "bar_u2": bar_u2,
            "delete_blocked": delete_blocked,
            "hard_delete_allowed": hard_delete_allowed,
        },
    )


# ========================
#     Autocomplete APIs
# ========================
@require_GET
@role_required(AccountProfile.Role.MANAGER)
def api_collection_stats(request: HttpRequest, pk: int) -> JsonResponse:
    col = get_object_or_404(ProductCollection, pk=pk)
    sets_cnt = ProductSet.objects.filter(collection=col).count()
    prods_cnt = Product.objects.filter(set__collection=col).count()
    return JsonResponse({
        "ok": True,
        "collection": {"id": col.id, "name": col.name, "code": col.code},
        "counts": {"sets": sets_cnt, "products": prods_cnt},
    })


@require_POST
@role_required(AccountProfile.Role.MANAGER)
def api_collection_cascade_delete(request: HttpRequest, pk: int) -> JsonResponse:
    col = get_object_or_404(ProductCollection, pk=pk)
    before = _snap_collection(col)

    try:
        deleted_sets, deleted_products = hard_delete_collection(col)
    except CollectionHardDeleteBlockedError:
        return JsonResponse(
            {
                "ok": False,
                "error": "لا يمكن الحذف النهائي، بعض عناصر الزمرة/المجموعة الأب تحتوي على بيانات في النظام.",
            },
            status=400,
        )
    except Exception:
        return JsonResponse({"ok": False, "error": "delete failed"}, status=500)

    try:
        log_delete(
            actor=request.user,
            request=request,
            target=col,
            title="Hard delete collection",
            message=f"Collection hard deleted: {before.get('name')}",
            before=before,
            after=None,
            meta={
                "source": "catalog.api_collection_cascade_delete",
                "cascade": True,
                "hard_delete": True,
                "deleted_sets": deleted_sets,
                "deleted_products": deleted_products,
            },
        )
    except Exception:
        pass

    return JsonResponse(
        {
            "ok": True,
            "message": "Collection hard deleted.",
            "deleted_sets": deleted_sets,
            "deleted_products": deleted_products,
        }
    )


@require_GET
@role_required(AccountProfile.Role.MANAGER)
def api_collections_ac(request: HttpRequest) -> JsonResponse:
    q = (request.GET.get("q") or "").strip()
    if not q:
        return JsonResponse({"ok": True, "items": []})
    qs = (
        ProductCollection.objects
        .filter(name__icontains=q)
        .order_by("name")
        .values("id", "name", "code")[:20]
    )
    return JsonResponse({"ok": True, "items": list(qs)})


@require_GET
@role_required(AccountProfile.Role.MANAGER)
def api_sets_ac(request: HttpRequest) -> JsonResponse:
    q = (request.GET.get("q") or "").strip()
    cid = request.GET.get("cid")
    if not q or not cid:
        return JsonResponse({"ok": True, "items": []})
    qs = (
        ProductSet.objects
        .filter(collection_id=cid, name__icontains=q)
        .order_by("name")
        .values("id", "name", "code")[:20]
    )
    return JsonResponse({"ok": True, "items": list(qs)})


@require_GET
@role_required(AccountProfile.Role.MANAGER)
def api_sets_search(request: HttpRequest) -> JsonResponse:
    """
    Global father-sets search (no cid required).
    Optional params:
      - q: substring in set name or code
      - collection_id / cid: filter by collection
      - limit: max items (default 25)
    """
    q = (request.GET.get("q") or "").strip()
    col_id = request.GET.get("collection_id") or request.GET.get("cid")
    try:
        limit = max(1, min(50, int(request.GET.get("limit", "25"))))
    except ValueError:
        limit = 25

    qs = ProductSet.objects.select_related("collection")
    if col_id:
        qs = qs.filter(collection_id=col_id)
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(code__icontains=q))

    qs = qs.order_by("collection__name", "name")[:limit]
    items = [{
        "id": s.id,
        "name": s.name,
        "code": s.code,
        "collection": {
            "id": s.collection_id,
            "code": s.collection.code,
            "name": s.collection.name,
        },
    } for s in qs]
    return JsonResponse({"ok": True, "items": items})


@require_POST
@role_required(AccountProfile.Role.MANAGER)
def api_sets_create(request: HttpRequest) -> JsonResponse:
    name = (request.POST.get("name") or "").strip()
    cid = request.POST.get("cid")
    if not name or not cid:
        return JsonResponse({"ok": False, "error": "bad params"}, status=400)

    col = ProductCollection.objects.filter(id=cid).first()
    if not col:
        return JsonResponse({"ok": False, "error": "collection not found"}, status=404)

    exists = (
        ProductSet.objects
        .annotate(n=Lower("name"))
        .filter(n=name.lower(), collection=col)
        .exists()
    )
    if exists:
        return JsonResponse({"ok": False, "error": "exists"}, status=409)

    st = ProductSet.objects.create(collection=col, name=name)

    # AUDIT: create set via API
    try:
        st2 = ProductSet.objects.select_related("collection").get(pk=st.pk)
        log_create(
            actor=request.user,
            request=request,
            target=st2,
            title="Create set",
            message=f"Set created: {st2.name}",
            before=None,
            after=_snap_set(st2),
            meta={"source": "catalog.api_sets_create"},
        )
    except Exception:
        pass

    return JsonResponse({"ok": True, "item": {"id": st.id, "name": st.name, "code": st.code}})

