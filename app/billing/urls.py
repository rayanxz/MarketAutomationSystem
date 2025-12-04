# app/billing/urls.py
from django.urls import path
from billing import views as v
from . import views as V

urlpatterns = [
    path("", v.billing_home, name="billing_home"),
    path("add/", v.add_bill, name="billing_add"),
    path("list/", v.bills_list, name="billing_list"),
    path("providers/", v.providers_list, name="billing_providers"),

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

    # Payments (payables)
    path("bills/<int:bill_id>/pay-full/", v.pay_debt_full, name="pay_debt_full"),
    path("bills/<int:bill_id>/pay-batch/", v.pay_debt_batch, name="pay_debt_batch"),
    path("bills/<int:bill_id>/", v.bill_view, name="billing_bill_view"),

    # Provider returns (receivables)
    path("returns/list/", V.providers_returns_list_page, name="billing_returns_list"),
    path("api/returns/list/", V.api_returns_list, name="billing_api_returns_list"),
    path("returns/<int:ret_id>/", V.return_view, name="billing_return_view"),
    path("returns/<int:ret_id>/collect-full/", V.collect_return_full, name="billing_collect_full"),
    path("returns/<int:ret_id>/collect-batch/", V.collect_return_batch, name="billing_collect_batch"),

    path("bills/<int:bill_id>/", v.bill_view, name="billing_bill_view"),
    path("bills/<int:bill_id>/returns-wizard/",v.bill_return_wizard,name="billing_bill_return_wizard"),

]
