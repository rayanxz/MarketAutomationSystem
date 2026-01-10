from django.db import migrations


def seed_currencies(apps, schema_editor):
    Currency = apps.get_model("financials", "Currency")

    # SYP: usually 0 decimals
    Currency.objects.update_or_create(
        code="SYP",
        defaults={
            "name": "Syrian Pound",
            "decimals": 0,
            "is_active": True,
        },
    )

    # USD: usually 2 decimals
    Currency.objects.update_or_create(
        code="USD",
        defaults={
            "name": "US Dollar",
            "decimals": 2,
            "is_active": True,
        },
    )


class Migration(migrations.Migration):
    dependencies = [
        ("financials", "0007_alter_moneycontainer_ref_code"),
    ]

    operations = [
        migrations.RunPython(seed_currencies, migrations.RunPython.noop),
    ]
