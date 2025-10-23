# app/ledger/models.py
from __future__ import annotations
from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone
from .choices import AccountType, DC
from .fields import JSONTextField

User = settings.AUTH_USER_MODEL

class Account(models.Model):
    code = models.CharField(max_length=32, unique=True)   # e.g. CASH_REGISTER_1
    name = models.CharField(max_length=128)
    type = models.CharField(max_length=16, choices=AccountType.choices)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="children")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]

    def __str__(self): return f"{self.code} — {self.name}"

class CashRegister(models.Model):
    """Physical or logical drawers/registers (useful if you have >1 cashier)."""
    code = models.CharField(max_length=32, unique=True)  # e.g. REG1
    name = models.CharField(max_length=64)
    cash_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="registers")

    def __str__(self): return f"{self.code} — {self.name}"

class CashSession(models.Model):
    register = models.ForeignKey(CashRegister, on_delete=models.PROTECT, related_name="sessions")
    cashier = models.ForeignKey(User, on_delete=models.PROTECT, related_name="cash_sessions")
    opened_at = models.DateTimeField(default=timezone.now)
    opened_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="cash_sessions_opened")
    opening_float_minor = models.BigIntegerField(validators=[MinValueValidator(0)])

    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.PROTECT, related_name="cash_sessions_closed")
    counted_minor = models.BigIntegerField(null=True, blank=True, validators=[MinValueValidator(0)])
    expected_minor = models.BigIntegerField(null=True, blank=True)
    over_short_minor = models.BigIntegerField(null=True, blank=True)

    is_closed = models.BooleanField(default=False)

    class Meta:
        ordering = ["-opened_at"]
        constraints = [
            # Prevent overlapping sessions per register (enforced by service too)
            models.UniqueConstraint(
                fields=["register"], condition=models.Q(is_closed=False),
                name="uniq_open_session_per_register"
            )
        ]

    def __str__(self):
        status = "closed" if self.is_closed else "open"
        return f"{self.register.code} / {self.cashier} ({status})"

class JournalEntry(models.Model):
    posted_at = models.DateTimeField(default=timezone.now)
    actor = models.ForeignKey(User, on_delete=models.PROTECT, related_name="journal_entries")
    session = models.ForeignKey(CashSession, null=True, blank=True, on_delete=models.PROTECT, related_name="journal_entries")

    source_app = models.CharField(max_length=64, blank=True, default="")
    source_model = models.CharField(max_length=64, blank=True, default="")
    source_id = models.CharField(max_length=64, blank=True, default="")

    idempotency_key = models.CharField(max_length=128, unique=True, null=True, blank=True)
    memo = models.CharField(max_length=255, blank=True, default="")

    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.PROTECT, related_name="journal_entries_voided")
    void_reason = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        ordering = ["-posted_at"]

    @property
    def is_void(self): return self.voided_at is not None

class JournalLine(models.Model):
    entry = models.ForeignKey(JournalEntry, on_delete=models.CASCADE, related_name="lines")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="journal_lines")
    dc = models.CharField(max_length=1, choices=DC.choices)
    amount_minor = models.BigIntegerField(validators=[MinValueValidator(1)])
    extra = JSONTextField(default=dict, blank=True)  # tax, discount, etc.

    class Meta:
        indexes = [models.Index(fields=["account_id"]), models.Index(fields=["entry_id"])]

class CashDrawerEvent(models.Model):
    """For UI-friendly history: cash in/out, safe drop, bank deposit."""
    entry = models.OneToOneField(JournalEntry, on_delete=models.CASCADE, related_name="drawer_event")
    kind = models.CharField(max_length=24)  # "CASH_IN","CASH_OUT","SAFE_DROP","BANK_DEPOSIT"
    register = models.ForeignKey(CashRegister, null=True, blank=True, on_delete=models.PROTECT)
    amount_minor = models.BigIntegerField(validators=[MinValueValidator(1)])
    note = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

class CashCount(models.Model):
    session = models.ForeignKey(CashSession, on_delete=models.CASCADE, related_name="counts")
    snapshot_at = models.DateTimeField(default=timezone.now)
    denominations = JSONTextField(default=dict, blank=True)  # {"100": 2, "50": 3, ...}
    total_minor = models.BigIntegerField(validators=[MinValueValidator(0)])

class DailyBalance(models.Model):
    """Optional cache table; recomputable. Use for fast reports."""
    account = models.ForeignKey(Account, on_delete=models.CASCADE)
    day = models.DateField()
    debit_minor = models.BigIntegerField(default=0)
    credit_minor = models.BigIntegerField(default=0)

    class Meta:
        unique_together = [("account", "day")]
        indexes = [models.Index(fields=["day"]), models.Index(fields=["account", "day"])]
