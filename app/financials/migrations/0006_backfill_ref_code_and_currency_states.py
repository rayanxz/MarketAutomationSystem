from django.db import migrations

def forwards(apps, schema_editor):
    MoneyContainer = apps.get_model("financials", "MoneyContainer")
    Currency = apps.get_model("financials", "Currency")
    MoneyContainerCurrency = apps.get_model("financials", "MoneyContainerCurrency")

    # Ensure we have SYP/USD rows (only if you want that guarantee)
    # If you already seed currencies elsewhere, this just reuses them.
    syp = Currency.objects.filter(code="SYP").first()
    usd = Currency.objects.filter(code="USD").first()

    for c in MoneyContainer.objects.all().order_by("id"):
        # 1) ref_code unique per container
        if not c.ref_code or c.ref_code == "MIG-TEMP":
            c.ref_code = f"MC-{c.id:06d}"
            c.save(update_fields=["ref_code"])

        # 2) currency states (container always has both, enabled/disabled later)
        # If currency row missing, skip it safely.
        if syp:
            MoneyContainerCurrency.objects.get_or_create(
                container=c, currency=syp,
                defaults={"is_enabled": True},
            )
        if usd:
            MoneyContainerCurrency.objects.get_or_create(
                container=c, currency=usd,
                defaults={"is_enabled": True},
            )

def backwards(apps, schema_editor):
    # keep it no-op, we don't want to delete data on rollback
    pass

class Migration(migrations.Migration):
    dependencies = [
        ("financials", "0005_remove_moneycontainer_currencies_and_more"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
