from django.urls import path
from . import views as v

urlpatterns = [
    # Pages
    path("", v.debts_page, name="debts_page"),                 # /manager/debts/
    path("creditors/", v.creditors_page, name="creditors_page"),
    path("add/", v.add_debt, name="debts_add"),

    path("view/<str:direction>/<int:entry_id>/", v.view_debt, name="debts_view_debt"),
    path("view/record/<str:debt_ref>/", v.view_central_debt, name="debts_view_central_debt"),

    # APIs (lists)
    path("api/debts/", v.api_debts_list, name="debts_api_debts_list"),
    path("api/creditors/", v.api_creditors_list, name="debts_api_creditors_list"),
    path("api/records/", v.api_central_debts_list, name="debts_api_central_list"),
    path("api/record/<str:debt_ref>/settle/", v.api_central_debt_settle, name="debts_api_central_settle"),
    path("api/other-party-suggest/", v.api_other_party_suggest, name="debts_api_other_party_suggest"),
    path("api/debts", v.api_debts_list),            # alias (no slash)
    path("api/creditors", v.api_creditors_list),    # alias (no slash)
    path("api/records", v.api_central_debts_list),  # alias (no slash)
    path("api/record/<str:debt_ref>/settle", v.api_central_debt_settle),  # alias (no slash)

    # APIs (manual debts create / pay / collect)
    path("api/manual/save/", v.api_manual_debt_save, name="debts_api_manual_debt_save"),
    path("manual/<int:entry_id>/pay-full/", v.api_manual_debt_pay_full, name="debts_manual_pay_full"),
    path("manual/<int:entry_id>/pay-batch/", v.api_manual_debt_pay_batch, name="debts_manual_pay_batch"),
    path("manual-creditors/<int:entry_id>/collect-full/", v.api_manual_creditor_collect_full, name="debts_manual_collect_full"),
    path("manual-creditors/<int:entry_id>/collect-batch/", v.api_manual_creditor_collect_batch, name="debts_manual_collect_batch"),

    # NEW: details + actions for any debt
    path("api/entry/<str:direction>/<int:entry_id>/", v.api_debt_details, name="debts_api_entry_details"),
    path("api/entry/<str:direction>/<int:entry_id>/pay-full/", v.api_entry_pay_full, name="debts_api_entry_pay_full"),
    path("api/entry/<str:direction>/<int:entry_id>/pay-batch/", v.api_entry_pay_batch, name="debts_api_entry_pay_batch"),
    path("api/entry/<str:direction>/<int:entry_id>/set-reminder/", v.api_entry_set_reminder, name="debts_api_entry_set_reminder"),
]
