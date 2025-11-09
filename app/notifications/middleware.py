# app/notifications/middleware.py
from __future__ import annotations
from django.utils import timezone
from accounts.models import AccountProfile
from .services import create_due_reminder_notifications_for_manager

class NotificationsOnLoginMiddleware:
    """
    On a manager's first request each day, generate due reminder notifications.
    Cheap guard via session key to avoid re-running all day.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            user = getattr(request, "user", None)
            if user and user.is_authenticated:
                role = getattr(getattr(user, "account_profile", None), "role", None)
                if role == AccountProfile.Role.MANAGER:
                    today_str = timezone.now().date().isoformat()
                    key = "notif_due_reminders_last_run"
                    if request.session.get(key) != today_str:
                        create_due_reminder_notifications_for_manager()
                        request.session[key] = today_str
        except Exception:
            # Never block request flow if notifications fail.
            pass
        return self.get_response(request)
