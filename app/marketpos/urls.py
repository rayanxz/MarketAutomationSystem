from django.contrib import admin
from django.urls import path , include
from django.shortcuts import redirect

from accounts import views as acc_views  # all auth + dashboards live here

urlpatterns = [
    # Home → login
    path("", lambda r: redirect("login"), name="home"),

    # Django admin (optional)
    path("admin/", admin.site.urls),

    # First-time owner setup
    path("setup/owner/", acc_views.owner_setup_view, name="owner_setup"),

    # Auth
    path("login/", acc_views.login_view, name="login"),
    path("logout/", acc_views.logout_view, name="logout"),

    # Dashboards
    path("owner/", acc_views.owner_dashboard, name="owner_dash"),
    path("manager/", acc_views.manager_dashboard, name="manager_dash"),
    path("cashier/", acc_views.cashier_dashboard, name="cashier_dash"),

    # Accounts management (point to the NEW view, keep old name)
    # URL stays /accounts/ and name stays 'accounts' to match your current links
    path("accounts/", acc_views.manage_accounts, name="accounts"),
    path("accounts/list/", acc_views.accounts_list, name="accounts_list"),  # NEW

    path("owner/edit/", acc_views.owner_edit_account, name="owner_edit"),
    path("accounts/staff/<int:user_id>/edit/", acc_views.staff_edit, name="staff_edit"),
    path("accounts/staff/<int:user_id>/delete/", acc_views.staff_delete, name="staff_delete"),
    path("", include("catalog.urls")),


]
