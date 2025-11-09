# app/notifications/services.py
from __future__ import annotations
from django.db.models import OuterRef, Exists
from django.utils import timezone

from debts.models import DebtReminder, DebtorDebt, CreditorDebt
from .models import Notification

def _current_due_reminders_today():
    """
    Yield today's *current* reminders only (latest set_at per entry),
    for OPEN debts in both directions.
    """
    today = timezone.now().date()

    # ---- Debtor (store owes) ----
    newer_debtor = DebtReminder.objects.filter(
        direction="debtor",
        debtor=OuterRef("debtor"),
        set_at__gt=OuterRef("set_at"),
    )
    debtor_qs = (
        DebtReminder.objects
        .filter(direction="debtor", due_date=today, debtor__status=DebtorDebt.Status.OPEN)
        .annotate(is_latest=~Exists(newer_debtor))
        .filter(is_latest=True)
        .select_related("debtor", "debtor__provider")
    )

    # ---- Creditor (store is owed) ----
    newer_creditor = DebtReminder.objects.filter(
        direction="creditor",
        creditor=OuterRef("creditor"),
        set_at__gt=OuterRef("set_at"),
    )
    creditor_qs = (
        DebtReminder.objects
        .filter(direction="creditor", due_date=today, creditor__status=CreditorDebt.Status.OPEN)
        .annotate(is_latest=~Exists(newer_creditor))
        .filter(is_latest=True)
        .select_related("creditor", "creditor__provider")
    )

    return today, debtor_qs, creditor_qs


def create_due_reminder_notifications_for_manager():
    """
    Idempotent for the day: uses get_or_create keyed by (role, related_model+id, due_date).
    Creates Arabic titles/messages + linking metadata.
    """
    today, debtor_qs, creditor_qs = _current_due_reminders_today()

    # Debtor
    for r in debtor_qs:
        e = r.debtor
        other_type = (e.party_type or "provider").lower()
        other_name = e.party_name or (getattr(e.provider, "name", "") or "")
        # مثال: دين من النوع "مدين" يجب دفعه للمورد ريان اليوم
        title = f'دين من النوع "مدين" يجب دفعه لـ{_ar_party_type(other_type)} {other_name} اليوم'
        msg   = f"رقم السيريال: {e.doc_serial or '—'} | الإجمالي: {e.total} | المتبقي: {e.remaining}"
        Notification.objects.get_or_create(
            receiver_role="manager",
            notif_type=Notification.Type.SYSTEM,
            related_app="debts",
            related_model="DebtorDebt",
            related_id=str(e.id),
            due_date=today,
            defaults=dict(
                title=title,
                message=msg,
                trigger_date=today,
            ),
        )

    # Creditor
    for r in creditor_qs:
        e = r.creditor
        other_type = (e.party_type or "provider").lower()
        other_name = e.party_name or (getattr(e.provider, "name", "") or "")
        # مثال: دين من النوع "دائن" يجب تحصيله من المورد ريان اليوم
        title = f'دين من النوع "دائن" يجب تحصيله من { _ar_party_type(other_type) } {other_name} اليوم'
        msg   = f"رقم السيريال: {e.doc_serial or '—'} | الإجمالي: {e.total} | المتبقي: {e.remaining}"
        Notification.objects.get_or_create(
            receiver_role="manager",
            notif_type=Notification.Type.SYSTEM,
            related_app="debts",
            related_model="CreditorDebt",
            related_id=str(e.id),
            due_date=today,
            defaults=dict(
                title=title,
                message=msg,
                trigger_date=today,
            ),
        )


def _ar_party_type(t: str) -> str:
    x = (t or "").lower()
    if x == "customer": return "الزبون"
    if x == "worker":   return "العامل"
    return "المورد"
