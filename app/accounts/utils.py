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


def has_role(
    user,
    *roles: str,
    allow_owner: bool = True,
    allow_staff_fallback: bool = True,
) -> bool:
    """
    Authoritative authorization helper:
    - Primary: AccountProfile role.
    - Compatibility fallback: Django superuser/staff when profile is missing.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return False

    effective_roles = tuple(roles) if roles else (
        AccountProfile.Role.OWNER,
        AccountProfile.Role.MANAGER,
        AccountProfile.Role.CASHIER,
    )

    profile = profile_for(user)
    if profile is not None:
        return profile.has_any_role(effective_roles, allow_owner=allow_owner)

    if getattr(user, "is_superuser", False):
        return True

    if allow_staff_fallback and getattr(user, "is_staff", False):
        # Legacy compatibility path: staff is treated as managerial/operator access.
        if AccountProfile.Role.MANAGER in effective_roles or AccountProfile.Role.CASHIER in effective_roles:
            return True

    return False


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
