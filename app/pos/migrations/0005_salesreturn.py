from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
from decimal import Decimal


class Migration(migrations.Migration):

    dependencies = [
        ("pos", "0004_salesbill_fx_rate_used_salesbill_money_container_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("catalog", "0001_initial"),
        ("stock", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="SalesReturn",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("serial", models.PositiveIntegerField(blank=True, db_index=True, null=True, unique=True)),
                ("status", models.CharField(choices=[("draft", "Draft"), ("posted", "Posted"), ("cancelled", "Cancelled")], db_index=True, default="draft", max_length=12)),
                ("total_syp", models.DecimalField(decimal_places=3, default=Decimal("0.000"), max_digits=14)),
                ("total_usd", models.DecimalField(decimal_places=3, default=Decimal("0.000"), max_digits=14)),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("posted_at", models.DateTimeField(blank=True, null=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="pos_sales_returns_created", to=settings.AUTH_USER_MODEL)),
                ("posted_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="pos_sales_returns_posted", to=settings.AUTH_USER_MODEL)),
                ("customer", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="returns", to="pos.customerprofile")),
                ("sale_bill", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="returns", to="pos.salesbill")),
                ("stock_container", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="pos_sales_returns", to="stock.productcontainer")),
            ],
            options={
                "ordering": ["-id"],
            },
        ),
        migrations.CreateModel(
            name="SalesReturnRow",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("uom_index", models.PositiveSmallIntegerField(default=1)),
                ("qty_returned", models.DecimalField(decimal_places=3, max_digits=14)),
                ("currency_code", models.CharField(choices=[("SYP", "SYP"), ("USD", "USD")], db_index=True, default="SYP", max_length=3)),
                ("unit_price_at_sale", models.DecimalField(decimal_places=3, default=Decimal("0.000"), max_digits=14)),
                ("line_total", models.DecimalField(decimal_places=3, default=Decimal("0.000"), max_digits=14)),
                ("reason", models.CharField(blank=True, max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("product", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="pos_return_rows", to="catalog.product")),
                ("ret", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="rows", to="pos.salesreturn")),
                ("sale_row", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="return_rows", to="pos.salesbillrow")),
            ],
        ),
        migrations.AddIndex(
            model_name="salesreturn",
            index=models.Index(fields=["sale_bill"], name="pos_salesr_sale_bi_f9ac5e_idx"),
        ),
        migrations.AddIndex(
            model_name="salesreturn",
            index=models.Index(fields=["status"], name="pos_salesr_status_8f0b6a_idx"),
        ),
        migrations.AddIndex(
            model_name="salesreturnrow",
            index=models.Index(fields=["ret"], name="pos_salesr_ret_id_4b9f4b_idx"),
        ),
        migrations.AddIndex(
            model_name="salesreturnrow",
            index=models.Index(fields=["sale_row"], name="pos_salesr_sale_ro_2b6a07_idx"),
        ),
        migrations.AddIndex(
            model_name="salesreturnrow",
            index=models.Index(fields=["product"], name="pos_salesr_product_3af7a3_idx"),
        ),
        migrations.AddConstraint(
            model_name="salesreturn",
            constraint=models.CheckConstraint(check=models.Q(total_syp__gte=0), name="pos_ret_total_syp_non_negative"),
        ),
        migrations.AddConstraint(
            model_name="salesreturn",
            constraint=models.CheckConstraint(check=models.Q(total_usd__gte=0), name="pos_ret_total_usd_non_negative"),
        ),
    ]
