from decimal import Decimal

from django.db import migrations, models


def _conv_or_one(prod):
    try:
        c = getattr(prod, "conversion_factor", None)
        c = Decimal(str(c)) if c not in (None, "") else Decimal("1")
    except Exception:
        c = Decimal("1")
    return c if c and c > 0 else Decimal("1")


def backfill_billitem_snapshots(apps, schema_editor):
    Product = apps.get_model("catalog", "Product")
    BillItem = apps.get_model("billing", "BillItem")
    ProviderReturnItem = apps.get_model("billing", "ProviderReturnItem")

    prod_map = {p.id: p for p in Product.objects.all()}

    for it in BillItem.objects.all().iterator():
        prod = prod_map.get(it.product_id)
        if not prod:
            continue
        conv = _conv_or_one(prod)
        unit1 = prod.get_unit_primary_display() if getattr(prod, "unit_primary", None) else ""
        unit2 = prod.get_unit_secondary_display() if getattr(prod, "unit_secondary", None) else ""
        it.conv_factor_at_txn = conv
        it.unit_1_label_at_txn = unit1 or ""
        it.unit_2_label_at_txn = unit2 or ""
        it.save(update_fields=["conv_factor_at_txn", "unit_1_label_at_txn", "unit_2_label_at_txn"])

    for it in ProviderReturnItem.objects.all().iterator():
        prod = prod_map.get(it.product_id)
        if not prod:
            continue
        conv = _conv_or_one(prod)
        unit1 = prod.get_unit_primary_display() if getattr(prod, "unit_primary", None) else ""
        unit2 = prod.get_unit_secondary_display() if getattr(prod, "unit_secondary", None) else ""
        it.conv_factor_at_txn = conv
        it.unit_1_label_at_txn = unit1 or ""
        it.unit_2_label_at_txn = unit2 or ""
        it.save(update_fields=["conv_factor_at_txn", "unit_1_label_at_txn", "unit_2_label_at_txn"])


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0015_bill_money_container"),
    ]

    operations = [
        migrations.AddField(
            model_name="billitem",
            name="conv_factor_at_txn",
            field=models.DecimalField(decimal_places=4, default=Decimal("1.0000"), max_digits=12),
        ),
        migrations.AddField(
            model_name="billitem",
            name="unit_1_label_at_txn",
            field=models.CharField(default="", max_length=32),
        ),
        migrations.AddField(
            model_name="billitem",
            name="unit_2_label_at_txn",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.AddField(
            model_name="providerreturnitem",
            name="conv_factor_at_txn",
            field=models.DecimalField(decimal_places=4, default=Decimal("1.0000"), max_digits=12),
        ),
        migrations.AddField(
            model_name="providerreturnitem",
            name="unit_1_label_at_txn",
            field=models.CharField(default="", max_length=32),
        ),
        migrations.AddField(
            model_name="providerreturnitem",
            name="unit_2_label_at_txn",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.RunPython(backfill_billitem_snapshots, migrations.RunPython.noop),
    ]
