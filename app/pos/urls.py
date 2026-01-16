# app/pos/urls.py
from django.urls import path
from . import views
from . import returns_views
from .views_api import (
    api_barcode_lookup,
    api_search_name,
    api_lookup_code,
    api_lookup_id,
)
from .api_bills import (
    api_bill_save,
    api_bills_today,
    api_bill_detail,
    api_customers_search,
    api_bill_delete,
)

from .api_shifts import (
    api_shift_start,
    api_shift_end,
)

from .api_sessions import api_login_end


app_name = "pos"

urlpatterns = [
    # cashier POS screen
    path("", views.pos_screen, name="pos_screen"),

    # MANAGER POS OVERVIEW
    path("manager/overview/", views.pos_manager_overview, name="pos_manager_overview"),
    path("manager/overview/timeline/", views.pos_manager_overview_timeline, name="pos_manager_overview_timeline"),

    path("manager/bill/<int:bill_id>/", views.pos_manager_bill_detail, name="pos_manager_bill_detail"),
    path("manager/bill/<int:bill_id>/return-wizard/", returns_views.pos_manager_sale_return_wizard, name="pos_manager_sale_return_wizard"),
    path("manager/returns/<int:return_id>/settle/", returns_views.pos_manager_sale_return_settle, name="pos_manager_sale_return_settle"),
    path("manager/returns/<int:return_id>/post/", returns_views.pos_manager_sale_return_post, name="pos_manager_sale_return_post"),
    path("manager/customers/", views.pos_manager_customers, name="pos_manager_customers"),
    path("manager/customers/debts/", views.pos_manager_customer_debts, name="pos_manager_customer_debts"),


    # product lookup APIs
    path("api/barcode/<str:value>/", api_barcode_lookup, name="api_barcode_lookup"),
    path("api/search/name/", api_search_name, name="api_search_name"),
    path("api/lookup/code/<str:value>/", api_lookup_code, name="api_lookup_code"),
    path("api/lookup/id/<int:pk>/", api_lookup_id, name="api_lookup_id"),

    # bills APIs (POS only, not full billing app)
    path("api/bill/save/", api_bill_save, name="api_bill_save"),
    path("api/bills/today/", api_bills_today, name="api_bills_today"),
    path("api/bill/<int:bill_id>/", api_bill_detail, name="api_bill_detail"),
    path("api/bill/<int:pk>/delete/", api_bill_delete, name="api_bill_delete"),

    # customers autocomplete
    path("api/customers/search/", api_customers_search, name="api_customers_search"),

    # shifts
    path("api/shift/start/", api_shift_start, name="api_shift_start"),
    path("api/shift/end/", api_shift_end, name="api_shift_end"),

    path("api/login/end/", api_login_end, name="api_login_end"),

]
