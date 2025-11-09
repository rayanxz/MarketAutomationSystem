# app/notifications/views.py
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse, HttpRequest
from django.contrib.auth.decorators import login_required
from accounts.models import AccountProfile
from .models import Notification


@login_required
def list_notifications(request: HttpRequest):
    """
    Return JSON list of notifications for the logged-in user's role.
    """
    acc_profile = getattr(request.user, "account_profile", None)
    raw_role = getattr(acc_profile, "role", None)

    # Normalize to lowercase string safely whether it's a TextChoices member or plain string
    if hasattr(raw_role, "value"):   # TextChoices (e.g., AccountProfile.Role.MANAGER)
        role_str = str(raw_role.value).lower()
    else:
        role_str = str(raw_role or "manager").lower()

    # Case-insensitive filter to be extra safe
    notifs = Notification.objects.filter(receiver_role__iexact=role_str).order_by("-created_at")[:30]

    def build_url(n: Notification) -> str:
        if n.related_app == "debts":
            if n.related_model == "DebtorDebt":
                return f"/manager/debts/view/debtor/{n.related_id}/"
            if n.related_model == "CreditorDebt":
                return f"/manager/debts/view/creditor/{n.related_id}/"
        return ""

    data = [
        {
            "id": n.id,
            "title": n.title,
            "message": n.message,
            "created": n.created_at.strftime("%Y-%m-%d %H:%M"),
            "is_read": n.is_read,
            "url": build_url(n),
        }
        for n in notifs
    ]
    return JsonResponse({"ok": True, "items": data})



@login_required
def mark_read(request: HttpRequest, notif_id: int):
    """
    Mark a specific notification as read.
    """
    notif = get_object_or_404(Notification, id=notif_id)
    notif.is_read = True
    notif.save(update_fields=["is_read"])
    return JsonResponse({"ok": True})


@login_required
def notifications_page(request: HttpRequest):
    """
    Optional: render a full HTML notifications list page (for later use).
    """
    return render(request, "notifications/notifications_list.html")
