# app/billing/urls.py
from django.urls import path
from billing import views as v

urlpatterns = [
    # Pages
    path("", v.billing_home, name="billing_home"),
    path("add/", v.add_bill, name="billing_add"),
    path("list/", v.bills_list, name="billing_list"),
    path("debts/", v.debts_list, name="billing_debts"),
    path("providers/", v.providers_list, name="billing_providers"),

    # APIs (providers)
    path("api/providers/ac/", v.api_providers_ac, name="billing_api_providers_ac"),

    # APIs (bills)
    path("api/bill/save/", v.api_bill_save, name="billing_api_bill_save"),
    path("api/bills", v.api_bills_list, name="billing_api_bills_list"),
    path("api/bills/<int:bill_id>/delete", v.api_bill_delete, name="billing_api_bill_delete"),

    # APIs (providers)
    path("api/providers/", v.api_providers_list, name="billing_api_providers_list"),
    path("api/providers/create", v.api_provider_create, name="billing_api_provider_create"),
    path("api/providers/<int:pid>/delete", v.api_provider_delete, name="billing_api_provider_delete"),

]
