# app/audit_log/urls.py
from django.urls import path
from . import views

app_name = "audit_log"

urlpatterns = [
    path("owner/", views.owner_audit_dashboard, name="owner_dashboard"),
]
