from __future__ import annotations

from django.db import migrations, models
from django.db.models import Count
from django.db.models.functions import Lower


def _fail_on_global_duplicates(apps, schema_editor):
    Product = apps.get_model("catalog", "Product")
    ProductBarcode = apps.get_model("catalog", "ProductBarcode")
    ProductUnitId = apps.get_model("catalog", "ProductUnitId")

    name_dupes = list(
        Product.objects
        .annotate(name_ci=Lower("name"))
        .values("name_ci")
        .annotate(cnt=Count("id"))
        .filter(cnt__gt=1)
        .order_by("-cnt", "name_ci")[:10]
    )
    barcode_dupes = list(
        ProductBarcode.objects
        .values("barcode")
        .annotate(cnt=Count("id"))
        .filter(cnt__gt=1)
        .order_by("-cnt", "barcode")[:10]
    )
    unit_id_dupes = list(
        ProductUnitId.objects
        .values("value")
        .annotate(cnt=Count("id"))
        .filter(cnt__gt=1)
        .order_by("-cnt", "value")[:10]
    )

    if not (name_dupes or barcode_dupes or unit_id_dupes):
        return

    parts: list[str] = [
        "Cannot enforce global product uniqueness. Resolve duplicates first.",
    ]
    if name_dupes:
        parts.append("Duplicate product names (case-insensitive):")
        parts.extend([f"- {row['name_ci']} (count={row['cnt']})" for row in name_dupes])
    if barcode_dupes:
        parts.append("Duplicate barcodes:")
        parts.extend([f"- {row['barcode']} (count={row['cnt']})" for row in barcode_dupes])
    if unit_id_dupes:
        parts.append("Duplicate unit IDs:")
        parts.extend([f"- {row['value']} (count={row['cnt']})" for row in unit_id_dupes])
    raise RuntimeError("\n".join(parts))


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0016_remove_product_product_number"),
    ]

    operations = [
        migrations.RunPython(_fail_on_global_duplicates, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name="product",
            name="uq_product_name_ci_active",
        ),
        migrations.AddConstraint(
            model_name="product",
            constraint=models.UniqueConstraint(
                Lower("name"),
                name="uq_product_name_ci_global",
                violation_error_message="اسم المنتج موجود مسبقًا (بدون حساسية لحالة الأحرف).",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="productunitid",
            name="uq_unit_id_value_active",
        ),
        migrations.AddConstraint(
            model_name="productunitid",
            constraint=models.UniqueConstraint(
                fields=("value",),
                name="uq_unit_id_value_global",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="productbarcode",
            name="uq_barcode_active",
        ),
        migrations.AddConstraint(
            model_name="productbarcode",
            constraint=models.UniqueConstraint(
                fields=("barcode",),
                name="uq_barcode_global",
            ),
        ),
    ]
