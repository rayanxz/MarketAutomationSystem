from decimal import Decimal

from django.db import migrations, models


def backfill_productmovement_snapshots(apps, schema_editor):
    ProductMovement = apps.get_model("inventory", "ProductMovement")
    Product = apps.get_model("catalog", "Product")

    prod_map = {p.id: p for p in Product.objects.all()}

    for mv in ProductMovement.objects.all().iterator():
        prod = prod_map.get(mv.product_id)
        update_fields = []

        if not (mv.product_name_at_txn or "").strip():
            mv.product_name_at_txn = getattr(prod, "name", "") if prod else ""
            update_fields.append("product_name_at_txn")

        if mv.unit_cost_at_txn is None:
            mv.unit_cost_at_txn = mv.unit_cost
            update_fields.append("unit_cost_at_txn")

        if mv.qty_primary_at_txn is None:
            mv.qty_primary_at_txn = abs(mv.qty_primary or Decimal("0"))
            update_fields.append("qty_primary_at_txn")
        else:
            if mv.qty_primary_at_txn < 0:
                mv.qty_primary_at_txn = abs(mv.qty_primary_at_txn)
                update_fields.append("qty_primary_at_txn")

        if mv.unit_index_used_at_txn is None:
            mv.unit_index_used_at_txn = mv.unit_index
            update_fields.append("unit_index_used_at_txn")

        if mv.conversion_factor_at_txn is None:
            conv = None
            if prod is not None:
                conv = getattr(prod, "conversion_factor", None)
            try:
                conv_val = Decimal(str(conv)) if conv not in (None, "") else Decimal("1")
            except Exception:
                conv_val = Decimal("1")
            if not conv_val or conv_val <= 0:
                conv_val = Decimal("1")
            mv.conversion_factor_at_txn = conv_val
            update_fields.append("conversion_factor_at_txn")

        if mv.qty_used_at_txn is None:
            conv_val = mv.conversion_factor_at_txn or Decimal("1")
            if mv.unit_index == 2:
                try:
                    mv.qty_used_at_txn = abs((mv.qty_primary or Decimal("0")) / conv_val)
                except Exception:
                    mv.qty_used_at_txn = abs(mv.qty_primary or Decimal("0"))
            else:
                mv.qty_used_at_txn = abs(mv.qty_primary or Decimal("0"))
            update_fields.append("qty_used_at_txn")
        else:
            if mv.qty_used_at_txn < 0:
                mv.qty_used_at_txn = abs(mv.qty_used_at_txn)
                update_fields.append("qty_used_at_txn")

        if not (mv.unit_1_label_at_txn or "").strip() and prod is not None:
            try:
                mv.unit_1_label_at_txn = prod.get_unit_primary_display()
            except Exception:
                mv.unit_1_label_at_txn = ""
            update_fields.append("unit_1_label_at_txn")

        if not (mv.unit_2_label_at_txn or "").strip() and prod is not None:
            try:
                mv.unit_2_label_at_txn = prod.get_unit_secondary_display()
            except Exception:
                mv.unit_2_label_at_txn = ""
            update_fields.append("unit_2_label_at_txn")

        if update_fields:
            mv.save(update_fields=update_fields)


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0004_productmovement_origin_source_app_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="productmovement",
            name="product_name_at_txn",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="productmovement",
            name="unit_cost_at_txn",
            field=models.DecimalField(blank=True, decimal_places=4, max_digits=12, null=True),
        ),
        migrations.AddField(
            model_name="productmovement",
            name="cost_currency_at_txn",
            field=models.CharField(blank=True, max_length=3, null=True),
        ),
        migrations.AddField(
            model_name="productmovement",
            name="sale_unit_price_at_txn",
            field=models.DecimalField(blank=True, decimal_places=3, max_digits=14, null=True),
        ),
        migrations.AddField(
            model_name="productmovement",
            name="sale_currency_at_txn",
            field=models.CharField(blank=True, max_length=3, null=True),
        ),
        migrations.AddField(
            model_name="productmovement",
            name="fx_rate_at_txn",
            field=models.DecimalField(blank=True, decimal_places=6, max_digits=18, null=True),
        ),
        migrations.AddField(
            model_name="productmovement",
            name="qty_used_at_txn",
            field=models.DecimalField(blank=True, decimal_places=3, max_digits=14, null=True),
        ),
        migrations.AddField(
            model_name="productmovement",
            name="qty_primary_at_txn",
            field=models.DecimalField(blank=True, decimal_places=3, max_digits=14, null=True),
        ),
        migrations.AddField(
            model_name="productmovement",
            name="unit_index_used_at_txn",
            field=models.IntegerField(blank=True, choices=[(1, "الوحدة الأولى"), (2, "الوحدة الثانية")], null=True),
        ),
        migrations.AddField(
            model_name="productmovement",
            name="conversion_factor_at_txn",
            field=models.DecimalField(blank=True, decimal_places=4, max_digits=12, null=True),
        ),
        migrations.AddField(
            model_name="productmovement",
            name="unit_1_label_at_txn",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.AddField(
            model_name="productmovement",
            name="unit_2_label_at_txn",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.AddField(
            model_name="productmovement",
            name="discount_amount_at_txn",
            field=models.DecimalField(blank=True, decimal_places=3, max_digits=14, null=True),
        ),
        migrations.AddField(
            model_name="productmovement",
            name="discount_pct_at_txn",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=5, null=True),
        ),
        migrations.RunPython(backfill_productmovement_snapshots, migrations.RunPython.noop),
    ]
