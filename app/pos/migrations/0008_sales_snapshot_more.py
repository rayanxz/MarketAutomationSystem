from decimal import Decimal

from django.db import migrations, models


def backfill_sales_snapshots(apps, schema_editor):
    Product = apps.get_model("catalog", "Product")
    SalesBillRow = apps.get_model("pos", "SalesBillRow")
    SalesReturnRow = apps.get_model("pos", "SalesReturnRow")

    prod_map = {p.id: p for p in Product.objects.all()}

    for r in SalesBillRow.objects.select_related("bill").all().iterator():
        prod = prod_map.get(r.product_id)
        update_fields = []

        if not (r.product_name_at_txn or "").strip():
            r.product_name_at_txn = r.product_name or (getattr(prod, "name", "") if prod else "")
            update_fields.append("product_name_at_txn")

        if r.qty_primary_at_txn is None:
            conv = getattr(r, "conv_factor_at_txn", None) or Decimal("1")
            try:
                conv_val = Decimal(str(conv))
            except Exception:
                conv_val = Decimal("1")
            if not conv_val or conv_val <= 0:
                conv_val = Decimal("1")
            if int(r.uom_index or 1) == 2:
                r.qty_primary_at_txn = abs((r.qty or Decimal("0")) * conv_val)
            else:
                r.qty_primary_at_txn = abs(r.qty or Decimal("0"))
            update_fields.append("qty_primary_at_txn")
        else:
            if r.qty_primary_at_txn < 0:
                r.qty_primary_at_txn = abs(r.qty_primary_at_txn)
                update_fields.append("qty_primary_at_txn")

        if r.unit_cost_at_txn is None and prod is not None:
            r.unit_cost_at_txn = getattr(prod, "cost", None)
            update_fields.append("unit_cost_at_txn")

        if r.cost_currency_at_txn is None and prod is not None:
            r.cost_currency_at_txn = getattr(prod, "default_currency", None)
            update_fields.append("cost_currency_at_txn")

        if r.fx_rate_at_txn is None:
            b = getattr(r, "bill", None)
            r.fx_rate_at_txn = getattr(b, "fx_rate_used", None) if b is not None else None
            update_fields.append("fx_rate_at_txn")

        if update_fields:
            r.save(update_fields=update_fields)

    for r in SalesReturnRow.objects.select_related("ret", "sale_row").all().iterator():
        prod = prod_map.get(r.product_id)
        update_fields = []

        if not (r.product_name_at_txn or "").strip():
            sr = getattr(r, "sale_row", None)
            r.product_name_at_txn = getattr(sr, "product_name", "") if sr is not None else (getattr(prod, "name", "") if prod else "")
            update_fields.append("product_name_at_txn")

        if r.qty_used_at_txn is None:
            conv = getattr(r, "conv_factor_at_txn", None) or Decimal("1")
            try:
                conv_val = Decimal(str(conv))
            except Exception:
                conv_val = Decimal("1")
            if not conv_val or conv_val <= 0:
                conv_val = Decimal("1")
            if int(r.uom_index or 1) == 2:
                r.qty_used_at_txn = abs((r.qty_returned or Decimal("0")) / conv_val)
            else:
                r.qty_used_at_txn = abs(r.qty_returned or Decimal("0"))
            update_fields.append("qty_used_at_txn")
        else:
            if r.qty_used_at_txn < 0:
                r.qty_used_at_txn = abs(r.qty_used_at_txn)
                update_fields.append("qty_used_at_txn")

        if r.unit_cost_at_txn is None and prod is not None:
            r.unit_cost_at_txn = getattr(prod, "cost", None)
            update_fields.append("unit_cost_at_txn")

        if r.cost_currency_at_txn is None and prod is not None:
            r.cost_currency_at_txn = getattr(prod, "default_currency", None)
            update_fields.append("cost_currency_at_txn")

        if r.fx_rate_at_txn is None:
            ret = getattr(r, "ret", None)
            bill = getattr(ret, "sale_bill", None) if ret is not None else None
            r.fx_rate_at_txn = getattr(bill, "fx_rate_used", None) if bill is not None else None
            update_fields.append("fx_rate_at_txn")

        if r.discount_amount_at_txn is None:
            sr = getattr(r, "sale_row", None)
            r.discount_amount_at_txn = getattr(sr, "disc_amount", None) if sr is not None else None
            update_fields.append("discount_amount_at_txn")

        if r.discount_pct_at_txn is None:
            sr = getattr(r, "sale_row", None)
            r.discount_pct_at_txn = getattr(sr, "disc_pct", None) if sr is not None else None
            update_fields.append("discount_pct_at_txn")

        if update_fields:
            r.save(update_fields=update_fields)


class Migration(migrations.Migration):

    dependencies = [
        ("pos", "0007_salesbillrow_snapshot_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="salesbillrow",
            name="product_name_at_txn",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="salesbillrow",
            name="qty_primary_at_txn",
            field=models.DecimalField(blank=True, decimal_places=3, max_digits=14, null=True),
        ),
        migrations.AddField(
            model_name="salesbillrow",
            name="unit_cost_at_txn",
            field=models.DecimalField(blank=True, decimal_places=4, max_digits=12, null=True),
        ),
        migrations.AddField(
            model_name="salesbillrow",
            name="cost_currency_at_txn",
            field=models.CharField(blank=True, max_length=3, null=True),
        ),
        migrations.AddField(
            model_name="salesbillrow",
            name="fx_rate_at_txn",
            field=models.DecimalField(blank=True, decimal_places=6, max_digits=18, null=True),
        ),
        migrations.AddField(
            model_name="salesreturnrow",
            name="product_name_at_txn",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="salesreturnrow",
            name="qty_used_at_txn",
            field=models.DecimalField(blank=True, decimal_places=3, max_digits=14, null=True),
        ),
        migrations.AddField(
            model_name="salesreturnrow",
            name="unit_cost_at_txn",
            field=models.DecimalField(blank=True, decimal_places=4, max_digits=12, null=True),
        ),
        migrations.AddField(
            model_name="salesreturnrow",
            name="cost_currency_at_txn",
            field=models.CharField(blank=True, max_length=3, null=True),
        ),
        migrations.AddField(
            model_name="salesreturnrow",
            name="fx_rate_at_txn",
            field=models.DecimalField(blank=True, decimal_places=6, max_digits=18, null=True),
        ),
        migrations.AddField(
            model_name="salesreturnrow",
            name="discount_amount_at_txn",
            field=models.DecimalField(blank=True, decimal_places=3, max_digits=14, null=True),
        ),
        migrations.AddField(
            model_name="salesreturnrow",
            name="discount_pct_at_txn",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=5, null=True),
        ),
        migrations.RunPython(backfill_sales_snapshots, migrations.RunPython.noop),
    ]
