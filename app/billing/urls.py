# app/billing/urls.py
from django.urls import path
from billing import views as v
from . import views as V

urlpatterns = [
    path("", v.billing_home, name="billing_home"),
    path("add/", v.add_bill, name="billing_add"),
    path("list/", v.bills_list, name="billing_list"),
    path("providers/", v.providers_list, name="billing_providers"),


    path("debts/", v.debts_page, name="billing_debts"),           # قائمة المدين (store owes providers)
    path("creditors/", v.creditors_page, name="billing_creditors"),# قائمة الدائن (providers owe store)

    path("add-debt/", v.add_debt, name="billing_add_debt"),


    # APIs (debts/creditors)
    path("api/debts/", v.api_debts_list, name="billing_api_debts_list"),
    path("api/creditors/", v.api_creditors_list, name="billing_api_creditors_list"),

    # aliases (no trailing slash) to avoid front-end 301/redirect issues
    path("api/debts", v.api_debts_list),
    path("api/creditors", v.api_creditors_list),

    path("api/debt/save/", v.api_debt_save, name="billing_api_debt_save"),


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

        # Manual debts (pure, no products)
    path("api/manual-debts/save/", v.api_manual_debt_save, name="billing_api_manual_debt_save"),


    # Payments (payables)
    path("bills/<int:bill_id>/pay-full/", v.pay_debt_full, name="pay_debt_full"),
    path("bills/<int:bill_id>/pay-batch/", v.pay_debt_batch, name="pay_debt_batch"),
    path("bills/<int:bill_id>/", v.bill_view, name="billing_bill_view"),

    path("manual-debts/<int:entry_id>/pay-full/", v.api_manual_debt_pay_full, name="manual_debt_pay_full"),
    path("manual-debts/<int:entry_id>/pay-batch/", v.api_manual_debt_pay_batch, name="manual_debt_pay_batch"),
    path("manual-creditors/<int:entry_id>/collect-full/", v.api_manual_creditor_collect_full, name="manual_creditor_collect_full"),
    path("manual-creditors/<int:entry_id>/collect-batch/",v.api_manual_creditor_collect_batch,name="manual_creditor_collect_batch"),

    # Provider returns (receivables)
    path("returns/", V.providers_returns_page, name="billing_returns"),
    path("returns/list/", V.providers_returns_list_page, name="billing_returns_list"),
    path("api/returns/save/", V.api_return_save, name="billing_api_return_save"),
    path("api/returns/list/", V.api_returns_list, name="billing_api_returns_list"),
    path("returns/<int:ret_id>/", V.return_view, name="billing_return_view"),
    path("api/returns/next-serial/", V.api_return_next_serial, name="billing_api_return_next_serial"),
    path("returns/<int:ret_id>/collect-full/", V.collect_return_full, name="billing_collect_full"),
    path("returns/<int:ret_id>/collect-batch/", V.collect_return_batch, name="billing_collect_batch"),
]
