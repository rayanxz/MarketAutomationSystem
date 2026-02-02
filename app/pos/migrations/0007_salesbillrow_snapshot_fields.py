from decimal import Decimal

from django.db import migrations, models


def _conv_or_one(prod):
    try:
        c = getattr(prod, "conversion_factor", None)
        c = Decimal(str(c)) if c not in (None, "") else Decimal("1")
    except Exception:
        c = Decimal("1")
    return c if c and c > 0 else Decimal("1")


def backfill_pos_row_snapshots(apps, schema_editor):
    Product = apps.get_model("catalog", "Product")
    SalesBillRow = apps.get_model("pos", "SalesBillRow")
    SalesReturnRow = apps.get_model("pos", "SalesReturnRow")

    prod_map = {p.id: p for p in Product.objects.all()}

    for r in SalesBillRow.objects.all().iterator():
        prod = prod_map.get(r.product_id)
        if not prod:
            continue
        conv = _conv_or_one(prod)
        unit1 = prod.get_unit_primary_display() if getattr(prod, "unit_primary", None) else ""
        unit2 = prod.get_unit_secondary_display() if getattr(prod, "unit_secondary", None) else ""
        r.conv_factor_at_txn = conv
        r.unit_1_label_at_txn = unit1 or ""
        r.unit_2_label_at_txn = unit2 or ""
        r.save(update_fields=["conv_factor_at_txn", "unit_1_label_at_txn", "unit_2_label_at_txn"])

    for r in SalesReturnRow.objects.all().iterator():
        prod = prod_map.get(r.product_id)
        if not prod:
            continue
        conv = _conv_or_one(prod)
        unit1 = prod.get_unit_primary_display() if getattr(prod, "unit_primary", None) else ""
        unit2 = prod.get_unit_secondary_display() if getattr(prod, "unit_secondary", None) else ""
        r.conv_factor_at_txn = conv
        r.unit_1_label_at_txn = unit1 or ""
        r.unit_2_label_at_txn = unit2 or ""
        r.save(update_fields=["conv_factor_at_txn", "unit_1_label_at_txn", "unit_2_label_at_txn"])


class Migration(migrations.Migration):

    dependencies = [
        ("pos", "0006_rename_pos_salesr_sale_bi_f9ac5e_idx_pos_salesre_sale_bi_95284e_idx_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="salesbillrow",
            name="conv_factor_at_txn",
            field=models.DecimalField(decimal_places=4, default=Decimal("1.0000"), max_digits=12),
        ),
        migrations.AddField(
            model_name="salesbillrow",
            name="unit_1_label_at_txn",
            field=models.CharField(default="", max_length=32),
        ),
        migrations.AddField(
            model_name="salesbillrow",
            name="unit_2_label_at_txn",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.AddField(
            model_name="salesreturnrow",
            name="conv_factor_at_txn",
            field=models.DecimalField(decimal_places=4, default=Decimal("1.0000"), max_digits=12),
        ),
        migrations.AddField(
            model_name="salesreturnrow",
            name="unit_1_label_at_txn",
            field=models.CharField(default="", max_length=32),
        ),
        migrations.AddField(
            model_name="salesreturnrow",
            name="unit_2_label_at_txn",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.RunPython(backfill_pos_row_snapshots, migrations.RunPython.noop),
    ]
