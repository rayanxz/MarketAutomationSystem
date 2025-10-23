from django.db import migrations

def seed_accounts(apps, schema_editor):
    Account = apps.get_model("ledger", "Account")
    data = [
        ("CASH_REGISTER_1", "Cash Drawer 1", "ASSET"),
        ("SAFE", "Store Safe", "ASSET"),
        ("BANK_MAIN", "Main Bank Account", "ASSET"),
        ("CUSTOMER_RECEIVABLE", "Customer Receivables", "ASSET"),
        ("PROVIDER_PAYABLE", "Provider Payables", "LIABILITY"),
        ("VAT_PAYABLE", "VAT Payable", "LIABILITY"),
        ("SALES", "Sales Revenue", "REVENUE"),
        ("SALES_RETURN", "Sales Returns", "REVENUE"),
        ("DISCOUNT_EXPENSE", "Sales Discounts", "EXPENSE"),
        ("PETTY_CASH_EXPENSE", "Petty Cash", "EXPENSE"),
        ("ROUNDING_GAIN_LOSS", "Over/Short & Rounding", "EXPENSE"),
        ("OPENING_FLOAT_EQUITY", "Opening Float (Equity)", "EQUITY"),
        ("OWNER_EQUITY", "Owner Equity", "EQUITY"),
    ]
    Account.objects.bulk_create(
        [Account(code=c, name=n, type=t) for c, n, t in data],
        ignore_conflicts=True
    )

def unseed(apps, schema_editor):
    pass

class Migration(migrations.Migration):
    dependencies = [("ledger", "0001_initial")]
    operations = [migrations.RunPython(seed_accounts, unseed)]
