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

    # APIs
    path("api/providers/ac/", v.api_providers_ac, name="billing_api_providers_ac"),
    path("api/bill/save/", v.api_bill_save, name="billing_api_bill_save"),
]
