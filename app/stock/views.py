# app/stock/views.py
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.shortcuts import render, redirect
from django.contrib import messages
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.db.models import Q
from django.views.decorators.http import require_GET
from accounts.decorators import role_required
from accounts.models import AccountProfile

from catalog.models import Product
from stock.models import ProductContainer, StockEntry, StockFifoLayer, DEC0

from stock import services as StockSV

from django.db import transaction
from audit_log import services as AuditSV
from audit_log.models import AuditAction

from django.utils import timezone
from inventory.models import q3 , q4

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


@role_required(AccountProfile.Role.MANAGER)
def stock_list(request: HttpRequest) -> HttpResponse:
    """
    Stock list per container, grouped by:
      Collection -> Father set -> Products

    Supports two view modes:
      - products (default): one row per product with total qty
      - batches: product row + sub-rows for each FIFO batch (StockFifoLayer)
    All searching / qty-type filtering is still done client-side with JS.
    """
    # ---- 0) View mode: products | batches ----
    view_mode = (request.GET.get("view") or "products").lower()
    if view_mode not in ("products", "batches"):
        view_mode = "products"
    show_batches = view_mode == "batches"

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
                "view_mode": view_mode,
            },
        )

    requested_code = (request.GET.get("container") or "").strip()
    current: ProductContainer | None = None

    if requested_code:
        current = next((c for c in containers if c.code == requested_code), None)

    if current is None:
        # try store, else first
        current = next((c for c in containers if c.is_store), containers[0])

    # ---- 2) Optional: load batches per product for this container ----
    batches_by_product: dict[int, list[StockFifoLayer]] = {}
    if show_batches:
        fifo_qs = (
            StockFifoLayer.objects
            .filter(container=current, qty_remaining__gt=DEC0)
            .select_related("product")
            .order_by("-created_at", "-id")  # NEW: newest first
        )
        for layer in fifo_qs:
            batches_by_product.setdefault(layer.product_id, []).append(layer)


    # ---- 3) Load entries for this container (no qty filtering here) ----
    entries_qs = (
        StockEntry.objects
        .select_related(
            "product",
            "container",
            "product__set",            # FK to ProductSet
            "product__set__collection" # its collection
        )
        .filter(container=current)
        .prefetch_related("product__barcodes")   # avoid N+1 on barcodes
    )

    entries = list(entries_qs)

    # ---- 4) Build tree: collection -> father set -> products ----
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
        purchase_cur = (
            p.get_effective_default_purchase_currency()
            if hasattr(p, "get_effective_default_purchase_currency")
            else "SYP"
        )
        sale_cur = (
            p.get_effective_default_sale_currency()
            if hasattr(p, "get_effective_default_sale_currency")
            else "SYP"
        )
        cost = (
            p.get_default_cost_for_currency(purchase_cur)
            if hasattr(p, "get_default_cost_for_currency")
            else Decimal("0.000")
        )
        price = (
            p.get_default_price_for_currency(sale_cur)
            if hasattr(p, "get_default_price_for_currency")
            else Decimal("0.000")
        )

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

        # batches (only populated in batches view)
        batches_list: list[dict] = []
        if show_batches:
            layers = batches_by_product.get(p.id, [])
            for idx, layer in enumerate(layers, start=1):
                bqty = layer.qty_remaining or DEC0
                # same idea as api_product_batches
                source_str = f"{layer.source_app or ''} / {layer.source_model or ''} / {layer.source_id or ''}".strip(" /")
                batches_list.append(
                    {
                        "index": idx,
                        "id": layer.id,
                        "qty": _fmt_decimal(bqty),
                        "unit_cost": str(layer.unit_cost or "0.0000"),
                        "created_at": layer.created_at.strftime("%Y-%m-%d %H:%M"),
                        "source": source_str,
                    }
                )

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
                "batches": batches_list,
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

    # ---- 5) Simple stats for header (always based on product totals) ----
    total_products = 0
    negative_count = 0
    zero_count = 0

    for col in tree:
        for s in col["sets"]:
            for item in s["products"]:
                total_products += 1
                q = item["qty"]
                if q < DEC0:
                    negative_count += 1
                elif q == DEC0:
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
        "view_mode": view_mode,
    }
    return render(request, "stock/stock_list.html", context)



@role_required(AccountProfile.Role.MANAGER)
def stock_move(request: HttpRequest) -> HttpResponse:
    """
    Move one or more *batches* of products between two containers.

    - User chooses FROM container + TO container (required, must differ)
    - User adds multiple rows:
        product_id + batch_id + qty
    - For each row, we call StockSV.transfer_from_batch, which:
        * decreases that specific FIFO batch in the source container
        * creates a new batch in the target container with same cost
        * posts two ADJUSTMENT ProductMovement rows (out + in)
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

        # --- rows (product + batch + qty) ---
        product_ids = request.POST.getlist("product_id")
        batch_ids = request.POST.getlist("batch_id")
        qty_list = request.POST.getlist("qty")

        # each valid row => (batch: StockFifoLayer, qty: Decimal)
        valid_rows: list[tuple[StockFifoLayer, Decimal]] = []

        if not product_ids:
            errors["rows"] = "يجب إضافة مادة واحدة على الأقل إلى قائمة النقل."
        else:
            for idx, (pid_raw, bid_raw, qty_raw) in enumerate(
                zip(product_ids, batch_ids, qty_list),
                start=1,
            ):
                pid_raw = (pid_raw or "").strip()
                bid_raw = (bid_raw or "").strip()
                qty_raw = (qty_raw or "").strip()

                # completely empty row – ignore silently
                if not pid_raw and not bid_raw and not qty_raw:
                    continue

                # product
                try:
                    pid = int(pid_raw)
                    product = Product.objects.get(id=pid, is_active=True)
                except (ValueError, Product.DoesNotExist):
                    errors["rows"] = f"السطر رقم {idx}: المادة المحددة غير صحيحة."
                    break

                # batch
                if not bid_raw:
                    errors["rows"] = f"السطر رقم {idx}: يجب اختيار دفعة (باتش) من الحاوية المصدر."
                    break
                try:
                    bid = int(bid_raw)
                    batch = StockFifoLayer.objects.get(id=bid, product=product)
                except (ValueError, StockFifoLayer.DoesNotExist):
                    errors["rows"] = f"السطر رقم {idx}: الدفعة المحددة غير صحيحة."
                    break

                # sanity: batch must belong to from_container
                if from_container and batch.container_id != from_container.id:
                    errors["rows"] = (
                        f"السطر رقم {idx}: الدفعة لا تنتمي إلى الحاوية المصدر المحددة."
                    )
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

                # cannot move more than batch available
                batch_remain = batch.qty_remaining or DEC0
                if qty > batch_remain:
                    errors["rows"] = (
                        f"السطر رقم {idx}: الكمية المراد نقلها أكبر من الكمية المتاحة في هذه الدفعة."
                    )
                    break

                valid_rows.append((batch, qty))

            if not errors and not valid_rows:
                errors["rows"] = "لم يتم العثور على أي سطر صالح للنقل."

        note = (request.POST.get("note") or "").strip()
        # note is not stored yet; later you can log it to a journal / ledger doc

        if not errors and valid_rows and from_container and to_container:
            try:
                note = (request.POST.get("note") or "").strip()
                ref = timezone.now().strftime("TX%Y%m%d%H%M%S")

                rows_meta = []

                with transaction.atomic():
                    for i, (batch, qty) in enumerate(valid_rows, start=1):
                        mv_out, mv_in = StockSV.transfer_from_batch(
                            actor=request.user,
                            batch=batch,
                            to_container=to_container,
                            qty_primary=qty,
                            ref=ref,
                            line_no=i,
                        )

                        # Keep meta light but useful
                        rows_meta.append({
                            "line": i,
                            "product_id": batch.product_id,
                            "product_name": getattr(batch.product, "name", "") if hasattr(batch, "product") else "",
                            "batch_id": batch.id,
                            "qty_primary": str(q3(qty)),
                            "unit_cost": str(q4(batch.unit_cost or DEC0)),
                            "from_container": from_container.code,
                            "to_container": to_container.code,
                            "origin": {
                                "source_app": batch.source_app or "",
                                "source_model": batch.source_model or "",
                                "source_id": batch.source_id or "",
                                "created_at": batch.created_at.isoformat() if batch.created_at else "",
                            },
                            "movement_ids": {
                                "out": str(mv_out.id),
                                "in": str(mv_in.id),
                            },
                            "movement_source_ids": {
                                "out": mv_out.source_id,
                                "in": mv_in.source_id,
                            }
                        })

                    # ✅ One audit log for the whole transfer operation
                    AuditSV.log_event_safe(
                        action=AuditAction.INFO,
                        actor=request.user,
                        request=request,
                        title="Stock transfer between containers",
                        message=(
                            f"Transfer {len(valid_rows)} rows from {from_container.code} to {to_container.code}"
                        ),
                        source="stock.stock_move",
                        meta={
                            "kind": "stock.container_transfer",
                            "ref": ref,
                            "note": note,
                            "from": {
                                "id": from_container.id,
                                "code": from_container.code,
                                "name": from_container.display_label,
                            },
                            "to": {
                                "id": to_container.id,
                                "code": to_container.code,
                                "name": to_container.display_label,
                            },
                            "rows": rows_meta,
                        },
                    )

                messages.success(
                    request,
                    f"تم تنفيذ نقل {len(valid_rows)} مادة/دفعة من «{from_container.display_label}» "
                    f"إلى «{to_container.display_label}» بنجاح."
                )
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

@role_required(AccountProfile.Role.MANAGER)
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

    qs = Product.objects.filter(is_active=True)

    if mode == "id":
        try:
            pid = int(q)
            qs = qs.filter(id=pid)
        except ValueError:
            qs = qs.none()
    elif mode == "code":
        qs = qs.filter(id=int(q)) if q.isdigit() else qs.none()
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
                "code": p.id,
                "path": " / ".join(path_parts),
            }
        )

    return JsonResponse({"results": results})


@role_required(AccountProfile.Role.MANAGER)
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
        product = Product.objects.get(id=pid, is_active=True)
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


@role_required(AccountProfile.Role.MANAGER)
@require_GET
def api_product_batches(request: HttpRequest) -> HttpResponse:
    """
    Returns FIFO batches (StockFifoLayer) for a product in a given container.

    Query params:
      product_id
      container  (container.code)
    """
    pid_raw = (request.GET.get("product_id") or "").strip()
    cont_code = (request.GET.get("container") or "").strip()

    # product
    try:
        pid = int(pid_raw)
        product = Product.objects.get(id=pid, is_active=True)
    except (ValueError, Product.DoesNotExist):
        return JsonResponse(
            {"ok": False, "error": "المادة غير موجودة."},
            status=400,
        )

    # container
    try:
        container = ProductContainer.objects.get(code=cont_code, is_active=True)
    except ProductContainer.DoesNotExist:
        return JsonResponse(
            {"ok": False, "error": "الحاوية غير موجودة أو غير مفعّلة."},
            status=400,
        )

    layers = (
        StockFifoLayer.objects
        .filter(product=product, container=container, qty_remaining__gt=DEC0)
        .order_by("created_at", "id")
    )

    batches = []
    total_qty = DEC0

    for layer in layers:
        qty = layer.qty_remaining or DEC0
        total_qty += qty
        batches.append(
            {
                "id": layer.id,
                "qty": _fmt_decimal(qty),
                # cost is 4-decimal; we send as string
                "unit_cost": str(layer.unit_cost or "0.0000"),
                "created_at": layer.created_at.isoformat(),
                "source": f"{layer.source_app or ''} / {layer.source_model or ''} / {layer.source_id or ''}".strip(" /"),
            }
        )

    return JsonResponse(
        {
            "ok": True,
            "product": {
                "id": product.id,
                "name": product.name or "",
            },
            "container": {
                "code": container.code,
                "name": container.display_label,
            },
            "total_qty": _fmt_decimal(total_qty),
            "batches": batches,
        }
    )
