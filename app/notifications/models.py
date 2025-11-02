from __future__ import annotations
from django.db import models
from django.utils import timezone
from accounts.models import AccountProfile

class Notification(models.Model):
    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        MANAGER = "manager", "Manager"
        CASHIER = "cashier", "Cashier"

    class Type(models.TextChoices):
        SYSTEM = "system", "System"   # auto system events (ex: unpaid debt due)
        MANUAL = "manual", "Manual"   # user-to-user messages

    sender = models.ForeignKey(
        AccountProfile,
        null=True, blank=True,
        related_name="sent_notifications",
        on_delete=models.SET_NULL
    )
    receiver_role = models.CharField(max_length=20, choices=Role.choices)
    title = models.CharField(max_length=255)
    message = models.TextField(blank=True)
    notif_type = models.CharField(max_length=20, choices=Type.choices, default=Type.SYSTEM)
    created_at = models.DateTimeField(default=timezone.now)
    is_read = models.BooleanField(default=False)
    related_app = models.CharField(max_length=50, blank=True, help_text="e.g., billing")
    related_model = models.CharField(max_length=50, blank=True)
    related_id = models.CharField(max_length=50, blank=True)
    due_date = models.DateField(null=True, blank=True)  # used for reminders
    trigger_date = models.DateField(null=True, blank=True)  # system-generated trigger

    def __str__(self):
        return f"{self.receiver_role}: {self.title}"

    class Meta:
        ordering = ["-created_at"]
