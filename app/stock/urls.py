# app/stock/urls.py
from django.urls import path
from . import views

app_name = "stock"

urlpatterns = [
    path("", views.stock_list, name="stock_list"),
    path("move/", views.stock_move, name="stock_move"),

    # AJAX APIs for the move page
    path("api/product-search/", views.api_stock_product_search, name="api_product_search"),
    path("api/product-stock/", views.api_product_stock, name="api_product_stock"),
]
