from django.db import migrations

def seed_features(apps, schema_editor):
    ContainerFeature = apps.get_model("financials", "ContainerFeature")

    rows = [
        ("pos_sales", "مبيعات POS", 10),
        ("pos_returns", "مرتجعات POS", 20),
        ("purchase_bills", "فواتير الشراء", 30),
        ("provider_returns", "مرتجعات المورد", 40),
        ("debts_incoming", "تحصيل ديون", 50),
    ]

    for code, name, order in rows:
        ContainerFeature.objects.update_or_create(
            code=code,
            defaults={"name": name, "sort_order": order, "is_active": True},
        )

class Migration(migrations.Migration):
    dependencies = [
        ("financials", "0003_containerfeature_moneycontainer_allowed_users_and_more"),
    ]

    operations = [
        migrations.RunPython(seed_features, migrations.RunPython.noop),
    ]
