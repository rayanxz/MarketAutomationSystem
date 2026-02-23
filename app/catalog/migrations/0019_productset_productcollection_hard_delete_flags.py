from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0018_remove_productcollection_uq_collection_name_ci_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="productcollection",
            name="can_be_hard_deleted",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="productset",
            name="can_be_hard_deleted",
            field=models.BooleanField(default=True),
        ),
    ]
