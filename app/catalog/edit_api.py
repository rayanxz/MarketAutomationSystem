# catalog/edit_api.py
from __future__ import annotations

import json
from typing import Any, Optional

from django.db import transaction
from django.db.models.functions import Lower
from django.http import JsonResponse, HttpRequest
from django.views.decorators.http import require_POST

from accounts.models import AccountProfile
from catalog.models import (
    ProductCollection,
    ProductSet,
    Product,
)
from catalog.views import role_required
from catalog.services.deletion_policy import (
    ProductDisableBlockedError,
    disable_product,
)

from audit_log.services import log_update, log_delete, snap_instance

ALLOWED_TYPES = {"collection", "set"}


# ------------------------
# helpers
# ------------------------
def _as_int(x) -> int | None:
    try:
        return int(x)
    except Exception:
        return None



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


def _archive_products_for_set(st: ProductSet) -> int:
    count = 0
    for p in Product.objects.filter(set=st, is_active=True).order_by("id"):
        disable_product(p)
        count += 1
    return count


def _archive_products_for_collection(col: ProductCollection) -> int:
    count = 0
    for p in Product.objects.filter(set__collection=col, is_active=True).order_by("id"):
        disable_product(p)
        count += 1
    return count


def _can_archive_entity(obj, field_name: str = "is_active") -> bool:
    return hasattr(obj, field_name)


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

                if kind not in {"rename", "delete"}:
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
                #          DELETE
                # =========================
                elif kind == "delete":
                    if typ == "collection":
                        col: ProductCollection = target  # type: ignore[assignment]

                        before = snap_instance(col, ["name", "code"])
                        try:
                            archived = _archive_products_for_collection(col)
                        except ProductDisableBlockedError:
                            results.append({
                                "ok": False,
                                "error": "Cannot disable one or more products: stock must be zero in store/wh1/wh2.",
                            })
                            continue

                        if _can_archive_entity(col):
                            col.is_active = False  # type: ignore[attr-defined]
                            col.save(update_fields=["is_active"])
                            log_delete(
                                actor=request.user,
                                request=request,
                                target=col,
                                title="Archive collection",
                                message=f"Collection archived: {before.get('name')}",
                                before=before,
                                after=snap_instance(col, ["name", "code"]),
                                meta={
                                    "source": "catalog.edit_apply_batch",
                                    "op": op,
                                    "soft_delete": True,
                                    "products_archived": archived,
                                },
                            )
                            results.append({"ok": True, "products_archived": archived})
                        else:
                            results.append({
                                "ok": False,
                                "error": "Deletion disabled; archive not implemented for collections yet.",
                                "products_archived": archived,
                            })

                    else:
                        st: ProductSet = target  # type: ignore[assignment]

                        before_set = snap_instance(st, ["name", "code", "collection_id"])
                        before_set["collection_code"] = getattr(st.collection, "code", None)
                        before_set["collection_name"] = getattr(st.collection, "name", None)

                        try:
                            archived = _archive_products_for_set(st)
                        except ProductDisableBlockedError:
                            results.append({
                                "ok": False,
                                "error": "Cannot disable one or more products: stock must be zero in store/wh1/wh2.",
                            })
                            continue

                        if _can_archive_entity(st):
                            st.is_active = False  # type: ignore[attr-defined]
                            st.save(update_fields=["is_active"])
                            log_delete(
                                actor=request.user,
                                request=request,
                                target=st,
                                title="Archive set",
                                message=f"Set archived: {before_set.get('name')}",
                                before=before_set,
                                after=snap_instance(st, ["name", "code", "collection_id"]),
                                meta={
                                    "source": "catalog.edit_apply_batch",
                                    "op": op,
                                    "soft_delete": True,
                                    "products_archived": archived,
                                },
                            )
                            results.append({"ok": True, "products_archived": archived})
                        else:
                            results.append({
                                "ok": False,
                                "error": "Deletion disabled; archive not implemented for sets yet.",
                                "products_archived": archived,
                            })

    except Exception:
        # any crash: transaction rolls back
        return JsonResponse({"ok": False, "error": "apply failed"}, status=500)

    return JsonResponse({"ok": True, "results": results})
