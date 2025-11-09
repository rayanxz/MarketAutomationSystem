from django.db import migrations

def up(apps, schema_editor):
    Account = apps.get_model("ledger", "Account")
    # CharField choices are stable strings: "ASSET", "LIABILITY", "EQUITY", "REVENUE", "EXPENSE"
    ACCS = [
        ("SAFE",                 "Manager Vault (Safe)",    "ASSET"),
        ("INVENTORY",            "Inventory",               "ASSET"),
        ("PROVIDER_PAYABLE",     "Provider Payable",        "LIABILITY"),
        ("PROVIDER_RECEIVABLE",  "Provider Receivable",     "ASSET"),
        ("SALES",                "Sales Revenue",           "REVENUE"),
        ("VAT_PAYABLE",          "VAT Payable",             "LIABILITY"),
        ("DISCOUNT_EXPENSE",     "Discount Expense",        "EXPENSE"),
        ("OPENING_FLOAT_EQUITY", "Opening Float Equity",    "EQUITY"),
        ("PETTY_CASH_EXPENSE",   "Petty Cash Expense",      "EXPENSE"),
        ("ROUNDING_GAIN_LOSS",   "Rounding Gain/Loss",      "EXPENSE"),
        ("BANK_MAIN",            "Main Bank",               "ASSET"),
    ]
    for code, name, typ in ACCS:
        obj, created = Account.objects.get_or_create(
            code=code,
            defaults={"name": name, "type": typ, "is_active": True},
        )
        if not created:
            # ensure active + correct metadata if it already existed
            dirty = False
            if obj.name != name:
                obj.name = name; dirty = True
            if getattr(obj, "type", None) != typ:
                obj.type = typ; dirty = True
            if not getattr(obj, "is_active", True):
                obj.is_active = True; dirty = True
            if dirty:
                obj.save(update_fields=["name", "type", "is_active"])

    # Ensure there is a default cash register that points at SAFE (harmless if already exists)
    CashRegister = apps.get_model("ledger", "CashRegister")
    try:
        safe = Account.objects.get(code="SAFE")
        CashRegister.objects.get_or_create(
            code="REG_MAIN",
            defaults={"name": "Main Drawer", "cash_account": safe},
        )
    except Account.DoesNotExist:
        pass  # if SAFE wasn’t created for some reason, don’t crash migration


def down(apps, schema_editor):
    # no-op: keep baseline accounts even if migrating backwards
    pass

class Migration(migrations.Migration):

    dependencies = [
        ("ledger", "0003_seed_register"),  # or last applied ledger migration
    ]

    operations = [
        migrations.RunPython(up, down),
    ]
