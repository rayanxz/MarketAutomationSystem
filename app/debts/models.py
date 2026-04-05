# app/debts/models.py
from __future__ import annotations
from decimal import Decimal
from uuid import uuid4
from django.db import models

from core.currency import CURRENCY_CHOICES, SYP, USD
from django.utils import timezone

DEC0 = Decimal("0.000")

class PartyType(models.TextChoices):
    PROVIDER = "provider", "مزود"
    CUSTOMER = "customer", "زبون"
    WORKER   = "worker",   "عامل"

def _debt_public_id_default() -> str:
    return f"D-{uuid4().hex[:16].upper()}"


class DebtDirection(models.TextChoices):
    PAYABLE = "payable", "Payable"
    RECEIVABLE = "receivable", "Receivable"


class DebtCauseType(models.TextChoices):
    PURCHASE_BILL = "purchase_bill", "Purchase Bill"
    POS_BILL = "pos_bill", "POS Bill"
    PROVIDER_RETURN = "provider_return", "Provider Return"
    MANUAL = "manual", "Manual Debt"


class DebtStatus(models.TextChoices):
    OPEN = "open", "Open"
    CLOSED = "closed", "Closed"


class OtherPartyType(models.TextChoices):
    PROVIDER = "provider", "Provider"
    CUSTOMER = "customer", "Customer"
    SYSTEM_USER = "system_user", "System User"
    OTHER = "other", "Other"


class DebtRecord(models.Model):
    public_id = models.CharField(max_length=24, unique=True, default=_debt_public_id_default, editable=False)
    direction = models.CharField(max_length=12, choices=DebtDirection.choices, db_index=True)
    cause_type = models.CharField(max_length=32, choices=DebtCauseType.choices, db_index=True)
    cause_id = models.CharField(max_length=64, db_index=True)
    source_app = models.CharField(max_length=64, default="billing", blank=True)

    other_party_type = models.CharField(max_length=16, choices=OtherPartyType.choices, db_index=True)
    other_party_id = models.CharField(max_length=64, blank=True, default="")
    provider = models.ForeignKey("billing.Provider", on_delete=models.PROTECT, null=True, blank=True, related_name="central_debts")
    customer = models.ForeignKey("pos.CustomerProfile", on_delete=models.PROTECT, null=True, blank=True, related_name="central_debts")

    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    actor_username = models.CharField(max_length=150, blank=True, default="")

    total_syp = models.DecimalField(max_digits=14, decimal_places=3, default=Decimal("0.000"))
    total_usd = models.DecimalField(max_digits=14, decimal_places=3, default=Decimal("0.000"))
    remaining_syp = models.DecimalField(max_digits=14, decimal_places=3, default=Decimal("0.000"))
    remaining_usd = models.DecimalField(max_digits=14, decimal_places=3, default=Decimal("0.000"))
    status = models.CharField(max_length=8, choices=DebtStatus.choices, default=DebtStatus.OPEN, db_index=True)

    note = models.TextField(blank=True, default="")

    class Meta:
        db_table = "debts_debtrecord"
        constraints = [
            models.UniqueConstraint(
                fields=["direction", "cause_type", "cause_id"],
                name="uniq_debtrecord_direction_cause",
            ),
            models.CheckConstraint(check=models.Q(total_syp__gte=0), name="debtrecord_total_syp_ge0"),
            models.CheckConstraint(check=models.Q(total_usd__gte=0), name="debtrecord_total_usd_ge0"),
            models.CheckConstraint(check=models.Q(remaining_syp__gte=0), name="debtrecord_remaining_syp_ge0"),
            models.CheckConstraint(check=models.Q(remaining_usd__gte=0), name="debtrecord_remaining_usd_ge0"),
            models.CheckConstraint(check=models.Q(remaining_syp__lte=models.F("total_syp")), name="debtrecord_remaining_syp_le_total"),
            models.CheckConstraint(check=models.Q(remaining_usd__lte=models.F("total_usd")), name="debtrecord_remaining_usd_le_total"),
        ]
        indexes = [
            models.Index(fields=["cause_type", "cause_id"]),
            models.Index(fields=["provider", "status"]),
            models.Index(fields=["customer", "status"]),
            models.Index(fields=["created_at"]),
        ]

    @property
    def paid_syp(self) -> Decimal:
        return (self.total_syp or DEC0) - (self.remaining_syp or DEC0)

    @property
    def paid_usd(self) -> Decimal:
        return (self.total_usd or DEC0) - (self.remaining_usd or DEC0)

    def __str__(self) -> str:
        return f"{self.public_id} {self.direction} {self.cause_type}:{self.cause_id}"


class DebtSettlement(models.Model):
    debt = models.ForeignKey(DebtRecord, on_delete=models.CASCADE, related_name="settlements")
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    actor_username = models.CharField(max_length=150, blank=True, default="")

    payment_syp = models.DecimalField(max_digits=14, decimal_places=3, default=Decimal("0.000"))
    payment_usd = models.DecimalField(max_digits=14, decimal_places=3, default=Decimal("0.000"))
    applied_syp = models.DecimalField(max_digits=14, decimal_places=3, default=Decimal("0.000"))
    applied_usd = models.DecimalField(max_digits=14, decimal_places=3, default=Decimal("0.000"))
    fx_syp_per_usd_used = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)

    receipt = models.ForeignKey("financials.Receipt", null=True, blank=True, on_delete=models.PROTECT, related_name="debt_settlements")
    money_container = models.ForeignKey("financials.MoneyContainer", null=True, blank=True, on_delete=models.PROTECT, related_name="debt_settlements")

    note = models.TextField(blank=True, default="")

    class Meta:
        db_table = "debts_debtsettlement"
        constraints = [
            models.CheckConstraint(check=models.Q(payment_syp__gte=0), name="debtsettlement_payment_syp_ge0"),
            models.CheckConstraint(check=models.Q(payment_usd__gte=0), name="debtsettlement_payment_usd_ge0"),
            models.CheckConstraint(check=models.Q(applied_syp__gte=0), name="debtsettlement_applied_syp_ge0"),
            models.CheckConstraint(check=models.Q(applied_usd__gte=0), name="debtsettlement_applied_usd_ge0"),
            models.CheckConstraint(
                check=models.Q(payment_syp__gt=0) | models.Q(payment_usd__gt=0),
                name="debtsettlement_any_payment_positive",
            ),
            models.CheckConstraint(
                check=models.Q(applied_syp__gt=0) | models.Q(applied_usd__gt=0),
                name="debtsettlement_any_applied_positive",
            ),
        ]
        indexes = [
            models.Index(fields=["debt", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"DebtSettlement debt={self.debt_id} syp={self.payment_syp} usd={self.payment_usd}"

# ---------- Debtor (store owes) ----------
class DebtorDebt(models.Model):
    class Status(models.TextChoices):
        OPEN   = "open",   "مفتوحة"
        CLOSED = "closed", "مغلقة"

    # Keep FK by app label string to avoid import cycles
    provider     = models.ForeignKey("billing.Provider", on_delete=models.PROTECT, related_name="debtor_entries", null=True, blank=True)

    source_app   = models.CharField(max_length=64)
    source_model = models.CharField(max_length=64)   # "Bill" | "ManualDebt"
    source_id    = models.CharField(max_length=64)   # bill id as str | "manual:xxx"
    legacy_source_id = models.CharField(max_length=64, blank=True, default="")
    created_at   = models.DateTimeField(default=timezone.now)

    currency_code = models.CharField(max_length=3, choices=CURRENCY_CHOICES, default=SYP)

    total       = models.DecimalField(max_digits=14, decimal_places=3, default=Decimal("0.000"))
    paid_amount = models.DecimalField(max_digits=14, decimal_places=3, default=Decimal("0.000"))
    status      = models.CharField(max_length=8, choices=Status.choices, default=Status.OPEN)

    # manual/meta
    party_type  = models.CharField(max_length=16, choices=PartyType.choices, default=PartyType.PROVIDER)
    party_name  = models.CharField(max_length=128, blank=True)
    customer    = models.ForeignKey("pos.CustomerProfile", on_delete=models.PROTECT, null=True, blank=True, related_name="debtor_entries")
    doc_serial  = models.PositiveIntegerField(null=True, blank=True)  # uses Bill serial namespace
    due_date    = models.DateField(null=True, blank=True)

    class Meta:
        db_table = "billing_debtorentry"
        managed = False 
        constraints = [
            models.UniqueConstraint(
                fields=["source_app", "source_model", "source_id", "currency_code"],
                name="uniq_debtor_by_source_currency",
            ),
        ]
        

    @property
    def remaining(self) -> Decimal:
        return (self.total or DEC0) - (self.paid_amount or DEC0)

    def __str__(self) -> str:
        party = self.party_name or (getattr(self.provider, "name", None) or "—")
        return f"Debtor #{self.id} → {party} ({self.remaining} remaining)"


class DebtorPayment(models.Model):
    entry      = models.ForeignKey(DebtorDebt, on_delete=models.CASCADE, related_name="payments")
    created_at = models.DateTimeField(default=timezone.now)
    amount     = models.DecimalField(max_digits=14, decimal_places=3)
    journal_entry_id = models.IntegerField(null=True, blank=True)
    currency_code = models.CharField(max_length=3, choices=CURRENCY_CHOICES, default=SYP)
    receipt = models.ForeignKey("financials.Receipt", null=True, blank=True, on_delete=models.PROTECT, related_name="debtor_payments")
    money_container = models.ForeignKey("financials.MoneyContainer", null=True, blank=True, on_delete=models.PROTECT, related_name="debtor_payments")
    fx_syp_per_usd_used = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)

    class Meta:
        db_table = "billing_debtorpayment"
        managed = False

    def __str__(self) -> str:
        return f"DebtorPayment {self.amount} on entry {self.entry_id}"


# ---------- Creditor (store is owed) ----------
class CreditorDebt(models.Model):
    class Status(models.TextChoices):
        OPEN   = "open",   "مفتوحة"
        CLOSED = "closed", "مغلقة"

    provider     = models.ForeignKey("billing.Provider", on_delete=models.PROTECT, related_name="creditor_entries", null=True, blank=True)

    source_app   = models.CharField(max_length=64)
    source_model = models.CharField(max_length=64)   # "ProviderReturn" | "ManualDebt"
    source_id    = models.CharField(max_length=64)   # return id as str | "manual:xxx"
    legacy_source_id = models.CharField(max_length=64, blank=True, default="")
    created_at   = models.DateTimeField(default=timezone.now)

    currency_code = models.CharField(max_length=3, choices=CURRENCY_CHOICES, default=SYP)

    total     = models.DecimalField(max_digits=14, decimal_places=3, default=Decimal("0.000"))
    collected = models.DecimalField(max_digits=14, decimal_places=3, default=Decimal("0.000"))
    status    = models.CharField(max_length=8, choices=Status.choices, default=Status.OPEN)

    # manual/meta
    party_type  = models.CharField(max_length=16, choices=PartyType.choices, default=PartyType.PROVIDER)
    party_name  = models.CharField(max_length=128, blank=True)
    customer    = models.ForeignKey("pos.CustomerProfile", on_delete=models.PROTECT, null=True, blank=True, related_name="creditor_entries")
    doc_serial  = models.PositiveIntegerField(null=True, blank=True)  # uses ProviderReturn serial namespace
    due_date    = models.DateField(null=True, blank=True)

    class Meta:
        db_table = "billing_creditorentry"
        managed = False
        constraints = [
            models.UniqueConstraint(
                fields=["source_app", "source_model", "source_id", "currency_code"],
                name="uniq_creditor_by_source_currency",
            ),
        ]
    

    @property
    def remaining(self) -> Decimal:
        return (self.total or DEC0) - (self.collected or DEC0)

    def __str__(self) -> str:
        party = self.party_name or (getattr(self.provider, "name", None) or "—")
        return f"Creditor #{self.id} ← {party} ({self.remaining} remaining)"


class CreditorReceipt(models.Model):
    entry      = models.ForeignKey(CreditorDebt, on_delete=models.CASCADE, related_name="receipts")
    created_at = models.DateTimeField(default=timezone.now)
    amount     = models.DecimalField(max_digits=14, decimal_places=3)
    journal_entry_id = models.IntegerField(null=True, blank=True)
    currency_code = models.CharField(max_length=3, choices=CURRENCY_CHOICES, default=SYP)
    receipt = models.ForeignKey("financials.Receipt", null=True, blank=True, on_delete=models.PROTECT, related_name="creditor_receipts")
    money_container = models.ForeignKey("financials.MoneyContainer", null=True, blank=True, on_delete=models.PROTECT, related_name="creditor_receipts")
    fx_syp_per_usd_used = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)

    class Meta:
        db_table = "billing_creditorreceipt"
        managed = False

    def __str__(self) -> str:
        return f"CreditorReceipt {self.amount} on entry {self.entry_id}"


# ---------- Reminders (new table) ----------
class DebtReminder(models.Model):
    class Direction(models.TextChoices):
        DEBTOR   = "debtor",   "مدين"
        CREDITOR = "creditor", "دائن"

    direction  = models.CharField(max_length=8, choices=Direction.choices)
    debtor     = models.ForeignKey(DebtorDebt, null=True, blank=True, on_delete=models.CASCADE, related_name="reminders")
    creditor   = models.ForeignKey(CreditorDebt, null=True, blank=True, on_delete=models.CASCADE, related_name="reminders")
    set_at     = models.DateTimeField(default=timezone.now)   # when manager set/changed it
    due_date   = models.DateField()                           # the target date (current reminder snapshot)

    class Meta:
        db_table = "debts_debtreminder"
        indexes = [
            models.Index(fields=["direction"]),
            models.Index(fields=["due_date"]),
        ]
