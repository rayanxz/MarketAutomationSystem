# catalog/edit_api.py
from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation

from django.db import transaction, IntegrityError
from django.db.models import F, Value
from django.db.models.deletion import ProtectedError
from django.db.models.functions import Lower, Greatest
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from accounts.models import AccountProfile
from catalog.models import ProductCollection, ProductSet, Product, ProductBarcode, ProductUnitId
from catalog.views import role_required


ALLOWED_TYPES  = {"collection", "set"}
ALLOWED_FIELDS = {"price", "cost"}
ALLOWED_MODES  = {"percent", "absolute"}
ALLOWED_SIGNS  = {"+", "-"}


def _scope_qs(scope_type: str, scope_id: int):
    if scope_type == "collection":
        return Product.objects.filter(set__collection_id=scope_id)
    if scope_type == "set":
        return Product.objects.filter(set_id=scope_id)
    raise ValueError("bad scope")


def _as_int(x) -> int | None:
    try:
        return int(x)
    except Exception:
        return None


def _as_decimal(x) -> Decimal | None:
    try:
        return Decimal(str(x))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _get_or_create_trash_set(col: ProductCollection) -> ProductSet:
    """Find (case-insensitive) or create a 'Trash' set in the given collection."""
    trash = (
        ProductSet.objects
        .filter(collection=col)
        .annotate(n=Lower("name"))
        .filter(n="سلة المحذوفات")
        .first()
    )
    if not trash:
        trash = ProductSet.objects.create(collection=col, name="سلة المحذوفات")
    return trash


@require_POST
@role_required(AccountProfile.Role.MANAGER)
def edit_apply_batch(request):
    """
    Accepts JSON:
    {
      "ops": [
        {"op":"rename", "type":"collection"|"set", "id":123, "name":"New name"},
        {"op":"adjust", "type":"collection"|"set", "id":123, "field":"price"|"cost",
         "mode":"percent"|"absolute", "delta": 10.0, "sign":"+"|"-"},
        {"op":"delete", "type":"collection"|"set", "id":123}
      ]
    }

    Runs in a single transaction and returns per-op results:
    {"ok": true, "results": [{"ok":true}, {"ok":false,"error":"..."} , ...]}
    """
    # ---- parse payload ----
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
        ops = payload.get("ops") or []
        if not isinstance(ops, list):
            return JsonResponse({"ok": False, "error": "ops must be a list"}, status=400)
    except Exception:
        return JsonResponse({"ok": False, "error": "bad json"}, status=400)

    results: list[dict] = []

    # We want "all-or-nothing" for the entire batch
    try:
        with transaction.atomic():
            for op in ops:
                kind = (op.get("op") or "").strip().lower()
                typ  = (op.get("type") or "").strip().lower()
                _id  = _as_int(op.get("id"))

                if kind not in {"rename", "adjust", "delete"}:
                    results.append({"ok": False, "error": "bad op"})
                    continue
                if typ not in ALLOWED_TYPES or not _id:
                    results.append({"ok": False, "error": "bad type/id"})
                    continue

                # =========================
                #          RENAME
                # =========================
                if kind == "rename":
                    new_name = (op.get("name") or "").strip()
                    if not new_name:
                        results.append({"ok": False, "error": "empty name"})
                        continue

                    if typ == "collection":
                        col = ProductCollection.objects.filter(id=_id).first()
                        if not col:
                            results.append({"ok": False, "error": "collection not found"})
                            continue
                        # CI uniqueness
                        exists = (
                            ProductCollection.objects
                            .exclude(pk=col.pk)
                            .annotate(n=Lower("name"))
                            .filter(n=new_name.lower())
                            .exists()
                        )
                        if exists:
                            results.append({"ok": False, "error": "collection name exists"})
                            continue
                        col.name = new_name
                        col.full_clean()
                        col.save(update_fields=["name"])
                        results.append({"ok": True})

                    else:  # set
                        st = ProductSet.objects.select_related("collection").filter(id=_id).first()
                        if not st:
                            results.append({"ok": False, "error": "set not found"})
                            continue
                        # unique per collection (CI)
                        exists = (
                            ProductSet.objects
                            .exclude(pk=st.pk)
                            .filter(collection=st.collection)
                            .annotate(n=Lower("name"))
                            .filter(n=new_name.lower())
                            .exists()
                        )
                        if exists:
                            results.append({"ok": False, "error": "set name exists in collection"})
                            continue
                        st.name = new_name
                        st.full_clean()
                        st.save(update_fields=["name"])
                        results.append({"ok": True})

                # =========================
                #          ADJUST
                # =========================
                elif kind == "adjust":
                    field = (op.get("field") or "").strip().lower()
                    mode  = (op.get("mode") or "").strip().lower()
                    sign  = (op.get("sign") or "").strip()

                    delta = _as_decimal(op.get("delta"))
                    if field not in ALLOWED_FIELDS or mode not in ALLOWED_MODES or sign not in ALLOWED_SIGNS:
                        results.append({"ok": False, "error": "bad params"})
                        continue
                    if delta is None or delta <= 0:
                        results.append({"ok": False, "error": "delta must be > 0"})
                        continue

                    qs = _scope_qs(typ, _id)

                    if mode == "percent":
                        # factor = 1 +/- (delta/100)
                        factor = Decimal("1") + (delta / Decimal("100")) * (Decimal("1") if sign == "+" else Decimal("-1"))
                        # clamp at zero to avoid negative prices/costs
                        qs.update(**{
                            field: Greatest(F(field) * factor, Value(Decimal("0")))
                        })
                    else:
                        expr = F(field) + delta if sign == "+" else F(field) - delta
                        qs.update(**{
                            field: Greatest(expr, Value(Decimal("0")))
                        })

                    results.append({"ok": True, "affected": qs.count()})

                # =========================
                #          DELETE
                # =========================
                elif kind == "delete":
                    if typ == "collection":
                        col = ProductCollection.objects.filter(id=_id).first()
                        if not col:
                            results.append({"ok": False, "error": "collection not found"})
                            continue

                        # This operation is intentionally conservative here.
                        # Full destructive collection delete is handled by
                        # api_collection_cascade_delete (views.py).
                        if Product.objects.filter(set__collection=col).exists():
                            results.append({"ok": False, "error": "collection has products; deletion blocked"})
                            continue

                        ProductSet.objects.filter(collection=col).delete()
                        col.delete()
                        results.append({"ok": True})

                    else:
                        # DELETE a SET (father set) with FK-safe behavior.
                        st = ProductSet.objects.select_related("collection").filter(id=_id).first()
                        if not st:
                            results.append({"ok": False, "error": "set not found"})
                            continue

                        # All products inside the set
                        qs_products = Product.objects.filter(set=st)

                        try:
                            with transaction.atomic():
                                # Free globally-unique children to avoid future import collisions
                                ProductBarcode.objects.filter(product__in=qs_products).delete()
                                ProductUnitId.objects.filter(product__in=qs_products).delete()

                                # Try hard delete; if any product is referenced, this will raise ProtectedError
                                qs_products.delete()

                                # If we reached here, nothing protected → delete the set itself
                                st.delete()
                                results.append({"ok": True})
                                continue

                        except ProtectedError:
                            # Some products are referenced elsewhere (billing/ledger/...).
                            # Fall back to: move products to a Trash set in the SAME collection and archive them.
                            with transaction.atomic():
                                trash = _get_or_create_trash_set(st.collection)

                                # (barcodes/unit_ids already removed above – frees uniqueness)
                                Product.objects.filter(set=st).update(set=trash, is_active=False)

                                # Now the original set has no products pointing to it → delete it
                                st.delete()

                            results.append({"ok": True, "note": "moved referenced products to trash"})
                            continue

                        except IntegrityError as e:
                            results.append({"ok": False, "error": f"delete failed: {e.__class__.__name__}"})
                            continue

    except Exception:
        # If anything in the batch fails, return a generic error (transaction rolls back).
        return JsonResponse({"ok": False, "error": "apply failed"}, status=500)

    return JsonResponse({"ok": True, "results": results})
