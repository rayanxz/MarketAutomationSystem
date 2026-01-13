from __future__ import annotations

from decimal import Decimal

from django.db import migrations


def forwards(apps, schema_editor):
    Product = apps.get_model("catalog", "Product")

    for p in Product.objects.all():
        enable_syp = bool(getattr(p, "enable_syp", True))
        enable_usd = bool(getattr(p, "enable_usd", False))

        # Align new allow flags with legacy enable flags only if untouched (defaults)
        if p.allow_syp_purchasing is True and p.allow_usd_purchasing is False:
            p.allow_syp_purchasing = enable_syp
            p.allow_usd_purchasing = enable_usd
        if p.allow_syp_sales is True and p.allow_usd_sales is False:
            p.allow_syp_sales = enable_syp
            p.allow_usd_sales = enable_usd

        # Default currencies (carry legacy value if valid)
        if not p.default_purchase_currency and p.default_currency in ("SYP", "USD"):
            p.default_purchase_currency = p.default_currency
        if not p.default_sale_currency and p.default_currency in ("SYP", "USD"):
            p.default_sale_currency = p.default_currency
        else:
            if enable_syp and not enable_usd:
                if not p.default_purchase_currency:
                    p.default_purchase_currency = "SYP"
                if not p.default_sale_currency:
                    p.default_sale_currency = "SYP"
            elif enable_usd and not enable_syp:
                if not p.default_purchase_currency:
                    p.default_purchase_currency = "USD"
                if not p.default_sale_currency:
                    p.default_sale_currency = "USD"

        # Default costs/prices: prefer legacy per-currency, fallback to legacy single
        cost_single = p.cost or Decimal("0")
        price_single = p.price or Decimal("0")

        if not (p.default_cost_syp and p.default_cost_syp > 0):
            p.default_cost_syp = p.cost_syp if p.cost_syp is not None else cost_single
        if not (p.default_cost_usd and p.default_cost_usd > 0):
            p.default_cost_usd = p.cost_usd if p.cost_usd is not None else Decimal("0")
        if not (p.default_price_syp and p.default_price_syp > 0):
            p.default_price_syp = p.price_syp if p.price_syp is not None else price_single
        if not (p.default_price_usd and p.default_price_usd > 0):
            p.default_price_usd = p.price_usd if p.price_usd is not None else Decimal("0")

        # Seed latest values from defaults
        if not (p.latest_cost_syp and p.latest_cost_syp > 0):
            p.latest_cost_syp = p.default_cost_syp
        if not (p.latest_cost_usd and p.latest_cost_usd > 0):
            p.latest_cost_usd = p.default_cost_usd
        if not (p.latest_price_syp and p.latest_price_syp > 0):
            p.latest_price_syp = p.default_price_syp
        if not (p.latest_price_usd and p.latest_price_usd > 0):
            p.latest_price_usd = p.default_price_usd

        p.save(
            update_fields=[
                "allow_syp_purchasing",
                "allow_syp_sales",
                "allow_usd_purchasing",
                "allow_usd_sales",
                "default_purchase_currency",
                "default_sale_currency",
                "default_cost_syp",
                "default_cost_usd",
                "default_price_syp",
                "default_price_usd",
                "latest_cost_syp",
                "latest_cost_usd",
                "latest_price_syp",
                "latest_price_usd",
            ]
        )


def backwards(apps, schema_editor):
    # No-op: keep data as-is on rollback.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0011_product_allow_syp_purchasing_product_allow_syp_sales_and_more"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
