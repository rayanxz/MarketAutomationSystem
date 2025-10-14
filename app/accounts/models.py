# accounts/models.py
from typing import Iterable
from django.conf import settings
from django.db import models


class AccountProfile(models.Model):
    class Role(models.TextChoices):
        OWNER = "OWNER", "مالك"
        MANAGER = "MANAGER", "مدير"
        CASHIER = "CASHIER", "كاشير"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="account_profile",
    )
    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.CASHIER,         # sensible default for new non-owner accounts
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Account profile"
        verbose_name_plural = "Account profiles"

    def __str__(self) -> str:
        return f"{self.user.username} ({self.role})"

    @property
    def is_owner(self) -> bool:
        return self.role == self.Role.OWNER

    def has_any_role(self, roles: Iterable[str], allow_owner: bool = True) -> bool:
        if allow_owner and self.is_owner:
            return True
        return self.role in roles
