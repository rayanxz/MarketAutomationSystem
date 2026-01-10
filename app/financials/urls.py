from django.urls import path
from financials import views_manager as views
from financials import views_api

app_name = "financials"

urlpatterns = [
    path("manager/containers/", views.container_list, name="container_list"),
    path("manager/containers/create/", views.container_create, name="container_create"),
    path("api/ref-preview/", views_api.ref_code_preview, name="ref_code_preview"),
    path("manager/containers/<int:container_id>/edit/", views.container_edit, name="container_edit"),
    path("manager/movements/", views.container_movements, name="container_movements"),
    path("manager/fx/", views.fx_settings, name="fx_settings"),

]
