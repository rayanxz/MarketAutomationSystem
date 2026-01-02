from django.urls import path
from financials import views_manager as views

app_name = "financials"

urlpatterns = [
    path("manager/containers/", views.container_list, name="container_list"),
    path("manager/containers/create/", views.container_create, name="container_create"),
    path("manager/containers/<int:container_id>/edit/", views.container_edit, name="container_edit"),
    path("manager/movements/", views.container_movements, name="container_movements"),
]
