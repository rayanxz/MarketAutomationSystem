from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Dict, Iterable, List, Optional, Set, Tuple

from django.db import migrations, models
from django.db.models.expressions import RawSQL


# Frozen from core.money_precision_guards._MODEL_MONEY_FIELDS.
MONEY_FIELDS: Dict[Tuple[str, str], Tuple[str, ...]] = {
    ("financials", "MoneyContainer"): ("balance_syp", "balance_usd"),
    ("financials", "PostingLine"): ("amount",),
    ("billing", "Bill"): (
        "creation_paid_syp",
        "creation_paid_usd",
        "total",
        "subtotal_syp",
        "subtotal_usd",
        "total_syp",
        "total_usd",
        "grand_total_syp",
        "grand_total_usd",
    ),
    ("billing", "BillItem"): ("cost", "price", "line_total"),
    ("billing", "ProviderReturn"): ("total", "total_syp", "total_usd", "initial_paid"),
    ("billing", "ProviderReturnItem"): ("cost", "line_total"),
    ("pos", "SalesBill"): ("total_amount", "total_syp", "total_usd", "paid_amount"),
    ("pos", "SalesBillRow"): ("unit_price", "unit_cost_at_txn", "disc_amount"),
    ("pos", "SalesReturn"): ("total_syp", "total_usd"),
    ("pos", "SalesReturnRow"): ("unit_price_at_sale", "unit_cost_at_txn", "discount_amount_at_txn", "line_total"),
    ("debts", "DebtRecord"): ("total_syp", "total_usd", "remaining_syp", "remaining_usd"),
    ("debts", "DebtSettlement"): ("payment_syp", "payment_usd", "applied_syp", "applied_usd"),
    ("debts", "DebtorDebt"): ("total", "paid_amount"),
    ("debts", "DebtorPayment"): ("amount",),
    ("debts", "CreditorDebt"): ("total", "collected"),
    ("debts", "CreditorReceipt"): ("amount",),
}

MONEY_EXP = Decimal("0.01")


def _constraint_name(table_name: str, column_name: str) -> str:
    return f"chk_{table_name}_{column_name}_2dp"


def _to_decimal(raw) -> Optional[Decimal]:
    try:
        dec = Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not dec.is_finite():
        return None
    return dec


def _round_money_2dp(value: Decimal) -> Decimal:
    return value.quantize(MONEY_EXP, rounding=ROUND_HALF_UP)


def _money_targets(apps) -> Iterable[Tuple[models.Model, str, str, str]]:
    for (app_label, model_name), field_names in MONEY_FIELDS.items():
        model = apps.get_model(app_label, model_name)
        table_name = model._meta.db_table
        pk_column = model._meta.pk.column
        for field_name in field_names:
            field = model._meta.get_field(field_name)
            yield model, table_name, pk_column, field.column


def _scan_column(cursor, *, qn, table_name: str, pk_column: str, column_name: str) -> List[Tuple[object, Decimal, Decimal]]:
    cursor.execute(
        f'SELECT {qn(pk_column)}, {qn(column_name)} FROM {qn(table_name)} WHERE {qn(column_name)} IS NOT NULL'
    )
    rows = cursor.fetchall()
    violations: List[Tuple[object, Decimal, Decimal]] = []
    for pk, raw_value in rows:
        dec = _to_decimal(raw_value)
        if dec is None:
            raise RuntimeError(f"Invalid numeric value in {table_name}.{column_name} for pk={pk!r}: {raw_value!r}")
        rounded = _round_money_2dp(dec)
        if dec != rounded:
            violations.append((pk, dec, rounded))
    return violations


def _normalize_and_assert_money_2dp(apps, schema_editor):
    connection = schema_editor.connection
    qn = connection.ops.quote_name
    total_violations_before = 0
    total_updates = 0

    with connection.cursor() as cursor:
        for _model, table_name, pk_column, column_name in _money_targets(apps):
            violations = _scan_column(
                cursor,
                qn=qn,
                table_name=table_name,
                pk_column=pk_column,
                column_name=column_name,
            )
            if not violations:
                continue
            total_violations_before += len(violations)
            for pk, _before, after in violations:
                cursor.execute(
                    f"UPDATE {qn(table_name)} SET {qn(column_name)}=%s WHERE {qn(pk_column)}=%s",
                    [str(after), pk],
                )
                total_updates += 1

        remaining_samples = []
        remaining_count = 0
        for _model, table_name, pk_column, column_name in _money_targets(apps):
            violations = _scan_column(
                cursor,
                qn=qn,
                table_name=table_name,
                pk_column=pk_column,
                column_name=column_name,
            )
            if not violations:
                continue
            for pk, before, after in violations:
                remaining_count += 1
                if len(remaining_samples) < 10:
                    remaining_samples.append(
                        f"{table_name}.{column_name} pk={pk!r} value={before} expected={after}"
                    )

    if remaining_count:
        sample_text = "; ".join(remaining_samples)
        raise RuntimeError(
            f"2dp normalization failed: {remaining_count} violating value(s) remain. Samples: {sample_text}"
        )

    print(
        f"[money-2dp] normalized values before constraints: "
        f"violations={total_violations_before}, updates={total_updates}"
    )


def _table_exists(cursor, table_name: str) -> bool:
    cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=%s",
        [table_name],
    )
    return cursor.fetchone() is not None


def _group_targets_by_table(apps) -> Dict[str, Tuple[models.Model, Set[str]]]:
    grouped: Dict[str, Tuple[models.Model, Set[str]]] = {}
    for model, table_name, _pk_column, column_name in _money_targets(apps):
        if table_name not in grouped:
            grouped[table_name] = (model, set())
        grouped[table_name][1].add(column_name)
    return grouped


def _constraint_clause(table_name: str, column_name: str) -> str:
    constraint = _constraint_name(table_name, column_name)
    col = f'"{column_name}"'
    col_txt = f"CAST({col} AS TEXT)"
    dot_pos = f"instr({col_txt}, '.')"
    frac = f"substr({col_txt}, {dot_pos} + 1)"
    # SQLite REAL arithmetic can trigger false positives with multiplication checks
    # due to binary floating representation. Use TEXT-based fractional-length checks.
    predicate = (
        f"{col} IS NULL OR {dot_pos} = 0 OR "
        f"length(rtrim({frac}, '0')) <= 2"
    )
    return (
        f'CONSTRAINT "{constraint}" CHECK '
        f"({predicate})"
    )


def _append_constraints_to_create_sql(create_sql: str, clauses: List[str]) -> str:
    base = (create_sql or "").strip()
    if not base or not clauses:
        return base
    idx = base.rfind(")")
    if idx == -1:
        raise RuntimeError(f"Unable to parse CREATE TABLE SQL: {base}")
    prefix = base[:idx].rstrip()
    suffix = base[idx:]
    additions = "".join([",\n    " + clause for clause in clauses])
    return f"{prefix}{additions}\n{suffix}"


def _rebuild_table_sqlite(cursor, table_name: str, create_sql: str, *, suffix: str) -> None:
    if not _table_exists(cursor, table_name):
        return

    cursor.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=%s AND sql IS NOT NULL ORDER BY name",
        [table_name],
    )
    index_sql = [row[0] for row in cursor.fetchall() if row and row[0]]

    cursor.execute(f'PRAGMA table_info("{table_name}")')
    old_columns = [row[1] for row in cursor.fetchall()]

    new_table_name = f"{table_name}_{suffix}"
    new_sql = create_sql.replace(
        f'CREATE TABLE "{table_name}"',
        f'CREATE TABLE "{new_table_name}"',
        1,
    )
    cursor.execute(new_sql)

    cursor.execute(f'PRAGMA table_info("{new_table_name}")')
    new_columns = [row[1] for row in cursor.fetchall()]
    common_columns = [col for col in old_columns if col in new_columns]
    columns_csv = ", ".join(f'"{col}"' for col in common_columns)
    cursor.execute(
        f'INSERT INTO "{new_table_name}" ({columns_csv}) SELECT {columns_csv} FROM "{table_name}"'
    )

    cursor.execute(f'DROP TABLE "{table_name}"')
    cursor.execute(f'ALTER TABLE "{new_table_name}" RENAME TO "{table_name}"')
    for idx_sql in index_sql:
        cursor.execute(idx_sql)


def _add_sqlite_constraints_by_rebuild(apps, schema_editor) -> None:
    grouped = _group_targets_by_table(apps)
    connection = schema_editor.connection
    changed_tables = 0

    with connection.cursor() as cursor:
        cursor.execute("PRAGMA foreign_keys=OFF")
        try:
            for table_name, (_model, columns) in grouped.items():
                cursor.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name=%s",
                    [table_name],
                )
                row = cursor.fetchone()
                if not row or not row[0]:
                    raise RuntimeError(f"Missing CREATE TABLE SQL for {table_name}")
                create_sql = row[0]

                clauses: List[str] = []
                for column_name in sorted(columns):
                    cname = _constraint_name(table_name, column_name)
                    if cname in create_sql:
                        continue
                    clauses.append(_constraint_clause(table_name, column_name))

                if not clauses:
                    continue

                updated_create_sql = _append_constraints_to_create_sql(create_sql, clauses)
                _rebuild_table_sqlite(cursor, table_name, updated_create_sql, suffix="new_0011_2dp")
                changed_tables += 1
        finally:
            cursor.execute("PRAGMA foreign_keys=ON")

    print(f"[money-2dp] sqlite tables rebuilt with 2dp checks: {changed_tables}")


def _add_non_sqlite_constraints(apps, schema_editor) -> None:
    connection = schema_editor.connection
    added = 0
    for model, table_name, _pk_column, column_name in _money_targets(apps):
        name = _constraint_name(table_name, column_name)
        with connection.cursor() as cursor:
            existing = connection.introspection.get_constraints(cursor, table_name)
        if name in existing:
            continue
        constraint = models.CheckConstraint(
            check=RawSQL(
                f'"{column_name}" IS NULL OR ("{column_name}" * 100) = CAST("{column_name}" * 100 AS INTEGER)',
                params=[],
                output_field=models.BooleanField(),
            ),
            name=name,
        )
        schema_editor.add_constraint(model, constraint)
        added += 1
    print(f"[money-2dp] non-sqlite constraints added: {added}")


def _add_money_2dp_constraints(apps, schema_editor):
    if schema_editor.connection.vendor == "sqlite":
        _add_sqlite_constraints_by_rebuild(apps, schema_editor)
        return
    _add_non_sqlite_constraints(apps, schema_editor)


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("billing", "0023_alter_bill_creation_paid_syp_and_more"),
        ("debts", "0010_legacy_debt_money_2dp"),
        ("financials", "0018_alter_postingline_amount"),
        ("pos", "0012_alter_salesbill_paid_amount"),
    ]

    operations = [
        migrations.RunPython(_normalize_and_assert_money_2dp, migrations.RunPython.noop),
        migrations.RunPython(_add_money_2dp_constraints, migrations.RunPython.noop),
    ]
