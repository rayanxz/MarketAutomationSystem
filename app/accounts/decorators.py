# accounts/decorators.py
from __future__ import annotations

from functools import wraps
from typing import Iterable

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponseForbidden, JsonResponse

from .models import AccountProfile
from .utils import has_role


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
            if has_role(request.user, *roles, allow_owner=allow_owner):
                return view_func(request, *args, **kwargs)
            return HttpResponseForbidden("ليس لديك صلاحية للوصول إلى هذه الصفحة.")

        return wrapped

    return decorator


def role_required_api(*roles: Iterable[str], allow_owner: bool = True):
    """API variant of role_required that returns JSON 403 on authorization failure."""
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
            if has_role(request.user, *roles, allow_owner=allow_owner):
                return view_func(request, *args, **kwargs)
            return JsonResponse({"ok": False, "error": "FORBIDDEN"}, status=403)

        return wrapped

    return decorator
