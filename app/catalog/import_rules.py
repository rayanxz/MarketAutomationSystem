# catalog/import_rules.py
from __future__ import annotations
from typing import Dict, List, Any, Tuple, Set

"""
Import validation rules for staged rows.

We validate three things:
- Name policy depending on mode
- Barcodes uniqueness (vs DB and within file)
- Unit IDs ("codes") uniqueness (vs DB and within file)

MODE semantics:
  "new"        -> only add new products (error if name exists in DB; also if duplicated in file)
  "add_update" -> add new + update existing (no DB-name error; still error if duplicated in file)
  "update"     -> only update existing (error if name NOT in DB; also if duplicated in file)

Barcode / Unit-ID rules:
  - All modes: duplicate within the uploaded file => error (each row involved gets flagged)
  - Mode NEW: any barcode/unit-id that exists in DB => error
  - Mode ADD_UPDATE:
       * If row is NEW name (not in DB) -> barred if any barcode/unit-id exists in DB
       * If row is EXISTING name -> a barcode/unit-id is allowed iff it already belongs to THIS product.
         If it belongs to another product => error.
  - Mode UPDATE:
       * Name must exist in DB
       * Each barcode/unit-id is allowed iff it already belongs to THIS product.
         If it belongs to another product => error.

IMPORTANT EXCEPTION:
  If the row is updating an existing product and the provided barcodes/unit-ids include values that
  already belong to that same product in DB, that’s OK and must NOT be an error.
"""

def _norm(s: str) -> str:
    return (s or "").strip().lower()

def build_db_maps(
    db_name_to_pid: Dict[str, int],
    db_bar_to_pid: Dict[str, int],
    db_uid_to_pid: Dict[str, int],
) -> Tuple[Dict[str, int], Dict[str, int], Dict[str, int]]:
    # Already normalized by caller; keep shape
    return db_name_to_pid, db_bar_to_pid, db_uid_to_pid

def _as_tokens(v) -> List[str]:
    """Return a list of tokens from list/str/None without exploding."""
    if v is None:
        return []
    if isinstance(v, list):
        out = []
        for x in v:
            t = str(x).strip()
            if t:
                out.append(t)
        return out
    # string: split on commas + whitespace
    s = str(v).replace("\n", " ")
    parts: List[str] = []
    for chunk in s.split(","):
        parts.extend(chunk.split(" "))
    return [t.strip() for t in parts if t.strip()]

def analyze_infile_dups(rows: List[Dict[str, Any]]) -> Tuple[Set[str], Set[str], Set[str]]:
    """
    Scan the staged rows and return sets of duplicate names, barcodes, and unit-ids
    *within the uploaded file only*.
    """
    name_counts: Dict[str, int] = {}
    bc_counts: Dict[str, int] = {}
    uid_counts: Dict[str, int] = {}

    for r in rows:
        d = r.get("data", {})
        nm = _norm(d.get("name", ""))
        if nm:
            name_counts[nm] = name_counts.get(nm, 0) + 1

        # IMPORTANT: use _as_tokens so strings won't crash when concatenated
        for bc in _as_tokens(d.get("barcodes_u1")) + _as_tokens(d.get("barcodes_u2")):
            bc_counts[bc] = bc_counts.get(bc, 0) + 1

        for uid in _as_tokens(d.get("unit_ids_u1")) + _as_tokens(d.get("unit_ids_u2")):
            uid_counts[uid] = uid_counts.get(uid, 0) + 1

    dup_names    = {k for k, c in name_counts.items() if c > 1}
    dup_barcodes = {k for k, c in bc_counts.items()   if c > 1}
    dup_unitids  = {k for k, c in uid_counts.items()  if c > 1}
    return dup_names, dup_barcodes, dup_unitids

def apply_rules(
    rows: List[Dict[str, Any]],
    mode: str,
    db_name_to_pid: Dict[str, int],
    db_bar_to_pid: Dict[str, int],
    db_uid_to_pid: Dict[str, int],
) -> None:
    """
    Mutates each row["errors"] based on the rules.
    """
    mode = (mode or "add_update").strip().lower()
    db_names, db_bars, db_uids = build_db_maps(db_name_to_pid, db_bar_to_pid, db_uid_to_pid)
    dup_names, dup_barcodes, dup_unitids = analyze_infile_dups(rows)

    for r in rows:
        d = r.get("data", {})
        errs = dict(r.get("errors") or {})
        name_raw = d.get("name", "")
        name = _norm(name_raw)

        # current product id in DB (if exists)
        current_pid = db_names.get(name) if name else None

        # ---- NAME policy by mode ----
        if mode == "new":
            if current_pid:
                errs["name_db"] = "اسم المنتج موجود مسبقاً في قاعدة البيانات."
        elif mode == "update":
            if not current_pid:
                errs["name_db"] = "اسم المنتج غير موجود في قاعدة البيانات (وضع التعديل فقط)."
        # add_update => no DB-name error

        # duplicate name in the same upload => flag both
        if name and name in dup_names:
            errs["name_dup_file"] = "اسم المنتج مكرر داخل الملف."

        # ---- BARCODE / UNIT-ID policy ----
        # Use _as_tokens so field type never breaks us
        row_barcodes = _as_tokens(d.get("barcodes_u1")) + _as_tokens(d.get("barcodes_u2"))
        row_unitids  = _as_tokens(d.get("unit_ids_u1")) + _as_tokens(d.get("unit_ids_u2"))

        def check_pool(values: List[str], pool: Dict[str, int], dup_set: Set[str],
                       key_err: str, other_owner_err: str, exists_db_err: str):
            for v in values:
                vv = (v or "").strip()
                if not vv:
                    continue

                # in-file duplicate
                if vv in dup_set:
                    # NOTE: we only keep the first message per category so UI stays tidy
                    if key_err not in errs:
                        errs[key_err] = f"{vv}: مكرر داخل الملف."
                    # continue checking DB ownership for the same vv

                owner_pid = pool.get(vv)
                if owner_pid:
                    if mode == "new":
                        if exists_db_err not in errs:
                            errs[exists_db_err] = f"{vv}: موجود في قاعدة البيانات."
                    elif mode == "add_update":
                        if current_pid:
                            # updating existing product => allowed iff belongs to THIS product
                            if owner_pid != current_pid:
                                if other_owner_err not in errs:
                                    errs[other_owner_err] = f"{vv}: مستخدم لدى منتج آخر."
                        else:
                            # creating new product => must NOT exist in DB
                            if exists_db_err not in errs:
                                errs[exists_db_err] = f"{vv}: موجود في قاعدة البيانات."
                    elif mode == "update":
                        # must be updating an existing product
                        if not current_pid:
                            errs["name_db"] = "اسم غير موجود في قاعدة البيانات (وضع التعديل فقط)."
                        else:
                            # allowed only if belongs to THIS product
                            if owner_pid != current_pid:
                                if other_owner_err not in errs:
                                    errs[other_owner_err] = f"{vv}: مستخدم لدى منتج آخر."

        # barcodes
        check_pool(
            row_barcodes, db_bars, dup_barcodes,
            key_err="barcodes_dup_file",
            other_owner_err="barcodes_conflict_other",
            exists_db_err="barcodes_exists_db",
        )

        # unit ids (codes)
        check_pool(
            row_unitids, db_uids, dup_unitids,
            key_err="unitids_dup_file",
            other_owner_err="unitids_conflict_other",
            exists_db_err="unitids_exists_db",
        )

        r["errors"] = errs
