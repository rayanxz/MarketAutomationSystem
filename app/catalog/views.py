# catalog/views.py
from __future__ import annotations

from functools import wraps
from math import ceil
from typing import Iterable

from django.views.decorators.http import require_GET, require_POST
from django.db import transaction

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
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
            form.save()
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
        messages.success(request, "تم تعديل اسم الزمرة.")
    except IntegrityError:
        messages.error(request, "اسم الزمرة موجود مسبقاً.")
    return _go("manager_collections", "edit=1")


@role_required(AccountProfile.Role.MANAGER)
def collection_delete(request: HttpRequest, pk: int) -> HttpResponse:
    if request.method != "POST":
        return _go("manager_collections")
    col = get_object_or_404(ProductCollection, pk=pk)
    col.delete()
    messages.success(request, "تم حذف الزمرة.")
    return _go("manager_collections", "edit=1")


# =========================
#     JSON API (UI)
# =========================
def _products_qs_for_collection(cid: int):
    return (
        Product.objects
        .filter(set__collection_id=cid)
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
            "id": p.id,
            "code": f"{p.product_number:03d}",
            "name": p.name,
            "set_id": p.set_id,
            "set_code": p.set.code,
            "set_name": p.set.name,
            "col_code": p.set.collection.code,
            "col_id": p.set.collection_id,
            "col_name": p.set.collection.name,  # NEW
            "page": _page_for_product_in_collection(p),
        }


    if not q:
        return JsonResponse({"ok": True, "items": []})

    try:
        if mode == "barcode":
            pb = (
                ProductBarcode.objects
                .select_related("product__set__collection")
                .filter(barcode=q)
                .first()
            )
            items = [fmt(pb.product)] if pb else []

        elif mode == "name":
            # mixed: collections + sets + products
            max_total = 8

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

            prods = (
                Product.objects
                .select_related("set__collection")
                .filter(name__icontains=q)
                .order_by("name")[:max_total]
            )
            prod_items = [{
                "type": "product",
                "id": p.id,
                "code": f"{p.product_number:03d}",
                "name": p.name,
                "set_id": p.set_id,
                "set_code": p.set.code,
                "set_name": p.set.name,
                "col_code": p.set.collection.code,
                "col_id": p.set.collection_id,
                "col_name":p.set.collection.name,
                "page": _page_for_product_in_collection(p),
            } for p in prods]

            items = (col_items + set_items + prod_items)[:max_total]

        elif mode == "id":
            qs = (
                Product.objects
                .select_related("set__collection")
                .filter(unit_ids__value__iexact=q)
                .order_by("name")[:5]
            )
            items = [fmt(p) for p in qs]

        elif mode == "code":
            items = []
            got = False

            if q.isdigit():
                p = (
                    Product.objects.select_related("set__collection")
                    .filter(product_number=int(q))
                    .first()
                )
                if p:
                    items = [fmt(p)]; got = True

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
                        items = [fmt(p)]; got = True

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
                        .filter(set=st)
                        .order_by("product_number")
                        .first()
                    )
                    if p:
                        items = [fmt(p)]; got = True

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
                                    .filter(product_number=pn, set=st)
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
        if not st:
            form.add_error("set_name", "المجموعة الأب غير موجودة. حدِّد اسماً صحيحاً أو فعّل خيار الإنشاء.")
            return render(request, "manager/product_new.html", {"form": form, "editing": False})

        # Build product instance (name already normalized & CI-checked in form.clean_name)
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

                # Pre-check global uniqueness (nice UX; DB enforces too)
                for val in u1_ids + u2_ids:
                    if ProductUnitId.objects.filter(value=val).exists():
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
                bar_u1 = _post_list(request, "barcodes_u1")
                bar_u2 = _post_list(request, "barcodes_u2")
                if not bar_u1:
                    bar_u1 = ProductCreateForm.parse_barcodes(form.cleaned_data.get("barcodes_u1", ""))
                if not bar_u2:
                    bar_u2 = ProductCreateForm.parse_barcodes(form.cleaned_data.get("barcodes_u2", ""))

                # Pre-check duplicates
                for bc in bar_u1 + bar_u2:
                    if ProductBarcode.objects.filter(barcode=bc).exists():
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

        except ValidationError as ve:
            # Map model validation messages back onto the form where possible
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


# --- Delete product ---
@require_POST
@role_required(AccountProfile.Role.MANAGER)
def manager_product_delete(request: HttpRequest, pk: int) -> HttpResponse:
    p = get_object_or_404(Product, pk=pk)
    name = p.name
    try:
        # CASCADE takes care of ProductBarcode & ProductUnitId
        p.delete()
        messages.success(request, f"تم حذف المنتج «{name}».")
    except Exception:
        messages.error(request, "تعذّر حذف المنتج.")
    return redirect("manager_collections")



@role_required(AccountProfile.Role.MANAGER)
def manager_product_edit(request: HttpRequest, pk: int) -> HttpResponse:
    p = get_object_or_404(
            Product.objects
            .select_related("set__collection")
            .prefetch_related("unit_ids" , "barcodes"),
             pk=pk
        )

    if request.method == "POST":
        # Pass instance so name uniqueness ignores self
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
                p.full_clean()  # ensure model-level validation on edit as well
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
        # Barcodes textareas left empty so we don't replace unless user types
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
    return JsonResponse({"ok": True, "collection": {"id": col.id, "name": col.name, "code": col.code},
                         "counts": {"sets": sets_cnt, "products": prods_cnt}})

@require_POST
@role_required(AccountProfile.Role.MANAGER)
def api_collection_cascade_delete(request: HttpRequest, pk: int) -> JsonResponse:
    col = get_object_or_404(ProductCollection, pk=pk)
    try:
        with transaction.atomic():
            # 1) delete products (this cascades to barcodes/unit_ids due to FK CASCADE)
            Product.objects.filter(set__collection=col).delete()
            # 2) delete sets
            ProductSet.objects.filter(collection=col).delete()
            # 3) delete collection itself
            col.delete()
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
    return JsonResponse({"ok": True, "item": {"id": st.id, "name": st.name, "code": st.code}})