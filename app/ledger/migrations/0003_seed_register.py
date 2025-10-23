# app/ledger/migrations/0003_seed_register.py
from django.db import migrations

def seed_register(apps, schema_editor):
    Account = apps.get_model("ledger", "Account")
    CashRegister = apps.get_model("ledger", "CashRegister")
    try:
        cash = Account.objects.get(code="CASH_REGISTER_1")
    except Account.DoesNotExist:
        return
    CashRegister.objects.get_or_create(
        code="REG1",
        defaults={"name": "Drawer 1", "cash_account": cash}
    )

class Migration(migrations.Migration):
    dependencies = [("ledger", "0002_seed_accounts")]
    operations = [migrations.RunPython(seed_register)]
