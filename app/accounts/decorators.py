# accounts/decorators.py
from __future__ import annotations

from functools import wraps
from typing import Iterable

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponseForbidden
from django.shortcuts import redirect

from .models import AccountProfile
from .utils import profile_for


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
