from decimal import Decimal
from django.db import migrations

DEC0 = Decimal("0.000")

def forward(apps, schema_editor):
    Bill = apps.get_model("billing", "Bill")

    # Copy legacy total -> subtotal_syp for old bills that still have subtotal_syp = 0
    # (but only when total isn't zero, to avoid touching empty bills)
    for b in Bill.objects.exclude(total=DEC0).filter(subtotal_syp=DEC0).only("id", "total"):
        Bill.objects.filter(id=b.id).update(
            subtotal_syp=b.total or DEC0,
            subtotal_usd=DEC0,
        )

def backward(apps, schema_editor):
    # no-op (don’t try to undo money history)
    pass

class Migration(migrations.Migration):
    dependencies = [
        ("billing", "0004_bill_fx_usd_syp_bill_settlement_currency_and_more"),
    ]

    operations = [
        migrations.RunPython(forward, backward),
    ]
