from decimal import Decimal

from django.db import migrations, models


def backfill_billitem_snapshot_more(apps, schema_editor):
    Product = apps.get_model("catalog", "Product")
    BillItem = apps.get_model("billing", "BillItem")
    ProviderReturnItem = apps.get_model("billing", "ProviderReturnItem")

    prod_map = {p.id: p for p in Product.objects.all()}

    for it in BillItem.objects.select_related("bill").all().iterator():
        prod = prod_map.get(it.product_id)
        update_fields = []

        if not (it.product_name_at_txn or "").strip():
            it.product_name_at_txn = getattr(prod, "name", "") if prod else ""
            update_fields.append("product_name_at_txn")

        if it.qty_used_at_txn is None:
            conv = getattr(it, "conv_factor_at_txn", None) or Decimal("1")
            try:
                conv_val = Decimal(str(conv))
            except Exception:
                conv_val = Decimal("1")
            if not conv_val or conv_val <= 0:
                conv_val = Decimal("1")
            if int(it.unit_index or 1) == 2:
                it.qty_used_at_txn = abs((it.qty_primary or Decimal("0")) / conv_val)
            else:
                it.qty_used_at_txn = abs(it.qty_primary or Decimal("0"))
            update_fields.append("qty_used_at_txn")
        else:
            if it.qty_used_at_txn < 0:
                it.qty_used_at_txn = abs(it.qty_used_at_txn)
                update_fields.append("qty_used_at_txn")

        if it.fx_rate_at_txn is None:
            b = getattr(it, "bill", None)
            fx = None
            if b is not None:
                fx = getattr(b, "fx_rate_usd_to_syp_used", None) or getattr(b, "fx_usd_syp", None)
            it.fx_rate_at_txn = fx
            update_fields.append("fx_rate_at_txn")

        if update_fields:
            it.save(update_fields=update_fields)

    for it in ProviderReturnItem.objects.select_related("ret").all().iterator():
        prod = prod_map.get(it.product_id)
        update_fields = []

        if not (it.product_name_at_txn or "").strip():
            it.product_name_at_txn = getattr(prod, "name", "") if prod else ""
            update_fields.append("product_name_at_txn")

        if it.qty_used_at_txn is None:
            conv = getattr(it, "conv_factor_at_txn", None) or Decimal("1")
            try:
                conv_val = Decimal(str(conv))
            except Exception:
                conv_val = Decimal("1")
            if not conv_val or conv_val <= 0:
                conv_val = Decimal("1")
            if int(it.unit_index or 1) == 2:
                it.qty_used_at_txn = abs((it.qty_primary or Decimal("0")) / conv_val)
            else:
                it.qty_used_at_txn = abs(it.qty_primary or Decimal("0"))
            update_fields.append("qty_used_at_txn")
        else:
            if it.qty_used_at_txn < 0:
                it.qty_used_at_txn = abs(it.qty_used_at_txn)
                update_fields.append("qty_used_at_txn")

        if it.fx_rate_at_txn is None:
            r = getattr(it, "ret", None)
            fx = getattr(r, "fx_rate_used", None) if r is not None else None
            it.fx_rate_at_txn = fx
            update_fields.append("fx_rate_at_txn")

        if update_fields:
            it.save(update_fields=update_fields)


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0016_billitem_snapshot_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="billitem",
            name="product_name_at_txn",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="billitem",
            name="qty_used_at_txn",
            field=models.DecimalField(blank=True, decimal_places=3, max_digits=14, null=True),
        ),
        migrations.AddField(
            model_name="billitem",
            name="fx_rate_at_txn",
            field=models.DecimalField(blank=True, decimal_places=6, max_digits=18, null=True),
        ),
        migrations.AddField(
            model_name="providerreturnitem",
            name="product_name_at_txn",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="providerreturnitem",
            name="qty_used_at_txn",
            field=models.DecimalField(blank=True, decimal_places=3, max_digits=14, null=True),
        ),
        migrations.AddField(
            model_name="providerreturnitem",
            name="fx_rate_at_txn",
            field=models.DecimalField(blank=True, decimal_places=6, max_digits=18, null=True),
        ),
        migrations.RunPython(backfill_billitem_snapshot_more, migrations.RunPython.noop),
    ]
