# app/pos/urls.py
from django.urls import path
from . import views
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

app_name = "pos"

urlpatterns = [
    path("", views.pos_screen, name="pos_screen"),

    # product lookup APIs
    path("api/barcode/<str:value>/", api_barcode_lookup, name="api_barcode_lookup"),
    path("api/search/name/", api_search_name, name="api_search_name"),
    path("api/lookup/code/<str:value>/", api_lookup_code, name="api_lookup_code"),
    path("api/lookup/id/<int:pk>/", api_lookup_id, name="api_lookup_id"),

    # bills APIs (POS only, not full billing app)
    path("api/bill/save/", api_bill_save, name="api_bill_save"),
    path("api/bills/today/", api_bills_today, name="api_bills_today"),
    path("api/bill/<int:bill_id>/", api_bill_detail, name="api_bill_detail"),

    # customers autocomplete
    path("api/customers/search/", api_customers_search, name="api_customers_search"),
    
    path("api/bill/<int:pk>/delete/", api_bill_delete, name="api_bill_delete"),
]
