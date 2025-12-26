# catalog/views.py
from __future__ import annotations

from functools import wraps
from math import ceil
from typing import Iterable

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
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


# -------- constants --------
PAGE_SIZE = 20  # products per "slide" in the left pane


# ---------- helpers ----------
def _post_list(request: HttpRequest, base: str) -> list[str]:
    """
    Read repeated inputs named like base[] and return a trimmed, de-duplicated list.
    """
    seen, out = set(), []
    for v in request.POST.getlist(f"{base}[]"):
        v = (v or "").strip()
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _go(url_name: str, qs: str | None = None) -> HttpResponseRedirect:
    """small redirect helper (handles query strings safely)"""
    base = reverse(url_name)
    return HttpResponseRedirect(f"{base}?{qs}" if qs else base)


def _snap_collection(col: ProductCollection) -> dict:
    d = snap_instance(col, ["name", "code"])
    return d


def _snap_set(st: ProductSet) -> dict:
    d = snap_instance(st, ["name", "code", "collection_id"])
    d["collection_code"] = getattr(st.collection, "code", None) if getattr(st, "collection", None) else None
    d["collection_name"] = getattr(st.collection, "name", None) if getattr(st, "collection", None) else None
    return d


def _snap_product(p: Product) -> dict:
    d = snap_instance(
        p,
        [
            "name",
            "product_number",
            "is_active",
            "set_id",
            "unit_primary",
            "unit_secondary",
            "conversion_factor",
            "cost",
            "price",
            "notes",
        ],
    )
    # nice-to-have context (doesn't change business logic)
    try:
        d["display_code"] = p.display_code
    except Exception:
        pass
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

    # Block if any products exist (active or not)
    if Product.objects.filter(set__collection=col).exists():
        messages.error(request, "لا يمكن حذف الزمرة لوجود منتجات ضمنها.")
        return _go("manager_collections", "edit=1")

    before = _snap_collection(col)

    ProductSet.objects.filter(collection=col).delete()
    col.delete()

    # AUDIT: delete collection (hard)
    try:
        log_delete(
            actor=request.user,
            request=request,
            target=col,
            title="Delete collection",
            message=f"Collection deleted: {before.get('name')}",
            before=before,
            after=None,
            meta={"source": "catalog.collection_delete", "soft_delete": False},
        )
    except Exception:
        pass

    messages.success(request, "تم حذف الزمرة.")
    return _go("manager_collections", "edit=1")


# =========================
#     JSON API (UI)
# =========================
def _products_qs_for_collection(cid: int):
    return (
        Product.objects
        .filter(set__collection_id=cid, is_active=True)
        .select_related("set", "set__collection")
        .order_by("product_number")
    )


@role_required(AccountProfile.Role.MANAGER)
def api_collection_products(request: HttpRequest, cid: int) -> JsonResponse:
    """
    Returns products of a collection.
    - Default: paginated (PAGE_SIZE).
    - If ?all=1 -> returns ALL items (no pagination). JS will group by parent set.
    """
    qs = _products_qs_for_collection(cid)

    # --- NEW: all=1 returns everything (for tree view) ---
    all_flag = (request.GET.get("all") in {"1", "true", "yes"})
    if all_flag:
        items = [{
            "id": p.id,
            "code": f"{p.product_number:03d}",
            "name": p.name,
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
        "code": f"{p.product_number:03d}",
        "name": p.name,
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


def _page_for_product_in_collection(prod: Product) -> int:
    n = (
        _products_qs_for_collection(prod.set.collection_id)
        .filter(product_number__lte=prod.product_number)
        .count()
    )
    return max(1, ceil(n / PAGE_SIZE))


@role_required(AccountProfile.Role.MANAGER)
def api_product_search(request: HttpRequest) -> JsonResponse:
    q = (request.GET.get("q") or "").strip()
    mode = (request.GET.get("mode") or "barcode").strip().lower()

    def fmt(p: Product) -> dict:
        return {
            "type": "product",
            "id": p.id,
            "code": f"{p.product_number:03d}",
            "name": p.name,
            "set_id": p.set_id,
            "set_code": p.set.code,
            "set_name": p.set.name,
            "col_code": p.set.collection.code,
            "col_id": p.set.collection_id,
            "col_name": p.set.collection.name,
            "page": _page_for_product_in_collection(p),

            # pricing
            "cost": str(p.cost),
            "price": str(p.price),

            # unit info (codes + human labels)
            "unit_primary": p.unit_primary,
            "unit_primary_label": p.get_unit_primary_display(),
            "unit_secondary": p.unit_secondary or "",
            "unit_secondary_label": p.get_unit_secondary_display() if p.unit_secondary else "",
            "conversion_factor": str(p.conversion_factor or ""),
        }

    if not q:
        return JsonResponse({"ok": True, "items": []})

    items: list[dict] = []

    try:
        if mode == "barcode":
            pb = (
                ProductBarcode.objects
                .select_related("product__set__collection")
                .filter(barcode=q, product__is_active=True)
                .first()
            )
            if pb:
                item = fmt(pb.product)
                item["matched_unit"] = int(pb.unit_index)  # 1 or 2
                items = [item]

        elif mode == "name":
            max_total = 8

            prods = (
                Product.objects
                .select_related("set__collection")
                .filter(name__icontains=q, is_active=True)
                .order_by("name")[:max_total]
            )
            prod_items = [fmt(p) for p in prods]

            cols = (
                ProductCollection.objects
                .filter(name__icontains=q)
                .order_by("name")
                .values("id", "name", "code")[:3]
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
                .order_by("name")[:3]
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
            uid = (
                ProductUnitId.objects
                .select_related("product__set__collection")
                .filter(value__iexact=q, product__is_active=True)
                .first()
            )
            if uid:
                item = fmt(uid.product)
                item["matched_unit"] = int(uid.unit_index)  # 1 or 2
                items = [item]

        elif mode == "code":
            items = []
            got = False

            if q.isdigit():
                p = (
                    Product.objects.select_related("set__collection")
                    .filter(product_number=int(q), is_active=True)
                    .first()
                )
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
                    p = _products_qs_for_collection(col.id).first()
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
                    p = (
                        Product.objects.select_related("set__collection")
                        .filter(set=st, is_active=True)
                        .order_by("product_number")
                        .first()
                    )
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
                                pn = int(parts[2])
                                p = (
                                    Product.objects.select_related("set__collection")
                                    .filter(product_number=pn, set=st, is_active=True)
                                    .first()
                                )
                                if p:
                                    items = [fmt(p)]
                            except ValueError:
                                pass
        else:
            return JsonResponse({"ok": False, "error": "bad mode"}, status=400)

    except Exception:
        items = []

    return JsonResponse({"ok": True, "items": items[:8]})


# =========================
#   Product Create + Edit
# =========================
@role_required(AccountProfile.Role.MANAGER)
def manager_product_new(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = ProductCreateForm(request.POST)
        if not form.is_valid():
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
            return render(request, "manager/product_new.html", {"form": form, "editing": False})

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
            return render(request, "manager/product_new.html", {"form": form, "editing": False})

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
        if not st and create_parent:
            st = ProductSet.objects.create(collection=col, name=s_name or "مجموعة جديدة")
            # AUDIT: create set via product page (only when actually created)
            try:
                log_create(
                    actor=request.user,
                    request=request,
                    target=st,
                    title="Create set",
                    message=f"Set created: {st.name}",
                    before=None,
                    after=_snap_set(st),
                    meta={"source": "catalog.manager_product_new", "via": "create_parent"},
                )
            except Exception:
                pass

        if not st:
            form.add_error("set_name", "المجموعة الأب غير موجودة. حدِّد اسماً صحيحاً أو فعّل خيار الإنشاء.")
            return render(request, "manager/product_new.html", {"form": form, "editing": False})

        # Build product instance
        p = Product(
            name=form.cleaned_data["name"],
            set=st,
            unit_primary=form.cleaned_data["unit_primary"],
            unit_secondary=form.cleaned_data["unit_secondary"] or "",
            conversion_factor=form.cleaned_data["conversion_factor"],
            cost=form.cleaned_data["cost"],
            price=form.cleaned_data["price"],
            notes=form.cleaned_data["notes"] or "",
        )

        try:
            with transaction.atomic():
                p.full_clean()
                p.save()

                # ---- Unit IDs (lists) ----
                u1_ids = _post_list(request, "unit_primary_ids")
                u2_ids = _post_list(request, "unit_secondary_ids")

                all_ids = list(dict.fromkeys(u1_ids + u2_ids))
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
                        product=p, unit_index=ProductUnitId.UnitIndex.PRIMARY, value=val
                    )
                for val in u2_ids:
                    ProductUnitId.objects.create(
                        product=p, unit_index=ProductUnitId.UnitIndex.SECONDARY, value=val
                    )

                # ---- Barcodes (lists or textarea fallback) ----
                bar_u1 = _post_list(request, "barcodes_u1") or ProductCreateForm.parse_barcodes(
                    form.cleaned_data.get("barcodes_u1", "")
                )
                bar_u2 = _post_list(request, "barcodes_u2") or ProductCreateForm.parse_barcodes(
                    form.cleaned_data.get("barcodes_u2", "")
                )

                all_bcs = list(dict.fromkeys(bar_u1 + bar_u2))
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
                        product=p, unit_index=ProductBarcode.UnitIndex.PRIMARY, barcode=bc
                    )
                for bc in bar_u2:
                    ProductBarcode.objects.create(
                        product=p, unit_index=ProductBarcode.UnitIndex.SECONDARY, barcode=bc
                    )

            # AUDIT: create product (after everything is done)
            try:
                # refresh relations counts if needed
                p = Product.objects.select_related("set__collection").prefetch_related("barcodes", "unit_ids").get(pk=p.pk)
                log_create(
                    actor=request.user,
                    request=request,
                    target=p,
                    title="Create product",
                    message=f"Product created: {p.name} ({p.display_code})",
                    before=None,
                    after=_snap_product(p),
                    meta={"source": "catalog.manager_product_new"},
                )
            except Exception:
                pass

        except ValidationError as ve:
            mapping = {
                "conversion_factor": "conversion_factor",
                "unit_secondary": "unit_secondary",
                "name": "name",
            }
            for field, msgs in ve.message_dict.items():
                for msg in msgs:
                    form.add_error(mapping.get(field, None), msg)
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
            return render(request, "manager/product_new.html", {"form": form, "editing": False})

        except IntegrityError as e:
            emsg = str(e).lower()
            if ("catalog_product.name" in emsg) or ("product.name" in emsg) or ("unique" in emsg and "name" in emsg):
                form.add_error("name", "اسم المنتج موجود مسبقاً.")
            elif "productunitid" in emsg or "unit id" in emsg:
                messages.error(request, "أحد معرّفات الوحدات مستخدم مسبقاً.")
            elif "productbarcode" in emsg or "barcode" in emsg:
                messages.error(request, "أحد الباركودات مستخدم مسبقاً.")
            else:
                messages.error(request, "تعذّر حفظ المنتج بسبب تضارب في البيانات.")
            return render(request, "manager/product_new.html", {"form": form, "editing": False})

        messages.success(request, f"تم إنشاء المنتج «{p.name}» برقم {p.display_code}.")
        return redirect("manager_collections")

    # GET
    form = ProductCreateForm()
    return render(request, "manager/product_new.html", {"form": form, "editing": False})


@require_GET
@role_required(AccountProfile.Role.MANAGER)
def api_sets_search(request: HttpRequest) -> JsonResponse:
    """
    Global father-sets search (no cid required).
    Optional params:
      - q: substring in set name or code
      - collection_id: filter by collection
      - limit: max items (default 25)
    """
    q = (request.GET.get("q") or "").strip()
    col_id = request.GET.get("collection_id")
    try:
        limit = max(1, min(50, int(request.GET.get("limit", "25"))))
    except ValueError:
        limit = 25

    qs = ProductSet.objects.select_related("collection")
    if col_id:
        qs = qs.filter(collection_id=col_id)
    if q:
        from django.db.models import Q
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


# --- Delete product (soft archive) ---
@require_POST
@role_required(AccountProfile.Role.MANAGER)
def manager_product_delete(request: HttpRequest, pk: int) -> HttpResponse:
    p = get_object_or_404(Product.objects.select_related("set__collection"), pk=pk)
    before = _snap_product(p)

    name = p.name
    try:
        if p.is_active:
            p.is_active = False
            p.save(update_fields=["is_active"])

            # AUDIT: archive product (update)
            try:
                log_update(
                    actor=request.user,
                    request=request,
                    target=p,
                    title="Archive product",
                    message=f"Product archived: {name}",
                    before=before,
                    after=_snap_product(p),
                    meta={"source": "catalog.manager_product_delete", "soft_delete": True},
                )
            except Exception:
                pass

            messages.success(request, f"تمت أرشفة المنتج «{name}».")
        else:
            messages.info(request, f"المنتج «{name}» مؤرشف مسبقاً.")
    except Exception:
        messages.error(request, "تعذّر أرشفة المنتج.")
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
            return render(
                request,
                "manager/product_new.html",
                {"form": form, "editing": True, "product": p},
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
            return render(request, "manager/product_new.html", {"form": form, "editing": True, "product": p})

        # Resolve or create set
        st = None
        if s_name:
            st = (
                ProductSet.objects
                .annotate(n=Lower("name"))
                .filter(n=s_name.lower(), collection=col)
                .first()
            )
        if not st and create_parent:
            st = ProductSet.objects.create(collection=col, name=s_name or "مجموعة جديدة")
            # AUDIT: create set via edit page
            try:
                log_create(
                    actor=request.user,
                    request=request,
                    target=st,
                    title="Create set",
                    message=f"Set created: {st.name}",
                    before=None,
                    after=_snap_set(st),
                    meta={"source": "catalog.manager_product_edit", "via": "create_parent"},
                )
            except Exception:
                pass

        if not st:
            form.add_error("set_name", "المجموعة الأب غير موجودة. حدِّد اسماً صحيحاً أو فعّل خيار الإنشاء.")
            return render(request, "manager/product_new.html", {"form": form, "editing": True, "product": p})

        # Update fields
        p.name = form.cleaned_data["name"].strip()
        p.set = st
        p.unit_primary = form.cleaned_data["unit_primary"]
        p.unit_secondary = form.cleaned_data["unit_secondary"] or ""
        p.conversion_factor = form.cleaned_data["conversion_factor"]
        p.cost = form.cleaned_data["cost"]
        p.price = form.cleaned_data["price"]
        p.notes = form.cleaned_data["notes"] or ""

        try:
            with transaction.atomic():
                p.full_clean()
                p.save()

                # ---- Unit IDs (replace when lists posted) ----
                u1_ids = _post_list(request, "unit_primary_ids")
                u2_ids = _post_list(request, "unit_secondary_ids")

                if u1_ids:
                    ProductUnitId.objects.filter(
                        product=p, unit_index=ProductUnitId.UnitIndex.PRIMARY
                    ).delete()
                    for val in u1_ids:
                        if ProductUnitId.objects.filter(value=val).exclude(product=p).exists():
                            messages.error(request, f"معرّف الوحدة {val} مستخدم مسبقاً.")
                            raise IntegrityError("duplicate unit id")
                        ProductUnitId.objects.create(
                            product=p, unit_index=ProductUnitId.UnitIndex.PRIMARY, value=val
                        )

                if u2_ids:
                    ProductUnitId.objects.filter(
                        product=p, unit_index=ProductUnitId.UnitIndex.SECONDARY
                    ).delete()
                    for val in u2_ids:
                        if ProductUnitId.objects.filter(value=val).exclude(product=p).exists():
                            messages.error(request, f"معرّف الوحدة {val} مستخدم مسبقاً.")
                            raise IntegrityError("duplicate unit id")
                        ProductUnitId.objects.create(
                            product=p, unit_index=ProductUnitId.UnitIndex.SECONDARY, value=val
                        )

                # ---- Barcodes: replace when lists or textarea posted ----
                list_u1 = _post_list(request, "barcodes_u1")
                list_u2 = _post_list(request, "barcodes_u2")
                txt_u1 = ProductCreateForm.parse_barcodes(request.POST.get("barcodes_u1", ""))
                txt_u2 = ProductCreateForm.parse_barcodes(request.POST.get("barcodes_u2", ""))

                if list_u1 or txt_u1:
                    new_u1 = list_u1 or txt_u1
                    ProductBarcode.objects.filter(
                        product=p, unit_index=ProductBarcode.UnitIndex.PRIMARY
                    ).delete()
                    for bc in new_u1:
                        if ProductBarcode.objects.filter(barcode=bc).exclude(product=p).exists():
                            messages.error(request, f"الباركود {bc} مستخدم مسبقاً.")
                            raise IntegrityError("duplicate barcode")
                        ProductBarcode.objects.create(
                            product=p, unit_index=ProductBarcode.UnitIndex.PRIMARY, barcode=bc
                        )

                if list_u2 or txt_u2:
                    new_u2 = list_u2 or txt_u2
                    ProductBarcode.objects.filter(
                        product=p, unit_index=ProductBarcode.UnitIndex.SECONDARY
                    ).delete()
                    for bc in new_u2:
                        if ProductBarcode.objects.filter(barcode=bc).exclude(product=p).exists():
                            messages.error(request, f"الباركود {bc} مستخدم مسبقاً.")
                            raise IntegrityError("duplicate barcode")

                        ProductBarcode.objects.create(
                            product=p, unit_index=ProductBarcode.UnitIndex.SECONDARY, barcode=bc
                        )

            # AUDIT: update product
            try:
                p2 = Product.objects.select_related("set__collection").prefetch_related("barcodes", "unit_ids").get(pk=p.pk)
                log_update(
                    actor=request.user,
                    request=request,
                    target=p2,
                    title="Update product",
                    message=f"Product updated: {p2.name} ({p2.display_code})",
                    before=before,
                    after=_snap_product(p2),
                    meta={"source": "catalog.manager_product_edit"},
                )
            except Exception:
                pass

        except IntegrityError as e:
            if "product.name" in str(e).lower():
                form.add_error("name", "اسم المنتج موجود مسبقاً.")
            else:
                messages.error(request, "تعذّر حفظ التعديلات. تحقّق من المعرّفات/الباركودات المتكررة.")
            return render(request, "manager/product_new.html", {"form": form, "editing": True, "product": p})

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
        "cost": p.cost,
        "price": p.price,
        "notes": p.notes or "",
        "barcodes_u1": "",
        "barcodes_u2": "",
    }
    form = ProductCreateForm(initial=initial, instance=p)
    return render(request, "manager/product_new.html", {"form": form, "editing": True, "product": p})


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
        with transaction.atomic():
            ProductBarcode.objects.filter(product__set__collection=col).delete()
            ProductUnitId.objects.filter(product__set__collection=col).delete()
            Product.objects.filter(set__collection=col).delete()
            ProductSet.objects.filter(collection=col).delete()
            col.delete()

        # AUDIT: cascade delete collection (hard)
        try:
            log_delete(
                actor=request.user,
                request=request,
                target=col,
                title="Cascade delete collection",
                message=f"Collection cascade deleted: {before.get('name')}",
                before=before,
                after=None,
                meta={"source": "catalog.api_collection_cascade_delete", "cascade": True, "soft_delete": False},
            )
        except Exception:
            pass

    except Exception:
        return JsonResponse({"ok": False, "error": "delete failed"}, status=500)

    return JsonResponse({"ok": True})


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
        .values("id", "name", "code")[:8]
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
        .values("id", "name", "code")[:8]
    )
    return JsonResponse({"ok": True, "items": list(qs)})


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
