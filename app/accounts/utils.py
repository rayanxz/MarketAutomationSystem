from __future__ import annotations

from typing import Optional

from .models import AccountProfile


def profile_for(user) -> Optional[AccountProfile]:
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    try:
        return user.account_profile
    except AccountProfile.DoesNotExist:  # type: ignore[attr-defined]
        return None


def role_for(user) -> Optional[str]:
    profile = profile_for(user)
    if profile is None:
        return None
    return profile.role


def is_owner(user) -> bool:
    profile = profile_for(user)
    return bool(profile and profile.role == AccountProfile.Role.OWNER)


def owner_exists() -> bool:
    return AccountProfile.objects.filter(role=AccountProfile.Role.OWNER).exists()


def dashboard_name_for(user) -> str:
    profile = profile_for(user)
    if profile is None:
        return "login"
    if profile.role == AccountProfile.Role.OWNER:
        return "owner_dash"
    if profile.role == AccountProfile.Role.MANAGER:
        return "manager_dash"
    if profile.role == AccountProfile.Role.CASHIER:
        return "cashier_dash"
    return "login"
