# app/inventory/urls.py
from django.urls import path
from . import views

app_name = "inventory"

urlpatterns = [
    path("movements/", views.manager_product_movements, name="product_movements"),
    #path("api/products/search/", views.api_product_search, name="api_product_search"),
]
