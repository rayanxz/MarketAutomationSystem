# catalog/import_engine.py
from __future__ import annotations
import os, json, uuid
from typing import Any, Dict, List, Optional
from decimal import Decimal, InvalidOperation

import pandas as pd  # xlsx/ods only
from django.conf import settings
from django.db import transaction
from django.db.models.functions import Lower
from django.core.exceptions import ValidationError

from catalog.models import (
    ProductCollection, ProductSet, Product,
    ProductBarcode, ProductUnitId, UnitType
)

from catalog.import_rules import apply_rules
from catalog.services.deletion_policy import sync_identifiers_for_product

from catalog.io_records import CatalogDataJob
from audit_log.services import log_create


# ========= infra =========
class StageError(Exception):
    ...


TMP_DIR = os.path.join(settings.MEDIA_ROOT, "tmp", "imports")


def _ensure_dirs() -> None:
    os.makedirs(TMP_DIR, exist_ok=True)


def _p(sid: str, suf: str) -> str:
    return os.path.join(TMP_DIR, f"{sid}{suf}")


# ========= upload =========
def save_temp_upload(
    dj_file,
    *,
    file_type: str,
    collection: ProductCollection,
    has_header: bool = True,
) -> str:
    """
    Persist the uploaded file + minimal metadata.
    has_header = True -> first row is column headers; False -> pure data.
    """
    _ensure_dirs()
    sid = uuid.uuid4().hex
    binp = _p(sid, ".bin")
    with open(binp, "wb") as out:
        for ch in dj_file.chunks():
            out.write(ch)
    meta = {
        "file_type": (file_type or "").lower(),
        "collection_id": collection.id,
        "has_header": bool(has_header),
    }
    with open(_p(sid, ".meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)
    return sid


# ========= dataframe IO =========
def _read_df(sid: str) -> pd.DataFrame:
    """
    Read the uploaded file into a pandas DataFrame.
    - ODS/XLSX: use odf/openpyxl engines.
    Respects has_header flag saved in meta.
    """
    meta = json.load(open(_p(sid, ".meta.json"), "r", encoding="utf-8"))
    has_header = bool(meta.get("has_header", True))
    header_arg = 0 if has_header else None

    t = (meta.get("file_type") or "").lower()
    binp = _p(sid, ".bin")

    try:
        if t == "xlsx":
            return pd.read_excel(binp, engine="openpyxl", header=header_arg, dtype=str)
        if t == "ods":
            return pd.read_excel(binp, engine="odf", header=header_arg, dtype=str)
    except Exception as e:
        raise StageError(f"فشل القراءة: {e}")

    raise StageError("نوع ملف غير مدعوم.")


# ========= DB maps (names, barcodes, unit-ids) =========
def _db_maps():
    """
    Returns:
      - db_name_to_pid (lowercased name -> product_id)  [ACTIVE products only]
      - db_bar_to_pid  (barcode -> product_id)          [all]
      - db_uid_to_pid  (unit_id -> product_id)          [all]
    """
    name_map = {n.lower(): pid
                for (n, pid) in Product.objects
                    .filter(is_active=True)
                    .values_list("name", "id")}
    bar_map  = {b: pid for (b, pid) in ProductBarcode.objects.filter(is_active=True).values_list("barcode", "product_id")}
    uid_map  = {v: pid for (v, pid) in ProductUnitId.objects.filter(is_active=True).values_list("value", "product_id")}
    return name_map, bar_map, uid_map



# ========= analyze (columns + sample) =========
def analyze_file(sid: str) -> Dict[str, Any]:
    df = _read_df(sid).fillna("")
    headers = [str(c) for c in df.columns]
    sample = df.head(5).values.tolist()

    hints: List[str] = []
    for c in df.columns:
        s = df[c].astype(str).str.replace(",", ".", regex=False)
        try:
            pd.to_numeric(s, errors="raise")
            hints.append("رقمي غالباً")
        except Exception:
            hints.append("نصي غالباً")

    return {"headers": headers, "hints": hints, "sample": sample}


# ========= helpers =========
def _to_dec(x, nd: int = 4) -> Optional[Decimal]:
    if x is None:
        return None
    s = str(x).strip().replace(",", ".")
    if not s:
        return None
    try:
        d = Decimal(s)
        q = Decimal("0." + ("0" * (nd - 1)) + "1") if nd > 0 else Decimal("1")
        return d.quantize(q)
    except (InvalidOperation, ValueError):
        return None


def _split_tokens(x) -> List[str]:
    if x is None:
        return []
    text = str(x).replace("\n", " ")
    raw: List[str] = []
    for chunk in text.split(","):
        raw.extend(chunk.split(" "))
    seen: set[str] = set()
    out: List[str] = []
    for t in (t.strip() for t in raw):
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


# required mapping fields (stage-2)
REQUIRED = {"name", "set", "unit_primary", "unit_secondary", "conversion_factor"}

ALL_FIELDS = {
    "name", "set", "unit_primary", "unit_secondary", "conversion_factor",
    "cost", "price", "stock_qty", "product_number",
    "barcodes_u1", "barcodes_u2", "unit_ids_u1", "unit_ids_u2", "notes", "dup_action"
}


def _build_unit_maps():
    # UnitType.choices is [(value, label), ...]
    vals = {str(v).strip().lower(): v for v, _ in UnitType.choices}
    labs = {str(l).strip().lower(): v for v, l in UnitType.choices}
    vals.update(labs)
    return vals, {v for v, _ in UnitType.choices}


_UNIT_MAP, _UNIT_VALID = _build_unit_maps()


def _norm_unit(s: Any) -> str:
    """Return a valid UnitType value from a value/label; '' if empty."""
    if s is None:
        return ""
    t = str(s).strip()
    if not t:
        return ""
    return _UNIT_MAP.get(t.lower(), t)  # may still be raw -> validated later


# ---- JSON helpers (prevent Decimal crash / trim trailing zeros) ----
def _json_sanitize(obj):
    """Recursively convert Decimals to strings for json.dump (without phantom .0)."""
    if isinstance(obj, list):
        return [_json_sanitize(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _json_sanitize(v) for k, v in obj.items()}
    if isinstance(obj, Decimal):
        s = format(obj, "f")               # e.g. "5000.0000"
        if "." in s:
            s = s.rstrip("0").rstrip(".")  # -> "5000"
        return s
    return obj


# ========= stage (map + validate; NO DB writes) =========
def stage_file_with_mapping(sid: str, mapping: Dict[str, int], options: Dict[str, Any]) -> Dict[str, Any]:
    # Require minimal fields
    for r in REQUIRED:
        if r not in mapping:
            raise StageError(f"حقل إجباري مفقود: {r}")

    df = _read_df(sid).fillna("")

    def get_cell(irow: int, field: str) -> Any:
        idx = mapping.get(field)
        if idx is None:
            return ""
        try:
            return df.iat[irow, idx]
        except Exception:
            return ""

    # load collection (not strictly used in stage but kept for parity)
    meta = json.load(open(_p(sid, ".meta.json"), "r", encoding="utf-8"))
    ProductCollection.objects.get(id=meta["collection_id"])  # ensures existence

    staged: List[Dict[str, Any]] = []
    names_seen: set[str] = set()

    # DB prefetch for duplicate name flags (fast path; final policy handled by import_rules)
    file_names = [str(get_cell(i, "name")).strip().lower() for i in range(len(df))]
    existing_names = set(
        Product.objects.annotate(n=Lower("name"))
        .filter(n__in=file_names)
        .values_list("n", flat=True)
    )

    # pre-scan barcodes / unit ids for quick flags (import_rules will do full policy)
    all_barcodes, all_ids = set(), set()
    for i in range(len(df)):
        all_barcodes.update(_split_tokens(get_cell(i, "barcodes_u1")))
        all_barcodes.update(_split_tokens(get_cell(i, "barcodes_u2")))
        all_ids.update(_split_tokens(get_cell(i, "unit_ids_u1")))
        all_ids.update(_split_tokens(get_cell(i, "unit_ids_u2")))
    taken_bc = set(ProductBarcode.objects.filter(barcode__in=all_barcodes, is_active=True).values_list("barcode", flat=True)) if all_barcodes else set()
    taken_ids = set(ProductUnitId.objects.filter(value__in=all_ids, is_active=True).values_list("value", flat=True)) if all_ids else set()

    for i in range(len(df)):
        d = {k: "" for k in ALL_FIELDS}
        d["name"] = str(get_cell(i, "name")).strip()
        d["set"] = str(get_cell(i, "set")).strip()
        d["unit_primary"] = (str(get_cell(i, "unit_primary")).strip() or UnitType.PIECE)
        d["unit_secondary"] = str(get_cell(i, "unit_secondary")).strip()
        d["conversion_factor"] = _to_dec(get_cell(i, "conversion_factor"), nd=4)
        d["cost"] = _to_dec(get_cell(i, "cost"), nd=4) or Decimal("0.0000")
        d["price"] = _to_dec(get_cell(i, "price"), nd=4) or Decimal("0.0000")
        d["stock_qty"] = _to_dec(get_cell(i, "stock_qty"), nd=3)

        pn = str(get_cell(i, "product_number")).strip()
        d["product_number"] = int(pn) if pn.isdigit() else None

        d["barcodes_u1"] = _split_tokens(get_cell(i, "barcodes_u1"))
        d["barcodes_u2"] = _split_tokens(get_cell(i, "barcodes_u2"))
        d["unit_ids_u1"] = _split_tokens(get_cell(i, "unit_ids_u1"))
        d["unit_ids_u2"] = _split_tokens(get_cell(i, "unit_ids_u2"))
        d["notes"] = str(get_cell(i, "notes")).strip()
        d["dup_action"] = "update"  # default if name collides with DB

        errs: Dict[str, str] = {}
        if not d["name"]:
            errs["name"] = "اسم المنتج مطلوب."

        if d["unit_secondary"]:
            if not d["conversion_factor"]:
                errs["conversion_factor"] = "مطلوب عند تحديد الوحدة الثانية."
            if d["unit_primary"] == d["unit_secondary"]:
                errs["unit_secondary"] = "لا يجوز أن تكون الوحدة الثانية مطابقة للأولى."

        key = d["name"].lower() if d["name"] else ""
        if key:
            if key in names_seen:
                errs["name"] = "الاسم مكرر داخل الملف."
            names_seen.add(key)
            if key in existing_names:
                # informational early flag; exact policy decided by mode rules
                errs.setdefault("name_db", "يوجد منتج بنفس الاسم في قاعدة البيانات.")

        # quick DB collision hints (mode rules refine later)
        for bc in d["barcodes_u1"] + d["barcodes_u2"]:
            if bc in taken_bc:
                errs.setdefault("barcodes", "بعض الباركودات مستخدمة مسبقاً.")
        for val in d["unit_ids_u1"] + d["unit_ids_u2"]:
            if val in taken_ids:
                errs.setdefault("unit_ids", "بعض معرّفات الوحدات مستخدمة مسبقاً.")

        staged.append({"rid": i + 1, "data": d, "errors": errs})

    # === heavy pre-scan by mode (name/barcode/unit-id policies) ===
    mode = (options.get("import_mode") or "add_update").lower()
    db_name_to_pid, db_bar_to_pid, db_uid_to_pid = _db_maps()
    apply_rules(staged, mode, db_name_to_pid, db_bar_to_pid, db_uid_to_pid)

    payload = {"mapping": mapping, "options": options, "rows": staged}
    with open(_p(sid, ".stage.json"), "w", encoding="utf-8") as f:
        json.dump(_json_sanitize(payload), f, ensure_ascii=False)

    errors_total = sum(1 for s in staged if s["errors"])
    return {"rows_total": len(staged), "errors_total": errors_total}


# ========= paging =========
def paged_rows(sid: str, *, page: int, size: int) -> Dict[str, Any]:
    st = json.load(open(_p(sid, ".stage.json"), "r", encoding="utf-8"))
    rows = st["rows"]
    total = len(rows)
    pages = max(1, (total + size - 1) // size)
    page = max(1, min(page, pages))
    off = (page - 1) * size
    return {
        "rows": rows[off:off + size],
        "page": page,
        "pages": pages,
        "errors_total": sum(1 for r in rows if r["errors"]),
        "total": total,
    }


# ========= inline update in staging =========
def update_row_in_stage(sid: str, rid: int, field: str, value: Any):
    stp = _p(sid, ".stage.json")
    st = json.load(open(stp, "r", encoding="utf-8"))
    rows = st["rows"]
    mode = (st.get("options", {}).get("import_mode") or "add_update").lower()

    target = None
    for r in rows:
        if r["rid"] == rid:
            target = r
            break
    if not target:
        raise StageError("الصف غير موجود.")

    d = target["data"]
    d[field] = value  # raw update

    # --- normalize edited field types ---
    if field in {"barcodes_u1", "barcodes_u2", "unit_ids_u1", "unit_ids_u2"}:
        d[field] = _split_tokens(value)  # ALWAYS keep these as lists

    # revalidate common edits
    errs: Dict[str, str] = {}
    if not (d.get("name") or "").strip():
        errs["name"] = "اسم المنتج مطلوب."

    if field in {"price", "cost"} and _to_dec(value, nd=4) is None:
        errs[field] = "القيمة ليست رقماً صالحاً."

    if field == "conversion_factor" and _to_dec(value, nd=4) is None:
        errs["conversion_factor"] = "عامل التحويل يجب أن يكون رقماً."

    # unit + cf checks
    if (d.get("unit_secondary") or "").strip():
        if not _to_dec(d.get("conversion_factor"), nd=4):
            errs["conversion_factor"] = "مطلوب عند تحديد الوحدة الثانية."
        if (d.get("unit_primary") or "") == (d.get("unit_secondary") or ""):
            errs["unit_secondary"] = "لا يجوز أن تكون الوحدة الثانية مطابقة للأولى."

    target["errors"] = errs

    # === RE-RUN NAME/CODE/BARCODE RULES across the WHOLE STAGE
    db_name_to_pid, db_bar_to_pid, db_uid_to_pid = _db_maps()
    apply_rules(rows, mode, db_name_to_pid, db_bar_to_pid, db_uid_to_pid)

    with open(stp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False)
    return target, sum(1 for r in rows if r.get("errors"))


# ========= commit (DB writes) =========
def commit_stage(sid: str, *, actor=None, request=None) -> Dict[str, Any]:
    st = json.load(open(_p(sid, ".stage.json"), "r", encoding="utf-8"))
    if any(r["errors"] for r in st["rows"]):
        raise StageError("لا يمكن الإدخال قبل تصفير جميع الأخطاء.")

    meta = json.load(open(_p(sid, ".meta.json"), "r", encoding="utf-8"))
    collection = ProductCollection.objects.get(id=meta["collection_id"])

    created = updated = skipped = 0

    with transaction.atomic():
        for r in st["rows"]:
            d = r["data"]
            rid = r.get("rid", "?")

            name = (d.get("name") or "").strip()
            if not name:
                skipped += 1
                continue

            # Convert staged strings back to Decimals / typed values
            conv_factor = _to_dec(d.get("conversion_factor"), nd=4)
            cost  = _to_dec(d.get("cost"), nd=4)  or Decimal("0.0000")
            price = _to_dec(d.get("price"), nd=4) or Decimal("0.0000")
            stock_qty = _to_dec(d.get("stock_qty"), nd=3)

            # Map Arabic/English labels to UnitType values
            unit_primary   = _norm_unit(d.get("unit_primary")) or UnitType.PIECE
            unit_secondary = _norm_unit(d.get("unit_secondary")) or ""
            notes = d.get("notes", "")

            # Validate unit values against actual choices
            if unit_primary and unit_primary not in _UNIT_VALID:
                raise StageError(f"سطر {rid}: وحدة أولى غير معروفة: {d.get('unit_primary')!r}")
            if unit_secondary and unit_secondary not in _UNIT_VALID:
                raise StageError(f"سطر {rid}: وحدة ثانية غير معروفة: {d.get('unit_secondary')!r}")

            # If both set, they must be different when a CF is present
            if unit_secondary and unit_primary == unit_secondary:
                raise StageError(f"سطر {rid}: لا يجوز أن تكون الوحدة الثانية مطابقة للأولى.")
            if unit_secondary and not conv_factor:
                raise StageError(f"سطر {rid}: عامل التحويل مطلوب عند تحديد الوحدة الثانية.")

            # set (create if missing)
            s_name = (d.get("set") or "").strip() or "غير مصنّف"
            st_obj = (
                ProductSet.objects.filter(collection=collection)
                .annotate(n=Lower("name"))
                .filter(n=s_name.lower())
                .first()
            )
            if not st_obj:
                st_obj = ProductSet.objects.create(collection=collection, name=s_name)

            existing = Product.objects.annotate(n=Lower("name")).filter(n=name.lower()).first()

            try:
                if existing:
                    if d.get("dup_action") == "skip":
                        skipped += 1
                        continue

                    p = existing

                    if not p.is_active:
                        raise StageError(f"سطر {rid}: المنتج مؤرشف ولا يمكن تحديثه عبر الاستيراد.")

                    p.set = st_obj
                    p.unit_primary = unit_primary
                    p.unit_secondary = unit_secondary
                    p.conversion_factor = conv_factor
                    p.cost = cost
                    p.price = price
                    if stock_qty is not None:
                        p.stock_qty = stock_qty
                    p.notes = notes
                    p.full_clean()
                    p.save()
                    for bc in d.get("barcodes_u1", []):
                        ProductBarcode.objects.get_or_create(
                            product=p, unit_index=ProductBarcode.UnitIndex.PRIMARY, barcode=bc,
                            defaults={"is_active": p.is_active},
                        )
                    if not p.is_single_unit:
                        for bc in d.get("barcodes_u2", []):
                            ProductBarcode.objects.get_or_create(
                                product=p, unit_index=ProductBarcode.UnitIndex.SECONDARY, barcode=bc,
                                defaults={"is_active": p.is_active},
                            )
                    for val in d.get("unit_ids_u1", []):
                        ProductUnitId.objects.get_or_create(
                            product=p, unit_index=ProductUnitId.UnitIndex.PRIMARY, value=val,
                            defaults={"is_active": p.is_active},
                        )
                    if not p.is_single_unit:
                        for val in d.get("unit_ids_u2", []):
                            ProductUnitId.objects.get_or_create(
                                product=p, unit_index=ProductUnitId.UnitIndex.SECONDARY, value=val,
                                defaults={"is_active": p.is_active},
                            )

                    updated += 1
                else:
                    p = Product(
                        name=name,
                        set=st_obj,
                        unit_primary=unit_primary,
                        unit_secondary=unit_secondary,
                        conversion_factor=conv_factor,
                        cost=cost,
                        price=price,
                        notes=notes,
                    )
                    if d.get("product_number"):
                        p.product_number = d["product_number"]
                    p.full_clean()
                    p.save()

                    for bc in d.get("barcodes_u1", []):
                        ProductBarcode.objects.create(
                            product=p, unit_index=ProductBarcode.UnitIndex.PRIMARY, barcode=bc, is_active=p.is_active
                        )
                    if not p.is_single_unit:
                        for bc in d.get("barcodes_u2", []):
                            ProductBarcode.objects.create(
                                product=p, unit_index=ProductBarcode.UnitIndex.SECONDARY, barcode=bc, is_active=p.is_active
                            )
                    for val in d.get("unit_ids_u1", []):
                        ProductUnitId.objects.create(
                            product=p, unit_index=ProductUnitId.UnitIndex.PRIMARY, value=val, is_active=p.is_active
                        )
                    if not p.is_single_unit:
                        for val in d.get("unit_ids_u2", []):
                            ProductUnitId.objects.create(
                                product=p, unit_index=ProductUnitId.UnitIndex.SECONDARY, value=val, is_active=p.is_active
                            )

                    if stock_qty is not None:
                        p.stock_qty = stock_qty
                        p.save(update_fields=["stock_qty"])

                    created += 1

            except ValidationError as ve:
                msgs = []
                for field, errs in ve.message_dict.items():
                    for e in errs:
                        msgs.append(f"{field}: {e}")
                raise StageError(f"سطر {rid}: " + (" ؛ ".join(msgs) or "بيانات غير صالحة."))

    # ===== AFTER SUCCESSFUL DB COMMIT: store import record + audit =====
    job = CatalogDataJob.objects.create(
        kind=CatalogDataJob.Kind.IMPORT,
        status=CatalogDataJob.Status.SUCCESS,
        actor=actor if getattr(actor, "is_authenticated", False) else None,
    )
    job.summary_json = {"created": created, "updated": updated, "skipped": skipped}
    job.meta_json = {
        "collection_id": collection.id,
        "staging_id": sid,
        "import_mode": (st.get("options", {}).get("import_mode") or ""),
    }
    # Store EXACT approved rows (can be big; later we can cap / compress)
    job.rows_json = st.get("rows", [])
    job.save(update_fields=["summary_text", "meta_text", "rows_text"])

    # Audit entry that points to THIS job row
    log_create(
        actor=actor,
        target=job,  # IMPORTANT: lets audit point to CatalogDataJob
        title="Catalog Import",
        message=f"Imported catalog rows. created={created}, updated={updated}, skipped={skipped}",
        after={
            "job_id": job.id,
            "kind": job.kind,
            "status": job.status,
            "collection_id": collection.id,
            "created": created,
            "updated": updated,
            "skipped": skipped,
            "rows_total": len(st.get("rows", [])),
        },
        request=request,
    )

    return {"created": created, "updated": updated, "skipped": skipped, "job_id": job.id}

