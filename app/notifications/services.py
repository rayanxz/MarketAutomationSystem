from django.utils import timezone
from billing.models import DebtorEntry
from .models import Notification

def check_due_debts():
    today = timezone.now().date()
    overdue = DebtorEntry.objects.filter(
        due_date=today,
        status=DebtorEntry.Status.OPEN
    )
    for d in overdue:
        Notification.objects.get_or_create(
            receiver_role="manager",
            related_app="billing",
            related_model="DebtorEntry",
            related_id=str(d.id),
            defaults={
                "title": f"تذكير بدين غير مسدد ({d.party_name})",
                "message": f"يجب تغطية الدين رقم {d.doc_serial} اليوم ({d.total} المجموع).",
            },
        )
