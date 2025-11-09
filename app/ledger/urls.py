# app/ledger/urls.py
from django.urls import path
from . import views

app_name = "ledger"

urlpatterns = [
    path("", views.volt_home, name="volt_home"),
    path("movements/", views.volt_movements_page, name="volt_movements_page"),
    path("api/movements/", views.api_volt_movements, name="api_volt_movements"),
    path("api/summary/", views.api_volt_summary, name="api_volt_summary"),
    path("api/party-ac/", views.api_volt_party_ac, name="api_volt_party_ac"),
]
