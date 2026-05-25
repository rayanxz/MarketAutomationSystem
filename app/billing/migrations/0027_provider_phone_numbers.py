from __future__ import annotations

from django.db import migrations, models


def _forward_backfill_provider_phones(apps, schema_editor):
    Provider = apps.get_model("billing", "Provider")
    ProviderPhone = apps.get_model("billing", "ProviderPhone")

    for provider in Provider.objects.all().iterator():
        raw_phone = str(getattr(provider, "phone", "") or "").strip()
        if not raw_phone:
            continue
        if ProviderPhone.objects.filter(provider_id=provider.id, phone_number=raw_phone).exists():
            continue
        ProviderPhone.objects.create(
            provider_id=provider.id,
            phone_number=raw_phone,
        )


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0026_provider_public_id_layer"),
    ]

    operations = [
        migrations.CreateModel(
            name="ProviderPhone",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("phone_number", models.CharField(db_index=True, max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("provider", models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="phone_numbers", to="billing.provider")),
            ],
            options={
                "ordering": ["-created_at", "-id"],
            },
        ),
        migrations.AddIndex(
            model_name="providerphone",
            index=models.Index(fields=["provider", "-created_at", "-id"], name="idx_provider_phone_latest"),
        ),
        migrations.AddConstraint(
            model_name="providerphone",
            constraint=models.UniqueConstraint(fields=("provider", "phone_number"), name="uq_provider_phone_unique_per_provider"),
        ),
        migrations.RunPython(_forward_backfill_provider_phones, migrations.RunPython.noop),
    ]
