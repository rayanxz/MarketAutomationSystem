from django.db import migrations

class Migration(migrations.Migration):
    dependencies = [
        ("billing", "0011_creditorentry_doc_serial_creditorentry_due_date_and_more"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.DeleteModel(name="DebtorPayment"),
                migrations.DeleteModel(name="DebtorEntry"),
                migrations.DeleteModel(name="CreditorReceipt"),
                migrations.DeleteModel(name="CreditorEntry"),
            ],
            database_operations=[
                # Intentionally empty → do NOT drop the tables.
            ],
        ),
    ]
