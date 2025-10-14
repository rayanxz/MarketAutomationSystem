# catalog/edit_api.py
from __future__ import annotations
from decimal import Decimal
from django.db import transaction
from django.db.models import F
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from accounts.models import AccountProfile
from catalog.models import ProductCollection, ProductSet, Product
from catalog.views import role_required

def _scope_qs(scope_type: str, scope_id: int):
    if scope_type == "collection":
        return Product.objects.filter(set__collection_id=scope_id)
    elif scope_type == "set":
        return Product.objects.filter(set_id=scope_id)
    raise ValueError("bad scope")

@require_POST
@role_required(AccountProfile.Role.MANAGER)
def edit_apply_batch(request):
    """
    Accepts a JSON body:
    {
      "ops": [
        {"op":"rename", "type":"collection"|"set", "id":123, "name":"New name"},
        {"op":"adjust", "type":"collection"|"set", "id":123, "field":"price"|"cost", "mode":"percent"|"absolute", "delta": 10.0, "sign":"+"|"-"},
        {"op":"delete", "type":"collection"|"set", "id":123}
      ]
    }
    Executes in a single transaction; returns {"ok":true}.
    """
    import json
    try:
      payload = json.loads(request.body.decode("utf-8") or "{}")
      ops = payload.get("ops") or []
    except Exception:
      return JsonResponse({"ok": False, "error": "bad json"}, status=400)

    try:
      with transaction.atomic():
        for op in ops:
          kind = op.get("op")
          typ  = op.get("type")
          _id  = int(op.get("id"))

          if kind == "rename":
            name = (op.get("name") or "").strip()
            if not name:
              continue
            if typ == "collection":
              c = ProductCollection.objects.filter(id=_id).first()
              if c: c.name = name; c.save(update_fields=["name"])
            elif typ == "set":
              s = ProductSet.objects.filter(id=_id).first()
              if s: s.name = name; s.save(update_fields=["name"])

          elif kind == "adjust":
            field = op.get("field")  # "price" | "cost"
            mode  = op.get("mode")   # "percent" | "absolute"
            sign  = op.get("sign")   # "+" | "-"
            delta = Decimal(str(op.get("delta") or 0))
            if field not in ("price","cost") or delta <= 0:
              continue
            qs = _scope_qs(typ, _id)
            if mode == "percent":
              # price = price * (1 +/- p/100)
              factor = Decimal("1.0") + (delta/Decimal("100.0")) * (Decimal("1") if sign=="+" else Decimal("-1"))
              qs.update(**{field: F(field) * factor})
            else:
              # price = price +/- delta
              if sign == "+":
                qs.update(**{field: F(field) + delta})
              else:
                qs.update(**{field: F(field) - delta})

          elif kind == "delete":
            if typ == "collection":
              ProductCollection.objects.filter(id=_id).delete()
            elif typ == "set":
              ProductSet.objects.filter(id=_id).delete()

    except Exception as e:
      return JsonResponse({"ok": False, "error": "apply failed"}, status=500)

    return JsonResponse({"ok": True})
