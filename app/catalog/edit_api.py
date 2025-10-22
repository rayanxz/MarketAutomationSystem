# catalog/edit_api.py
from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import F, Value
from django.db.models.functions import Lower, Greatest
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from accounts.models import AccountProfile
from catalog.models import ProductCollection, ProductSet, Product
from catalog.views import role_required


ALLOWED_TYPES = {"collection", "set"}
ALLOWED_FIELDS = {"price", "cost"}
ALLOWED_MODES = {"percent", "absolute"}
ALLOWED_SIGNS = {"+", "-"}


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
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
        ops = payload.get("ops") or []
        if not isinstance(ops, list):
            return JsonResponse({"ok": False, "error": "ops must be a list"}, status=400)
    except Exception:
        return JsonResponse({"ok": False, "error": "bad json"}, status=400)

    results: list[dict] = []

    try:
        with transaction.atomic():
            for op in ops:
                # basic validation
                kind = (op.get("op") or "").strip().lower()
                typ = (op.get("type") or "").strip().lower()
                _id = _as_int(op.get("id"))

                if kind not in {"rename", "adjust", "delete"}:
                    results.append({"ok": False, "error": "bad op"})
                    continue
                if typ not in ALLOWED_TYPES or not _id:
                    results.append({"ok": False, "error": "bad type/id"})
                    continue

                # ---------- RENAME ----------
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
                        # CI uniqueness (defensive; DB constraint will enforce anyway)
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
                        col.full_clean(exclude=None)
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
                        st.full_clean(exclude=None)
                        st.save(update_fields=["name"])
                        results.append({"ok": True})

                # ---------- ADJUST ----------
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

                    if mode == "percent":
                        # factor = 1 +/- (delta/100)
                        factor = Decimal("1") + (delta / Decimal("100")) * (Decimal("1") if sign == "+" else Decimal("-1"))
                        # clamp at zero to avoid negative prices/costs
                        qs.update(**{
                            field: Greatest(F(field) * factor, Value(Decimal("0")))
                        })
                    else:
                        # absolute add/subtract; clamp at zero
                        if sign == "+":
                            expr = F(field) + delta
                        else:
                            expr = F(field) - delta
                        qs.update(**{
                            field: Greatest(expr, Value(Decimal("0")))
                        })

                    results.append({"ok": True, "affected": qs.count()})

                # ---------- DELETE ----------
                elif kind == "delete":
                    if typ == "collection":
                        col = ProductCollection.objects.filter(id=_id).first()
                        if not col:
                            results.append({"ok": False, "error": "collection not found"})
                            continue
                        # mirror views: block destructive delete if any products exist
                        if Product.objects.filter(set__collection=col).exists():
                            results.append({"ok": False, "error": "collection has products; deletion blocked"})
                            continue
                        ProductSet.objects.filter(collection=col).delete()
                        col.delete()
                        results.append({"ok": True})

                    else:  # set
                        st = ProductSet.objects.filter(id=_id).first()
                        if not st:
                            results.append({"ok": False, "error": "set not found"})
                            continue
                        if Product.objects.filter(set=st).exists():
                            results.append({"ok": False, "error": "set has products; deletion blocked"})
                            continue
                        st.delete()
                        results.append({"ok": True})

    except Exception:
        return JsonResponse({"ok": False, "error": "apply failed"}, status=500)

    return JsonResponse({"ok": True, "results": results})
