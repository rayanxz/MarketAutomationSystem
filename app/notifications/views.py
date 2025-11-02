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
    # Safely get the user's role
    acc_profile = getattr(request.user, "account_profile", None)
    role = getattr(acc_profile, "role", AccountProfile.Role.MANAGER)

    # Fetch recent notifications for that role
    notifs = Notification.objects.filter(receiver_role=role).order_by("-created_at")[:30]

    data = [
        {
            "id": n.id,
            "title": n.title,
            "message": n.message,
            "created": n.created_at.strftime("%Y-%m-%d %H:%M"),
            "is_read": n.is_read,
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
