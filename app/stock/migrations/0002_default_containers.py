from __future__ import annotations

from django.db import migrations


def create_core_containers(apps, schema_editor):
    ProductContainer = apps.get_model("stock", "ProductContainer")

    defaults = [
        # name,        code,    is_store, sort_order
        ("Store",      "store", True,     1),
        ("Warehouse 1","wh1",   False,    2),
        ("Warehouse 2","wh2",   False,    3),
    ]

    for name, code, is_store, sort_order in defaults:
        ProductContainer.objects.update_or_create(
            code=code,
            defaults={
                "name": name,
                "is_store": is_store,
                "is_active": True,
                "sort_order": sort_order,
            },
        )


def delete_core_containers(apps, schema_editor):
    # Only used if you ever migrate backwards.
    ProductContainer = apps.get_model("stock", "ProductContainer")
    ProductContainer.objects.filter(code__in=["store", "wh1", "wh2"]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("stock", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_core_containers, delete_core_containers),
    ]
