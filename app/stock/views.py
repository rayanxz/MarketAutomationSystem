# app/stock/views.py
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.contrib import messages
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.db.models import Q
from django.views.decorators.http import require_GET

from catalog.models import Product
from stock.models import ProductContainer, StockEntry, DEC0

from stock import services as StockSV



def _fmt_decimal(x: Decimal | None) -> str:
    """
    Format decimal like:
      2        -> "2"
      2.5      -> "2.5"
      2.250    -> "2.25"
      0        -> "0"
    Always max 3 decimals, no localization, no commas.
    """
    if x is None:
        return "0"
    q = (x or DEC0).quantize(Decimal("0.001"))  # keep 3 decimals max
    return format(q.normalize(), "f")  # normalize() removes trailing zeros/dot


@login_required
def stock_list(request):
    """
    Stock list per container, grouped by:
      Collection -> Father set -> Products
    All filtering (search / qty type) is done client-side with JS.
    """
    # ---- 1) Containers + current selection ----
    containers = list(
        ProductContainer.objects.filter(is_active=True).order_by("sort_order", "name")
    )
    if not containers:
        return render(
            request,
            "stock/stock_list.html",
            {
                "containers": [],
                "current_container": None,
                "tree": [],
                "stats": {
                    "total_products": 0,
                    "negative_count": 0,
                    "zero_count": 0,
                },
            },
        )

    requested_code = (request.GET.get("container") or "").strip()
    current = None

    if requested_code:
        current = next((c for c in containers if c.code == requested_code), None)

    if current is None:
        # try store, else first
        current = next((c for c in containers if c.is_store), containers[0])

    # ---- 2) Load entries for this container (no qty filtering here) ----
    entries_qs = (
        StockEntry.objects
        .select_related(
            "product",
            "container",
            "product__set",            # FK to ProductSet
            "product__set__collection" # its collection
        )
        .filter(container=current)
    )

    entries = list(entries_qs)

    # ---- 3) Build tree: collection -> father set -> products ----
    tree_map: dict[int, dict] = {}

    for e in entries:
        p: Product = e.product

        pset = getattr(p, "set", None)
        collection = getattr(pset, "collection", None)

        collection_id = getattr(collection, "id", None) or 0

        if collection_id not in tree_map:
            tree_map[collection_id] = {
                "collection": collection,
                "sets": {},
            }

        # father set: if there is parent, use parent as group; else use set itself
        if pset is not None:
            parent = getattr(pset, "parent", None)
            father = parent or pset
        else:
            father = None

        set_id = getattr(father, "id", None) or 0

        sets_map = tree_map[collection_id]["sets"]
        if set_id not in sets_map:
            sets_map[set_id] = {
                "set": father,
                "products": [],
            }

        qty = e.qty_primary or DEC0
        cost = getattr(p, "cost", Decimal("0.000"))
        price = getattr(p, "price", Decimal("0.000"))

        # collect barcodes into a single string for JS search
        barcodes_str = ""
        bc_manager = getattr(p, "barcodes", None)  # adjust related_name if different
        if bc_manager is not None:
            try:
                codes = []
                for b in bc_manager.all():
                    # try common field names; adjust if your model is different
                    code_val = getattr(b, "code", None) or getattr(b, "value", None) or ""
                    if code_val:
                        codes.append(str(code_val))
                barcodes_str = " ".join(codes)
            except Exception:
                barcodes_str = ""

        sets_map[set_id]["products"].append(
            {
                "entry": e,
                "product": p,
                "qty": qty,
                "qty_display": _fmt_decimal(qty),
                "cost": cost,
                "cost_display": _fmt_decimal(cost),
                "price": price,
                "price_display": _fmt_decimal(price),
                "barcodes": barcodes_str,
            }
        )

    # Convert nested dicts to lists sorted nicely
    tree: list[dict] = []
    for _c_id, c_data in tree_map.items():
        collection = c_data["collection"]
        sets_list: list[dict] = []

        for _s_id, s_data in c_data["sets"].items():
            s_obj = s_data["set"]
            prods = sorted(
                s_data["products"],
                key=lambda x: (x["product"].name or "", x["product"].id or 0),
            )
            sets_list.append(
                {
                    "set": s_obj,
                    "products": prods,
                }
            )

        sets_list.sort(
            key=lambda s: (
                (s["set"].name if s["set"] else "بدون مجموعة"),
                s["set"].id if s["set"] else 0,
            )
        )

        tree.append(
            {
                "collection": collection,
                "sets": sets_list,
            }
        )

    tree.sort(
        key=lambda c: (
            (c["collection"].name if c["collection"] else "بدون تصنيف"),
            c["collection"].id if c["collection"] else 0,
        )
    )

    # ---- 4) Simple stats for header ----
    total_products = 0
    negative_count = 0
    zero_count = 0

    for col in tree:
        for s in col["sets"]:
            for item in s["products"]:
                total_products += 1
                qty = item["qty"]
                if qty < DEC0:
                    negative_count += 1
                elif qty == DEC0:
                    zero_count += 1

    stats = {
        "total_products": total_products,
        "negative_count": negative_count,
        "zero_count": zero_count,
    }

    context = {
        "containers": containers,
        "current_container": current,
        "tree": tree,
        "stats": stats,
    }
    return render(request, "stock/stock_list.html", context)


@login_required
def stock_move(request: HttpRequest) -> HttpResponse:
    """
    Move one or more products between two containers.

    - User chooses FROM container + TO container (required, must differ)
    - User adds multiple products to a table, with transfer quantities
    - For each row, we call transfer_between_containers (ADJUSTMENT out + in)
    - Global stock stays the same; per-container stock changes.

    We allow negative stock in the source container, but this is warned
    on the frontend (and could be guarded later if you decide to block it).
    """
    containers_qs = ProductContainer.objects.filter(is_active=True).order_by("sort_order", "name")
    containers = list(containers_qs)

    if not containers:
        messages.error(request, "لا توجد حاويات معرفة بعد. قم بإنشاء حاويات أولاً.")
        return redirect("stock:stock_list")

    errors: dict[str, str] = {}
    non_field_errors: list[str] = []

    if request.method == "POST":
        from_code = (request.POST.get("from_container") or "").strip()
        to_code = (request.POST.get("to_container") or "").strip()

        from_container = next((c for c in containers if c.code == from_code), None)
        to_container = next((c for c in containers if c.code == to_code), None)

        # --- containers validation ---
        if not from_container:
            errors["from_container"] = "يجب اختيار الحاوية المصدر."
        if not to_container:
            errors["to_container"] = "يجب اختيار الحاوية الهدف."
        if from_container and to_container and from_container.id == to_container.id:
            errors["to_container"] = "لا يمكن أن تكون الحاوية المصدر هي نفسها الحاوية الهدف."

        # --- rows (products) ---
        product_ids = request.POST.getlist("product_id")
        qty_list = request.POST.getlist("qty")

        valid_rows: list[tuple[Product, Decimal]] = []

        if not product_ids:
            errors["rows"] = "يجب إضافة مادة واحدة على الأقل إلى قائمة النقل."
        else:
            for idx, (pid_raw, qty_raw) in enumerate(zip(product_ids, qty_list), start=1):
                pid_raw = (pid_raw or "").strip()
                qty_raw = (qty_raw or "").strip()

                if not pid_raw and not qty_raw:
                    # completely empty row – ignore silently
                    continue

                # product
                try:
                    pid = int(pid_raw)
                    product = Product.objects.get(id=pid)
                except (ValueError, Product.DoesNotExist):
                    errors["rows"] = f"السطر رقم {idx}: المادة المحددة غير صحيحة."
                    break

                # quantity
                try:
                    qty = Decimal(qty_raw)
                except (InvalidOperation, TypeError):
                    errors["rows"] = f"السطر رقم {idx}: قيمة الكمية غير صالحة."
                    break

                if qty <= DEC0:
                    errors["rows"] = f"السطر رقم {idx}: يجب أن تكون الكمية أكبر من صفر."
                    break

                valid_rows.append((product, qty))

            if not errors and not valid_rows:
                errors["rows"] = "لم يتم العثور على أي سطر صالح للنقل."

        note = (request.POST.get("note") or "").strip()

        # --- perform transfer if everything valid ---
        if not errors and valid_rows and from_container and to_container:
            try:
                for product, qty in valid_rows:
                    StockSV.transfer_between_containers(
                        actor=request.user,
                        product=product,
                        from_container=from_container,
                        to_container=to_container,
                        qty_primary=qty,
                    )

                messages.success(
                    request,
                    f"تم تنفيذ نقل {len(valid_rows)} مادة/مواد من «{from_container.display_label}» "
                    f"إلى «{to_container.display_label}» بنجاح."
                )
                # We don't need to keep the note for now; later you can log it to a journal.
                return redirect("stock:stock_move")

            except Exception as e:
                non_field_errors.append(f"حدث خطأ أثناء عملية النقل: {e}")

    # context for initial GET or after validation error
    context = {
        "containers": containers,
        "errors": errors,
        "non_field_errors": non_field_errors,
    }
    return render(request, "stock/stock_move.html", context)


# ========= AJAX APIs for move page =========

@login_required
@require_GET
def api_stock_product_search(request: HttpRequest) -> HttpResponse:
    """
    Lightweight search for products to add to the transfer table.

    Query params:
      q    - search term
      mode - 'name' | 'id' | 'code' | 'barcode'
    """
    q = (request.GET.get("q") or "").strip()
    mode = (request.GET.get("mode") or "name").lower()

    if not q:
        return JsonResponse({"results": []})

    qs = Product.objects.all()

    if mode == "id":
        try:
            pid = int(q)
            qs = qs.filter(id=pid)
        except ValueError:
            qs = qs.none()
    elif mode == "code":
        # adjust field name if your Product uses something else
        qs = qs.filter(code__icontains=q)
    elif mode == "barcode":
        qs = qs.filter(
            Q(barcodes__code__icontains=q) | Q(barcodes__value__icontains=q)
        ).distinct()
    else:  # name
        qs = qs.filter(name__icontains=q)

    qs = qs.select_related("set", "set__collection")[:20]

    results = []
    for p in qs:
        collection = getattr(getattr(p, "set", None), "collection", None)
        set_obj = getattr(p, "set", None)

        path_parts = []
        if collection and getattr(collection, "name", None):
            path_parts.append(collection.name)
        if set_obj and getattr(set_obj, "name", None):
            path_parts.append(set_obj.name)

        results.append(
            {
                "id": p.id,
                "name": p.name or "",
                "code": getattr(p, "display_code", "") or getattr(p, "code", "") or "",
                "path": " / ".join(path_parts),
            }
        )

    return JsonResponse({"results": results})


@login_required
@require_GET
def api_product_stock(request: HttpRequest) -> HttpResponse:
    """
    Returns per-container stock for a single product.

    Query params:
      product_id
    """
    pid_raw = (request.GET.get("product_id") or "").strip()
    try:
        pid = int(pid_raw)
        product = Product.objects.get(id=pid)
    except (ValueError, Product.DoesNotExist):
        return JsonResponse({"ok": False, "error": "المادة غير موجودة."}, status=400)

    entries = (
        StockEntry.objects
        .filter(product=product)
        .select_related("container")
        .order_by("container__sort_order", "container__name")
    )

    data = []
    for e in entries:
        c = e.container
        qty = e.qty_primary or DEC0
        data.append(
            {
                "code": c.code,
                "name": c.display_label,
                "qty": _fmt_decimal(qty),
            }
        )

    return JsonResponse(
        {
            "ok": True,
            "product": {"id": product.id, "name": product.name or ""},
            "containers": data,
        }
    )
