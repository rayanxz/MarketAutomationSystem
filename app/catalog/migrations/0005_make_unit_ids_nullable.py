from django.db import migrations, models

class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0004_product_productset_productbarcode_product_set_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="product",
            name="unit_primary_id",
            field=models.CharField(max_length=32, unique=True, null=True, blank=True),
        ),
        migrations.AlterField(
            model_name="product",
            name="unit_secondary_id",
            field=models.CharField(max_length=32, unique=True, null=True, blank=True),
        ),
        migrations.RunSQL(
            sql=[
                ("UPDATE catalog_product SET unit_primary_id   = NULL WHERE unit_primary_id   = '';", None),
                ("UPDATE catalog_product SET unit_secondary_id = NULL WHERE unit_secondary_id = '';", None),
            ],
            reverse_sql=[
                ("UPDATE catalog_product SET unit_primary_id   = '' WHERE unit_primary_id   IS NULL;", None),
                ("UPDATE catalog_product SET unit_secondary_id = '' WHERE unit_secondary_id IS NULL;", None),
            ],
        ),
    ]
