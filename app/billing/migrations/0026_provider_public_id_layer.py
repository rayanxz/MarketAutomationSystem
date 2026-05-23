from __future__ import annotations

from django.db import migrations, models

import billing.models


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


def backfill_provider_public_ids(apps, schema_editor):
    Provider = apps.get_model("billing", "Provider")

    prefix = "P-"
    used = set(
        str(v or "").strip()
        for v in Provider.objects.exclude(public_id__isnull=True).exclude(public_id="").values_list("public_id", flat=True)
    )
    max_seen = 0
    for value in used:
        num = _extract_number(value=value, prefix=prefix)
        if num is not None and num > max_seen:
            max_seen = num
    next_value = max_seen + 1

    for provider in Provider.objects.order_by("id"):
        current = str(getattr(provider, "public_id", "") or "").strip()
        if current:
            continue

        preferred_num = int(getattr(provider, "id", 0) or 0)
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

        provider.public_id = candidate
        provider.save(update_fields=["public_id"])
        used.add(candidate)


def noop_reverse(apps, schema_editor):
    return


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0001_publicidsequence"),
        ("billing", "0025_rename_billing_pr_reqfp_idx_billing_pro_provide_273707_idx"),
    ]

    operations = [
        migrations.AddField(
            model_name="provider",
            name="public_id",
            field=models.CharField(blank=True, db_index=True, max_length=24, null=True, unique=True),
        ),
        migrations.RunPython(backfill_provider_public_ids, noop_reverse),
        migrations.AlterField(
            model_name="provider",
            name="public_id",
            field=models.CharField(db_index=True, default=billing.models._provider_public_id_default, editable=False, max_length=24, unique=True),
        ),
    ]
