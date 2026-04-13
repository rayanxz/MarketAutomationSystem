from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="PublicIdSequence",
            fields=[
                ("key", models.CharField(max_length=64, primary_key=True, serialize=False)),
                ("next_value", models.BigIntegerField(default=1)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "core_publicidsequence",
            },
        ),
    ]
