# app/ledger/migrations/0005_seed_misc_accounts.py
from django.db import migrations

def up(apps, schema_editor):
    Account = apps.get_model("ledger", "Account")
    # Use your real type strings (likely: "REVENUE" or "INCOME", and "EXPENSE")
    Account.objects.get_or_create(
        code="MISC_GAIN",
        defaults={"name": "Miscellaneous Gain", "type": "REVENUE", "is_active": True},
    )
    Account.objects.get_or_create(
        code="MISC_EXPENSE",
        defaults={"name": "Miscellaneous Expense", "type": "EXPENSE", "is_active": True},
    )

def down(apps, schema_editor):
    Account = apps.get_model("ledger", "Account")
    Account.objects.filter(code__in=["MISC_GAIN", "MISC_EXPENSE"]).delete()

class Migration(migrations.Migration):

    dependencies = [
        ("ledger", "0004_seed_baseline_fix"),
    ]

    operations = [
        migrations.RunPython(up, down),
    ]
