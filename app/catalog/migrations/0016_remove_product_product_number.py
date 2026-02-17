from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0015_normalize_single_unit_conversion_factor"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="product",
            name="uq_product_number_active",
        ),
        migrations.RemoveField(
            model_name="product",
            name="product_number",
        ),
    ]
