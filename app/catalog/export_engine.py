from __future__ import annotations

import json
import os
import uuid
from typing import Any, Dict, Iterable, List

import pandas as pd
from django.conf import settings

from catalog.models import Product, ProductBarcode, ProductUnitId

TMP_DIR = os.path.join(settings.MEDIA_ROOT, "tmp", "exports")
os.makedirs(TMP_DIR, exist_ok=True)

# Map your internal unit codes to Arabic labels
UNIT_LABELS = {
    "pc": "قطعة",
    "PCS": "قطعة",
    "pkg": "علبة",
    "PKG": "علبة",
    "box": "صندوق",
    "BOX": "صندوق",
    "kg": "كغ",
    "KG": "كغ",
    "g": "غ",
    "G": "غ",
    "l": "ليتر",
    "L": "ليتر",
}


def _u(x: str | None) -> str:
    if not x:
        return ""
    return UNIT_LABELS.get(str(x).strip(), str(x).strip())


DEFAULT_COLUMNS: List[str] = [
    "id",
    "name",
    "set",
    "unit_primary",
    "unit_secondary",
    "conversion_factor",
    "cost",
    "price",
    "stock_qty",
    "barcodes_u1",
    "barcodes_u2",
    "unit_ids_u1",
    "unit_ids_u2",
    "notes",
]


HEADER_LABELS: Dict[str, str] = {
    "id": "رمز المنتج",
    "name": "الاسم",
    "set": "المجموعة الأب",
    "unit_primary": "الوحدة الأولى",
    "unit_secondary": "الوحدة الثانية",
    "conversion_factor": "عامل التحويل",
    "cost": "الكلفة",
    "price": "السعر",
    "stock_qty": "الكمية بالمخزن",
    "barcodes_u1": "باركودات U1",
    "barcodes_u2": "باركودات U2",
    "unit_ids_u1": "معرّفات U1",
    "unit_ids_u2": "معرّفات U2",
    "notes": "ملاحظات",
}


def _join(values: Iterable[str]) -> str:
    return " ".join(v for v in values if v)


def _collect_rows(scope: str, ids: List[int] | None) -> List[Dict[str, Any]]:
    """
    scope: 'all' | 'collections' | 'sets'
    Only exports is_active=True
    """
    qs = Product.objects.select_related("set", "set__collection").filter(is_active=True)

    if scope == "collections":
        ids = ids or []
        if ids:
            qs = qs.filter(set__collection_id__in=ids)
    elif scope == "sets":
        ids = ids or []
        if ids:
            qs = qs.filter(set_id__in=ids)

    prod_ids = list(qs.values_list("id", flat=True))

    bc_u1 = {pid: [] for pid in prod_ids}
    bc_u2 = {pid: [] for pid in prod_ids}
    for pid, unit_index, bc in ProductBarcode.objects.filter(product_id__in=prod_ids).values_list(
        "product_id", "unit_index", "barcode"
    ):
        (bc_u1 if unit_index == 1 else bc_u2)[pid].append(bc)

    uid_u1 = {pid: [] for pid in prod_ids}
    uid_u2 = {pid: [] for pid in prod_ids}
    for pid, unit_index, val in ProductUnitId.objects.filter(product_id__in=prod_ids).values_list(
        "product_id", "unit_index", "value"
    ):
        (uid_u1 if unit_index == 1 else uid_u2)[pid].append(val)

    rows: List[Dict[str, Any]] = []
    for p in qs.order_by("set__collection__name", "set__name", "id"):
        rows.append(
            {
                "id": p.id,
                "name": p.name,
                "set": p.set.name,
                "unit_primary": _u(p.unit_primary),
                "unit_secondary": _u(p.unit_secondary),
                "conversion_factor": str(p.conversion_factor or ""),
                "cost": str(p.cost),
                "price": str(p.price),
                "stock_qty": str(p.stock_qty),
                "barcodes_u1": _join(bc_u1.get(p.id, [])),
                "barcodes_u2": _join(bc_u2.get(p.id, [])),
                "unit_ids_u1": _join(uid_u1.get(p.id, [])),
                "unit_ids_u2": _join(uid_u2.get(p.id, [])),
                "notes": p.notes or "",
            }
        )
    return rows


def build_dataframe(rows: List[Dict[str, Any]], columns: List[str] | None) -> pd.DataFrame:
    cols = columns or DEFAULT_COLUMNS
    if not rows:
        return pd.DataFrame(columns=cols)
    safe_cols = [c for c in cols if c in rows[0]]
    return pd.DataFrame(rows, columns=safe_cols)


def make_file(
    scope: str,
    ids: List[int] | None,
    *,
    columns: List[str] | None,
    include_header: bool,
    file_type: str,
) -> tuple[str, str]:
    rows = _collect_rows(scope, ids or [])
    df = build_dataframe(rows, columns)

    key = uuid.uuid4().hex
    ext = "xlsx" if file_type.lower() not in {"xlsx", "ods"} else file_type.lower()
    abs_path = os.path.join(TMP_DIR, f"{key}.{ext}")

    if include_header:
        df = df.rename(columns={c: HEADER_LABELS.get(c, c) for c in df.columns})

    if ext == "xlsx":
        with pd.ExcelWriter(abs_path, engine="openpyxl") as w:
            df.to_excel(w, index=False, header=include_header)
    else:
        with pd.ExcelWriter(abs_path, engine="odf") as w:
            df.to_excel(w, index=False, header=include_header)

    meta = {
        "scope": scope,
        "ids": ids or [],
        "columns": columns or DEFAULT_COLUMNS,
        "include_header": include_header,
        "file_type": ext,
        "rows": int(df.shape[0]),
    }
    with open(os.path.join(TMP_DIR, f"{key}.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)

    return key, abs_path
