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


def backfill_public_ids(apps, schema_editor):
    Bill = apps.get_model("billing", "Bill")
    ProviderReturn = apps.get_model("billing", "ProviderReturn")

    bill_prefix = "PB-"
    bill_used = set(
        str(v or "").strip()
        for v in Bill.objects.exclude(public_id__isnull=True).exclude(public_id="").values_list("public_id", flat=True)
    )
    bill_max = 0
    for value in bill_used:
        num = _extract_number(value=value, prefix=bill_prefix)
        if num is not None and num > bill_max:
            bill_max = num
    bill_next = bill_max + 1

    for bill in Bill.objects.order_by("id"):
        current = str(getattr(bill, "public_id", "") or "").strip()
        if current:
            continue
        preferred_num = int(getattr(bill, "serial", 0) or 0)
        candidate = ""
        if preferred_num > 0:
            preferred = _format_public_id(bill_prefix, preferred_num)
            if preferred not in bill_used:
                candidate = preferred
        if not candidate:
            while True:
                preferred = _format_public_id(bill_prefix, bill_next)
                bill_next += 1
                if preferred not in bill_used:
                    candidate = preferred
                    break
        bill.public_id = candidate
        bill.save(update_fields=["public_id"])
        bill_used.add(candidate)

    return_prefix = "PR-"
    return_used = set(
        str(v or "").strip()
        for v in ProviderReturn.objects.exclude(public_id__isnull=True).exclude(public_id="").values_list("public_id", flat=True)
    )
    return_max = 0
    for value in return_used:
        num = _extract_number(value=value, prefix=return_prefix)
        if num is not None and num > return_max:
            return_max = num
    return_next = return_max + 1

    for ret in ProviderReturn.objects.order_by("id"):
        current = str(getattr(ret, "public_id", "") or "").strip()
        if current:
            continue
        preferred_num = int(getattr(ret, "serial", 0) or 0)
        candidate = ""
        if preferred_num > 0:
            preferred = _format_public_id(return_prefix, preferred_num)
            if preferred not in return_used:
                candidate = preferred
        if not candidate:
            while True:
                preferred = _format_public_id(return_prefix, return_next)
                return_next += 1
                if preferred not in return_used:
                    candidate = preferred
                    break
        ret.public_id = candidate
        ret.save(update_fields=["public_id"])
        return_used.add(candidate)

    serial_to_bill_public: dict[int, str] = {}
    for bill in Bill.objects.exclude(serial__isnull=True).exclude(public_id="").values("serial", "public_id"):
        serial = int(bill["serial"] or 0)
        public_id = str(bill["public_id"] or "").strip()
        if serial > 0 and public_id:
            serial_to_bill_public[serial] = public_id

    for ret in ProviderReturn.objects.filter(source_bill_public_id="").exclude(source_bill_serial__isnull=True):
        serial = int(ret.source_bill_serial or 0)
        mapped = serial_to_bill_public.get(serial, "")
        if not mapped:
            continue
        ret.source_bill_public_id = mapped
        ret.save(update_fields=["source_bill_public_id"])


def noop_reverse(apps, schema_editor):
    return


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0001_publicidsequence"),
        ("billing", "0019_bill_creation_payment_intent"),
    ]

    operations = [
        migrations.AddField(
            model_name="bill",
            name="public_id",
            field=models.CharField(blank=True, db_index=True, max_length=24, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="providerreturn",
            name="public_id",
            field=models.CharField(blank=True, db_index=True, max_length=24, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="providerreturn",
            name="source_bill_public_id",
            field=models.CharField(blank=True, db_index=True, default="", max_length=24),
        ),
        migrations.RunPython(backfill_public_ids, noop_reverse),
        migrations.AlterField(
            model_name="bill",
            name="public_id",
            field=models.CharField(db_index=True, default=billing.models._bill_public_id_default, editable=False, max_length=24, unique=True),
        ),
        migrations.AlterField(
            model_name="providerreturn",
            name="public_id",
            field=models.CharField(db_index=True, default=billing.models._provider_return_public_id_default, editable=False, max_length=24, unique=True),
        ),
    ]
