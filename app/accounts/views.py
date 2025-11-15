# accounts/views.py
from __future__ import annotations

from functools import wraps
from typing import Iterable

from django.contrib import messages
from django.contrib.auth import get_user_model, login, logout
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST, require_http_methods

from .forms import (
    LoginForm,
    OwnerSetupForm,
    MainAccountUpdateForm,
    NewStaffAccountForm,
    StaffQuickEditForm,
    StaffDeleteForm,
)
from .models import AccountProfile
from .utils import dashboard_name_for, owner_exists, profile_for

User = get_user_model()

# ------------------------- Helpers -------------------------
def _normalize_role(value: str | None) -> str | None:
    """Normalize posted role values (accept Arabic/English variations)."""
    if not value:
        return None
    v = value.strip().upper()
    mapping = {
        "OWNER": "OWNER", "مالك": "OWNER", "مالِك": "OWNER",
        "MANAGER": "MANAGER", "مدير": "MANAGER",
        "CASHIER": "CASHIER", "كاشير": "CASHIER", "أَمِين الصندوق": "CASHIER", "أمين الصندوق": "CASHIER",
    }
    return mapping.get(v)


def role_required(*roles: Iterable[str], allow_owner: bool = True):
    """Decorator to restrict a view to certain roles (owner allowed by default)."""
    if not roles:
        roles = (
            AccountProfile.Role.OWNER,
            AccountProfile.Role.MANAGER,
            AccountProfile.Role.CASHIER,
        )
    else:
        roles = tuple(roles)

    def decorator(view_func):
        @wraps(view_func)
        @login_required(login_url="login")
        def wrapped(request: HttpRequest, *args, **kwargs):
            profile = profile_for(request.user)
            if profile is None:
                return redirect("login")
            if profile.has_any_role(roles, allow_owner=allow_owner):
                return view_func(request, *args, **kwargs)
            return HttpResponseForbidden("ليست لديك صلاحية للوصول إلى هذه الصفحة.")
        return wrapped
    return decorator


# ------------------------- First-time owner setup -------------------------
def owner_setup_view(request: HttpRequest) -> HttpResponse:
    if owner_exists():
        return redirect("login")

    form = OwnerSetupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "تم إنشاء حساب المالك. الرجاء تسجيل الدخول.")
        return redirect("login")

    return render(request, "accounts/owner.html", {"form": form})


# ------------------------- Login / Logout -------------------------
def login_view(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect(dashboard_name_for(request.user))

    if not owner_exists():
        return redirect("owner_setup")

    selected_role_raw = request.POST.get("role")
    selected_role = _normalize_role(selected_role_raw) or AccountProfile.Role.CASHIER

    login_form = LoginForm(request=request, data=request.POST or None)

    if request.method == "POST" and login_form.is_valid():
        user = login_form.get_user()
        login(request, user)
        request.session.set_expiry(0)  # expire on browser close

        profile = profile_for(user)
        role_to_url = {
            AccountProfile.Role.OWNER: "owner_dash",
            AccountProfile.Role.MANAGER: "manager_dash",
            AccountProfile.Role.CASHIER: "pos:pos_screen",
        }

        if not profile:
            messages.error(request, "لا توجد صلاحيات مرتبطة بهذا الحساب.")
            logout(request)
            return redirect("login")

        if selected_role not in role_to_url:
            selected_role = profile.role

        # role switching rules
        if profile.is_owner:
            allowed = {AccountProfile.Role.OWNER, AccountProfile.Role.MANAGER, AccountProfile.Role.CASHIER}
        elif profile.role == AccountProfile.Role.MANAGER:
            allowed = {AccountProfile.Role.MANAGER, AccountProfile.Role.CASHIER}
        else:
            allowed = {AccountProfile.Role.CASHIER}

        if selected_role not in allowed:
            messages.error(request, "لا يمكنك اختيار هذه الواجهة لهذا الحساب.")
            logout(request)
            return redirect("login")

        messages.success(request, "تم تسجيل الدخول بنجاح.")
        return redirect(role_to_url[selected_role])

    return render(request, "login.html", {"login_form": login_form, "selected_role": selected_role})


@require_POST
@login_required(login_url="login")
def logout_view(request: HttpRequest) -> HttpResponse:
    logout(request)
    messages.success(request, "تم تسجيل الخروج.")
    return redirect("login")


# ------------------------- Dashboards -------------------------
@role_required(AccountProfile.Role.OWNER)
def owner_dashboard(request: HttpRequest) -> HttpResponse:
    return render(request, "owner/owner_dash.html")


@role_required(AccountProfile.Role.MANAGER)
def manager_dashboard(request: HttpRequest) -> HttpResponse:
    return render(request, "manager/manager_dash.html")


# You allowed MANAGER to access cashier UI as well; keep as you wrote:
@role_required(AccountProfile.Role.CASHIER, AccountProfile.Role.MANAGER)
def cashier_dashboard(request: HttpRequest) -> HttpResponse:
    return render(request, "cashier/cashier_dash.html")


# ------------------------- Manage Accounts (create staff) -------------------------
@role_required(AccountProfile.Role.OWNER)
def manage_accounts(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        create_form = NewStaffAccountForm(request.POST)
        if create_form.is_valid():
            user = create_form.save()
            messages.success(request, f"تم إنشاء الحساب: {user.username}")
            return redirect("accounts")
        messages.error(request, "تحقق من الحقول وحاول مرة أخرى.")
    else:
        create_form = NewStaffAccountForm()

    return render(request, "accounts/manage_accounts.html", {"create_form": create_form})


# ------------------------- Owner edit own account -------------------------
@role_required(AccountProfile.Role.OWNER)
def owner_edit_account(request: HttpRequest) -> HttpResponse:
    form = MainAccountUpdateForm(request.user, data=request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "تم تحديث بيانات حساب المالك. الرجاء تسجيل الدخول مرة أخرى.")
        logout(request)
        return redirect("login")
    return render(request, "owner/edit_account.html", {"form": form})


# ------------------------- Staff list + (fallback) POST handler -------------------------
@role_required(AccountProfile.Role.OWNER)
def accounts_list(request: HttpRequest) -> HttpResponse:
    """
    GET  -> render lists.
    POST -> OPTIONAL fallback: handle edit/delete when forms post here with 'action'.
    """
    if request.method == "POST":
        action = request.POST.get("action", "").strip().lower()
        if action == "edit":
            form = StaffQuickEditForm(request.POST)
            if form.is_valid():
                user = form.save()
                messages.success(request, f"تم تحديث الحساب: {user.username}")
                return redirect("accounts_list")
            messages.error(request, "تعذر التحديث. تحقق من المدخلات.")
        elif action == "delete":
            form = StaffDeleteForm(request.POST)
            if form.is_valid():
                target = form.cleaned_data["_target_user"]
                username = target.username
                form.delete()
                messages.success(request, f"تم حذف الحساب: {username}")
                return redirect("accounts_list")
            messages.error(request, "تعذر الحذف. تحقق من المدخلات.")
        # fall through to re-render with messages

    qs = AccountProfile.objects.select_related("user").order_by("user__username")
    cashiers = [p for p in qs if p.role == AccountProfile.Role.CASHIER]
    managers = [p for p in qs if p.role == AccountProfile.Role.MANAGER]
    owner = next((p for p in qs if p.role == AccountProfile.Role.OWNER), None)

    return render(request, "owner/accounts_list.html", {
        "cashiers": cashiers,
        "managers": managers,
        "owner_profile": owner,
    })


# ------------------------- Staff edit/delete dedicated endpoints -------------------------
@role_required(AccountProfile.Role.OWNER)
@require_http_methods(["POST"])
def staff_edit(request: HttpRequest, user_id: int) -> HttpResponse:
    """
    Dedicated endpoint if your modal posts to /accounts/staff/<id>/edit.
    """
    form = StaffQuickEditForm({**request.POST, "user_id": user_id})
    if form.is_valid():
        user = form.save()
        messages.success(request, f"تم تحديث الحساب: {user.username}")
        return redirect("accounts_list")
    for e in form.errors.get("__all__", []):
        messages.error(request, e)
    return redirect("accounts_list")


@role_required(AccountProfile.Role.OWNER)
@require_http_methods(["POST"])
def staff_delete(request: HttpRequest, user_id: int) -> HttpResponse:
    """
    Dedicated endpoint if your modal posts to /accounts/staff/<id>/delete.
    """
    form = StaffDeleteForm({**request.POST, "user_id": user_id})
    if form.is_valid():
        target = form.cleaned_data["_target_user"]
        username = target.username
        form.delete()
        messages.success(request, f"تم حذف الحساب: {username}")
        return redirect("accounts_list")
    for e in form.errors.get("__all__", []):
        messages.error(request, e)
    return redirect("accounts_list")


# ------------------------- Wizard entry (legacy) -------------------------
def first_run_wizard(request: HttpRequest) -> HttpResponse:
    return redirect("owner_setup")
