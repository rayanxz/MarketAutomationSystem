# app/billing/services_provider.py
from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from billing.models import Provider
from audit_log.services import log_create, log_delete, snap_instance


@transaction.atomic
def create_provider(
    *,
    actor,
    name: str,
    phone: str = "",
    notes: str = "",
):
    provider = Provider.objects.create(
        name=name,
        phone=phone,
        notes=notes,
        is_active=True,
    )

    log_create(
        actor=actor,
        target=provider,
        title="Create provider",
        message=f"Provider created: {provider.name}",
        after=snap_instance(provider, ["name", "phone", "notes", "is_active"]),
    )

    return provider


@transaction.atomic
def delete_provider(
    *,
    actor,
    provider: Provider,
):
    before = snap_instance(provider, ["name", "phone", "notes", "is_active"])

    provider.is_active = False
    provider.deleted_at = timezone.now()
    provider.save(update_fields=["is_active", "deleted_at"])

    log_delete(
        actor=actor,
        target=provider,
        title="Delete provider",
        message=f"Provider deleted: {provider.name}",
        before=before,
        meta={"soft_delete": True},
    )

    return provider
