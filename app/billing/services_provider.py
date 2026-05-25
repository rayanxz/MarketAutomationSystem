# app/billing/services_provider.py
from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from billing.models import Provider, ProviderPhone
from audit_log.services import (
    log_create_safe as log_create,
    log_update_safe as log_update,
    log_delete_safe as log_delete,
    snap_instance,
)


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
    if (phone or "").strip():
        ProviderPhone.objects.create(
            provider=provider,
            phone_number=(phone or "").strip(),
        )

    log_create(
        actor=actor,
        target=provider,
        title="Create provider",
        message=f"Provider created: {provider.name}",
        meta={
            "kind": "billing.provider_created",
            "summary": {
                "provider_id": provider.id,
                "name": provider.name,
                "phone": provider.phone or "",
                "is_active": bool(provider.is_active),
            },
        },
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
        meta={
            "kind": "billing.provider_deleted",
            "summary": {
                "provider_id": provider.id,
                "name": provider.name,
                "phone": provider.phone or "",
                "is_active": False,
            },
            "soft_delete": True,
        },
    )


    return provider


@transaction.atomic
def update_provider_notes(
    *,
    actor,
    provider: Provider,
    notes: str,
):
    before = snap_instance(provider, ["notes"])
    provider.notes = notes
    provider.save(update_fields=["notes"])

    log_update(
        actor=actor,
        target=provider,
        title="Update provider notes",
        message=f"Provider notes updated: {provider.name}",
        before=before,
        after=snap_instance(provider, ["notes"]),
        meta={
            "kind": "billing.provider_notes_updated",
            "summary": {
                "provider_id": provider.id,
                "name": provider.name,
            },
        },
    )

    return provider


def _sync_provider_latest_phone(*, provider: Provider) -> None:
    latest_phone = (
        ProviderPhone.objects
        .filter(provider=provider)
        .order_by("-created_at", "-id")
        .values_list("phone_number", flat=True)
        .first()
    ) or ""
    if (provider.phone or "") == latest_phone:
        return
    provider.phone = latest_phone
    provider.save(update_fields=["phone"])


@transaction.atomic
def add_provider_phone(
    *,
    actor,
    provider: Provider,
    phone_number: str,
) -> ProviderPhone:
    normalized_phone = (phone_number or "").strip()
    phone_row = ProviderPhone.objects.create(
        provider=provider,
        phone_number=normalized_phone,
    )
    _sync_provider_latest_phone(provider=provider)

    log_update(
        actor=actor,
        target=provider,
        title="Add provider phone",
        message=f"Provider phone added: {provider.name}",
        meta={
            "kind": "billing.provider_phone_added",
            "summary": {
                "provider_id": provider.id,
                "phone_number": normalized_phone,
            },
        },
    )
    return phone_row


@transaction.atomic
def edit_provider_phone(
    *,
    actor,
    provider: Provider,
    phone_row: ProviderPhone,
    phone_number: str,
) -> ProviderPhone:
    normalized_phone = (phone_number or "").strip()
    old_phone = phone_row.phone_number
    phone_row.phone_number = normalized_phone
    phone_row.save(update_fields=["phone_number", "updated_at"])
    _sync_provider_latest_phone(provider=provider)

    log_update(
        actor=actor,
        target=provider,
        title="Edit provider phone",
        message=f"Provider phone edited: {provider.name}",
        meta={
            "kind": "billing.provider_phone_edited",
            "summary": {
                "provider_id": provider.id,
                "old_phone_number": old_phone,
                "new_phone_number": normalized_phone,
            },
        },
    )
    return phone_row


@transaction.atomic
def delete_provider_phone(
    *,
    actor,
    provider: Provider,
    phone_row: ProviderPhone,
) -> None:
    deleted_phone = phone_row.phone_number
    phone_row.delete()
    _sync_provider_latest_phone(provider=provider)

    log_update(
        actor=actor,
        target=provider,
        title="Delete provider phone",
        message=f"Provider phone deleted: {provider.name}",
        meta={
            "kind": "billing.provider_phone_deleted",
            "summary": {
                "provider_id": provider.id,
                "deleted_phone_number": deleted_phone,
            },
        },
    )
