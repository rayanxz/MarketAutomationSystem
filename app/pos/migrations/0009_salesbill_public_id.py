from __future__ import annotations

from django.db import migrations, models

import pos.models


def _format_public_id(prefix: str, number: int) -> str:
    return f"{prefix}{int(number):03d}"


def _extract_number(value: str, prefix: str) -> int | None:
    raw = str(value or "").strip().upper()
    pfx = prefix.upper()
    if not raw.startswith(pfx):
        return None
    suffix = raw[len(pfx):]
    if not suffix.isdigit():
        return None
    try:
        return int(suffix)
    except Exception:
        return None


def backfill_sales_bill_public_ids(apps, schema_editor):
    SalesBill = apps.get_model("pos", "SalesBill")

    prefix = "PS-"
    used = set(
        str(v or "").strip()
        for v in SalesBill.objects.exclude(public_id__isnull=True).exclude(public_id="").values_list("public_id", flat=True)
    )
    max_seen = 0
    for value in used:
        num = _extract_number(value=value, prefix=prefix)
        if num is not None and num > max_seen:
            max_seen = num
    next_value = max_seen + 1

    for bill in SalesBill.objects.order_by("id"):
        current = str(getattr(bill, "public_id", "") or "").strip()
        if current:
            continue

        preferred_num = int(getattr(bill, "id", 0) or 0)
        candidate = ""
        if preferred_num > 0:
            preferred = _format_public_id(prefix, preferred_num)
            if preferred not in used:
                candidate = preferred

        if not candidate:
            while True:
                preferred = _format_public_id(prefix, next_value)
                next_value += 1
                if preferred not in used:
                    candidate = preferred
                    break

        bill.public_id = candidate
        bill.save(update_fields=["public_id"])
        used.add(candidate)


def noop_reverse(apps, schema_editor):
    return


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0001_publicidsequence"),
        ("pos", "0008_sales_snapshot_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="salesbill",
            name="public_id",
            field=models.CharField(blank=True, db_index=True, max_length=24, null=True, unique=True),
        ),
        migrations.RunPython(backfill_sales_bill_public_ids, noop_reverse),
        migrations.AlterField(
            model_name="salesbill",
            name="public_id",
            field=models.CharField(db_index=True, default=pos.models._sales_bill_public_id_default, editable=False, max_length=24, unique=True),
        ),
    ]
