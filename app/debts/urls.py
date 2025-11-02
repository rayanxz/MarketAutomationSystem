from django.urls import path
from . import views as v

urlpatterns = [
    # Pages
    path("", v.debts_page, name="debts_page"),                 # /manager/debts/
    path("creditors/", v.creditors_page, name="creditors_page"),
    path("add/", v.add_debt, name="debts_add"),

    # APIs (lists)
    path("api/debts/", v.api_debts_list, name="debts_api_debts_list"),
    path("api/creditors/", v.api_creditors_list, name="debts_api_creditors_list"),
    path("api/debts", v.api_debts_list),            # alias (no slash)
    path("api/creditors", v.api_creditors_list),    # alias (no slash)

    # APIs (manual debts create / pay / collect)
    path("api/manual/save/", v.api_manual_debt_save, name="debts_api_manual_debt_save"),
    path("manual/<int:entry_id>/pay-full/", v.api_manual_debt_pay_full, name="debts_manual_pay_full"),
    path("manual/<int:entry_id>/pay-batch/", v.api_manual_debt_pay_batch, name="debts_manual_pay_batch"),
    path("manual-creditors/<int:entry_id>/collect-full/", v.api_manual_creditor_collect_full, name="debts_manual_collect_full"),
    path("manual-creditors/<int:entry_id>/collect-batch/", v.api_manual_creditor_collect_batch, name="debts_manual_collect_batch"),
]
