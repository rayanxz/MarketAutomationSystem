# app/billing/urls.py
from django.urls import path
from billing import views as v

urlpatterns = [
    path("", v.billing_home, name="billing_home"),
    path("add/", v.add_bill, name="billing_add"),
    path("list/", v.bills_list, name="billing_list"),
    path("providers/", v.providers_list, name="billing_providers"),
    path("debts/", v.debts_page, name="billing_debts"),

    # APIs (debts)
    path("api/debts", v.api_debts_list, name="billing_api_debts_list"),

    # APIs (bills)
    path("api/bill/save/", v.api_bill_save, name="billing_api_bill_save"),
    path("api/bill/next-serial/", v.api_bill_next_serial, name="billing_api_bill_next_serial"),
    path("api/bills", v.api_bills_list, name="billing_api_bills_list"),
    path("api/products/search/", v.api_products_search, name="billing_api_products_search"),
    path("api/bills/<int:bill_id>/delete", v.api_bill_delete, name="billing_api_bill_delete"),

    # APIs (providers)
    path("api/providers/ac/", v.api_providers_ac, name="billing_api_providers_ac"),
    path("api/providers/", v.api_providers_list, name="billing_api_providers_list"),
    path("api/providers/create", v.api_provider_create, name="billing_api_provider_create"),
    path("api/providers/<int:pid>/delete", v.api_provider_delete, name="billing_api_provider_delete"),

    # Payments
    path("bills/<int:bill_id>/pay-full/", v.pay_debt_full, name="pay_debt_full"),
    path("bills/<int:bill_id>/pay-batch/", v.pay_debt_batch, name="pay_debt_batch"),
]
