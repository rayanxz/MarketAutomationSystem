# catalog/edit_api.py
from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from django.db import transaction, IntegrityError
from django.db.models import F, Value
from django.db.models.deletion import ProtectedError
from django.db.models.functions import Lower, Greatest
from django.http import JsonResponse, HttpRequest
from django.views.decorators.http import require_POST

from accounts.models import AccountProfile
from catalog.models import (
    ProductCollection,
    ProductSet,
    Product,
    ProductBarcode,
    ProductUnitId,
)
from catalog.views import role_required

from audit_log.services import log_update, log_delete, snap_instance

ALLOWED_TYPES = {"collection", "set"}
ALLOWED_FIELDS = {"price", "cost"}
ALLOWED_MODES = {"percent", "absolute"}
ALLOWED_SIGNS = {"+", "-"}


# ------------------------
# helpers
# ------------------------
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


def _scope_qs(scope_type: str, scope_id: int):
    if scope_type == "collection":
        return Product.objects.filter(set__collection_id=scope_id)
    if scope_type == "set":
        return Product.objects.filter(set_id=scope_id)
    raise ValueError("bad scope")


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


def _bad(msg: str, status: int = 400) -> JsonResponse:
    return JsonResponse({"ok": False, "error": msg}, status=status)


def _scope_target(typ: str, _id: int) -> tuple[Optional[object], Optional[dict]]:
    """
    Returns:
      - target instance (ProductCollection/ProductSet) or None
      - snapshot dict (id/name/code/collection_id where applicable) or None
    """
    if typ == "collection":
        col = ProductCollection.objects.filter(id=_id).first()
        if not col:
            return None, None
        before = snap_instance(col, ["name", "code"])
        return col, before

    if typ == "set":
        st = ProductSet.objects.select_related("collection").filter(id=_id).first()
        if not st:
            return None, None
        before = snap_instance(st, ["name", "code", "collection_id"])
        # add human collection label to snapshot (nice to have)
        before["collection_code"] = getattr(st.collection, "code", None)
        before["collection_name"] = getattr(st.collection, "name", None)
        return st, before

    return None, None


# ------------------------
# main endpoint
# ------------------------
@require_POST
@role_required(AccountProfile.Role.MANAGER)
def edit_apply_batch(request: HttpRequest):
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
            return _bad("ops must be a list", 400)
    except Exception:
        return _bad("bad json", 400)

    results: list[dict[str, Any]] = []

    # all-or-nothing transaction
    try:
        with transaction.atomic():
            for op in ops:
                kind = (op.get("op") or "").strip().lower()
                typ = (op.get("type") or "").strip().lower()
                _id = _as_int(op.get("id"))

                if kind not in {"rename", "adjust", "delete"}:
                    results.append({"ok": False, "error": "bad op"})
                    continue
                if typ not in ALLOWED_TYPES or not _id:
                    results.append({"ok": False, "error": "bad type/id"})
                    continue

                # fetch target scope object (for audit + some validations)
                target, target_before = _scope_target(typ, _id)
                if target is None:
                    results.append({"ok": False, "error": f"{typ} not found"})
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
                        col: ProductCollection = target  # type: ignore[assignment]

                        # no-op rename
                        if new_name.lower() == (col.name or "").lower():
                            results.append({"ok": True, "note": "no change"})
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

                        before = target_before or snap_instance(col, ["name", "code"])
                        col.name = new_name
                        col.full_clean()
                        col.save(update_fields=["name"])

                        after = snap_instance(col, ["name", "code"])

                        log_update(
                            actor=request.user,
                            request=request,
                            target=col,
                            title="Rename collection",
                            message=f"Collection renamed to: {col.name}",
                            before=before,
                            after=after,
                            meta={
                                "source": "catalog.edit_apply_batch",
                                "op": op,
                            },
                        )

                        results.append({"ok": True})

                    else:  # set
                        st: ProductSet = target  # type: ignore[assignment]

                        if new_name.lower() == (st.name or "").lower():
                            results.append({"ok": True, "note": "no change"})
                            continue

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

                        before = target_before or snap_instance(st, ["name", "code", "collection_id"])
                        st.name = new_name
                        st.full_clean()
                        st.save(update_fields=["name"])

                        after = snap_instance(st, ["name", "code", "collection_id"])
                        after["collection_code"] = getattr(st.collection, "code", None)
                        after["collection_name"] = getattr(st.collection, "name", None)

                        log_update(
                            actor=request.user,
                            request=request,
                            target=st,
                            title="Rename set",
                            message=f"Set renamed to: {st.name}",
                            before=before,
                            after=after,
                            meta={
                                "source": "catalog.edit_apply_batch",
                                "op": op,
                            },
                        )

                        results.append({"ok": True})

                # =========================
                #          ADJUST
                # =========================
                elif kind == "adjust":
                    field = (op.get("field") or "").strip().lower()
                    mode = (op.get("mode") or "").strip().lower()
                    sign = (op.get("sign") or "").strip()

                    delta = _as_decimal(op.get("delta"))
                    if field not in ALLOWED_FIELDS or mode not in ALLOWED_MODES or sign not in ALLOWED_SIGNS:
                        results.append({"ok": False, "error": "bad params"})
                        continue
                    if delta is None or delta <= 0:
                        results.append({"ok": False, "error": "delta must be > 0"})
                        continue

                    qs = _scope_qs(typ, _id)

                    # IMPORTANT: count BEFORE update
                    affected = qs.count()

                    if affected <= 0:
                        # still log (optional) but usually just return ok
                        results.append({"ok": True, "affected": 0, "note": "no products"})
                        continue

                    if mode == "percent":
                        factor = Decimal("1") + (delta / Decimal("100")) * (
                            Decimal("1") if sign == "+" else Decimal("-1")
                        )
                        qs.update(**{
                            field: Greatest(F(field) * factor, Value(Decimal("0")))
                        })
                    else:
                        expr = F(field) + delta if sign == "+" else F(field) - delta
                        qs.update(**{
                            field: Greatest(expr, Value(Decimal("0")))
                        })

                    # audit (target is the scope object, not the products)
                    before = target_before or snap_instance(target, ["name", "code"])
                    after = snap_instance(target, ["name", "code"])
                    log_update(
                        actor=request.user,
                        request=request,
                        target=target,
                        title="Adjust product pricing",
                        message=f"Adjusted {field} ({mode} {sign}{delta}) for {affected} products",
                        before=before,
                        after=after,
                        meta={
                            "source": "catalog.edit_apply_batch",
                            "op": op,
                            "scope_type": typ,
                            "scope_id": _id,
                            "field": field,
                            "mode": mode,
                            "sign": sign,
                            "delta": str(delta),
                            "affected": affected,
                        },
                    )

                    results.append({"ok": True, "affected": affected})

                # =========================
                #          DELETE
                # =========================
                elif kind == "delete":
                    if typ == "collection":
                        col: ProductCollection = target  # type: ignore[assignment]

                        # conservative: block if any products exist
                        if Product.objects.filter(set__collection=col).exists():
                            results.append({"ok": False, "error": "collection has products; deletion blocked"})
                            continue

                        before = snap_instance(col, ["name", "code"])

                        # delete sets then collection
                        ProductSet.objects.filter(collection=col).delete()
                        col.delete()

                        log_delete(
                            actor=request.user,
                            request=request,
                            target=col,  # identity still OK even if deleted (pk used)
                            title="Delete collection",
                            message=f"Collection deleted: {before.get('name')}",
                            before=before,
                            after=None,
                            meta={
                                "source": "catalog.edit_apply_batch",
                                "op": op,
                                "soft_delete": False,
                            },
                        )

                        results.append({"ok": True})

                    else:
                        st: ProductSet = target  # type: ignore[assignment]

                        before_set = snap_instance(st, ["name", "code", "collection_id"])
                        before_set["collection_code"] = getattr(st.collection, "code", None)
                        before_set["collection_name"] = getattr(st.collection, "name", None)

                        qs_products = Product.objects.filter(set=st)
                        prod_count = qs_products.count()

                        try:
                            with transaction.atomic():
                                # free globally-unique children
                                ProductBarcode.objects.filter(product__in=qs_products).delete()
                                ProductUnitId.objects.filter(product__in=qs_products).delete()

                                # hard delete products (may raise ProtectedError)
                                qs_products.delete()

                                # delete set
                                st.delete()

                            log_delete(
                                actor=request.user,
                                request=request,
                                target=st,
                                title="Delete set",
                                message=f"Set deleted: {before_set.get('name')} (products removed: {prod_count})",
                                before=before_set,
                                after=None,
                                meta={
                                    "source": "catalog.edit_apply_batch",
                                    "op": op,
                                    "products_deleted": prod_count,
                                    "fallback": None,
                                },
                            )

                            results.append({"ok": True})
                            continue

                        except ProtectedError:
                            # fallback: move products to trash + archive
                            with transaction.atomic():
                                trash = _get_or_create_trash_set(st.collection)

                                # barcodes/unit_ids already removed above – frees uniqueness
                                Product.objects.filter(set=st).update(set=trash, is_active=False)

                                # delete set after moving
                                st.delete()

                            log_delete(
                                actor=request.user,
                                request=request,
                                target=st,
                                title="Delete set (fallback to trash)",
                                message=f"Set deleted: {before_set.get('name')} (products moved to trash)",
                                before=before_set,
                                after=None,
                                meta={
                                    "source": "catalog.edit_apply_batch",
                                    "op": op,
                                    "fallback": "moved_products_to_trash_and_archived",
                                },
                            )

                            results.append({"ok": True, "note": "moved referenced products to trash"})
                            continue

                        except IntegrityError as e:
                            results.append({"ok": False, "error": f"delete failed: {e.__class__.__name__}"})
                            continue

    except Exception:
        # any crash: transaction rolls back
        return JsonResponse({"ok": False, "error": "apply failed"}, status=500)

    return JsonResponse({"ok": True, "results": results})
