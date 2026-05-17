# app/debts/models.py
from __future__ import annotations
import re
from decimal import Decimal
from uuid import uuid4
from django.conf import settings
from django.db import IntegrityError, models, transaction

from core.currency import CURRENCY_CHOICES, SYP, USD
from django.utils import timezone

DEC0 = Decimal("0.000")

class PartyType(models.TextChoices):
    PROVIDER = "provider", "مزود"
    CUSTOMER = "customer", "زبون"
    WORKER   = "worker",   "عامل"

DEBT_PUBLIC_ID_PREFIX = "D-"
DEBT_PUBLIC_ID_MIN_WIDTH = 3
DEBT_PUBLIC_ID_SEQUENCE_KEY = "debt_record_public_id"
DEBT_PUBLIC_ID_PATTERN = re.compile(r"^D-(\d+)$")


def _format_debt_public_id(number: int) -> str:
    return f"{DEBT_PUBLIC_ID_PREFIX}{int(number):0{DEBT_PUBLIC_ID_MIN_WIDTH}d}"


def _extract_debt_public_id_number(value: str) -> int | None:
    m = DEBT_PUBLIC_ID_PATTERN.match((value or "").strip())
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


class DebtPublicIdSequence(models.Model):
    key = models.CharField(max_length=64, primary_key=True)
    next_value = models.BigIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "debts_debtpublicidsequence"

    def __str__(self) -> str:
        return f"{self.key}:{self.next_value}"


def _provider_settlement_action_public_id_default() -> str:
    return f"PSA-{uuid4().hex[:12].upper()}"


def _max_existing_sequential_public_id() -> int:
    max_seen = 0
    for public_id in DebtRecord.objects.values_list("public_id", flat=True).iterator():
        num = _extract_debt_public_id_number(public_id)
        if num is not None and num > max_seen:
            max_seen = num
    return max_seen


def _lock_or_create_debt_public_sequence() -> tuple["DebtPublicIdSequence", bool]:
    qs = DebtPublicIdSequence.objects.select_for_update()
    seq = qs.filter(key=DEBT_PUBLIC_ID_SEQUENCE_KEY).first()
    if seq is not None:
        return seq, False
    try:
        seq = DebtPublicIdSequence.objects.create(
            key=DEBT_PUBLIC_ID_SEQUENCE_KEY,
            next_value=1,
        )
        return seq, True
    except IntegrityError:
        return qs.get(key=DEBT_PUBLIC_ID_SEQUENCE_KEY), False


def _initialize_sequence_start_locked(*, sequence: "DebtPublicIdSequence") -> None:
    current = int(sequence.next_value or 1)
    if current > 1:
        return
    max_existing = _max_existing_sequential_public_id()
    desired_next = max(1, max_existing + 1)
    if desired_next != current:
        sequence.next_value = desired_next
        sequence.save(update_fields=["next_value", "updated_at"])


def _debt_public_id_default() -> str:
    with transaction.atomic():
        sequence, _ = _lock_or_create_debt_public_sequence()
        _initialize_sequence_start_locked(sequence=sequence)

        next_value = int(sequence.next_value or 1)
        while True:
            candidate = _format_debt_public_id(next_value)
            next_value += 1
            if not DebtRecord.objects.filter(public_id=candidate).exists():
                sequence.next_value = next_value
                sequence.save(update_fields=["next_value", "updated_at"])
                return candidate


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

    total_syp = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    total_usd = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    remaining_syp = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    remaining_usd = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    fx_syp_per_usd_at_creation = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
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

    payment_syp = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    payment_usd = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    applied_syp = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    applied_usd = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
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

    total       = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    paid_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
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
    amount     = models.DecimalField(max_digits=14, decimal_places=2)
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

    total     = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    collected = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
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
    amount     = models.DecimalField(max_digits=14, decimal_places=2)
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


class ProviderSettlementActionType(models.TextChoices):
    PAY_PROVIDER = "pay_provider", "Pay Provider"
    RECEIVE_FROM_PROVIDER = "receive_from_provider", "Receive From Provider"


class ProviderSettlementActionStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    COMMITTED = "committed", "Committed"
    FAILED = "failed", "Failed"
    REVERSED = "reversed", "Reversed"


class ProviderSettlementAction(models.Model):
    public_id = models.CharField(
        max_length=24,
        unique=True,
        db_index=True,
        default=_provider_settlement_action_public_id_default,
        editable=False,
    )
    provider = models.ForeignKey(
        "billing.Provider",
        on_delete=models.PROTECT,
        related_name="provider_settlement_actions",
    )
    action = models.CharField(
        max_length=32,
        choices=ProviderSettlementActionType.choices,
        db_index=True,
    )
    currency = models.CharField(max_length=3, choices=CURRENCY_CHOICES, db_index=True)

    requested_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    eligible_total_remaining = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    total_applied = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    unallocated_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))

    money_container = models.ForeignKey(
        "financials.MoneyContainer",
        on_delete=models.PROTECT,
        related_name="provider_settlement_actions",
    )
    receipt = models.ForeignKey(
        "financials.Receipt",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="provider_settlement_actions",
    )

    idempotency_key = models.CharField(max_length=180)
    preview_fingerprint = models.CharField(max_length=160, blank=True, default="")

    status = models.CharField(
        max_length=16,
        choices=ProviderSettlementActionStatus.choices,
        default=ProviderSettlementActionStatus.PENDING,
        db_index=True,
    )
    diagnostics = models.TextField(blank=True, default="")

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="debts_provider_settlement_actions",
    )
    reversed_by_action = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="reverses_actions",
    )

    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "debts_providersettlementaction"
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "idempotency_key"],
                name="uniq_psa_provider_idempotency",
            ),
            models.CheckConstraint(check=models.Q(requested_amount__gte=0), name="psa_requested_amount_ge0"),
            models.CheckConstraint(check=models.Q(eligible_total_remaining__gte=0), name="psa_eligible_total_ge0"),
            models.CheckConstraint(check=models.Q(total_applied__gte=0), name="psa_total_applied_ge0"),
            models.CheckConstraint(check=models.Q(unallocated_amount__gte=0), name="psa_unallocated_amount_ge0"),
            models.CheckConstraint(
                check=models.Q(total_applied__lte=models.F("requested_amount")),
                name="psa_applied_lte_requested",
            ),
        ]
        indexes = [
            models.Index(fields=["provider", "status", "created_at"]),
            models.Index(fields=["provider", "currency", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.public_id} {self.action} {self.currency} {self.requested_amount}"


class ProviderSettlementDebtSource(models.TextChoices):
    CENTRAL = "central", "Central"
    LEGACY = "legacy", "Legacy"


class ProviderSettlementAllocation(models.Model):
    action = models.ForeignKey(
        ProviderSettlementAction,
        on_delete=models.CASCADE,
        related_name="allocations",
    )
    sequence = models.PositiveIntegerField(default=1)

    debt_source = models.CharField(max_length=16, choices=ProviderSettlementDebtSource.choices, db_index=True)
    direction = models.CharField(max_length=12, choices=DebtDirection.choices, db_index=True)
    currency = models.CharField(max_length=3, choices=CURRENCY_CHOICES, db_index=True)

    central_debt = models.ForeignKey(
        DebtRecord,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="provider_settlement_allocations",
    )
    legacy_debtor_debt = models.ForeignKey(
        DebtorDebt,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="provider_settlement_allocations",
    )
    legacy_creditor_debt = models.ForeignKey(
        CreditorDebt,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="provider_settlement_allocations",
    )

    debt_id = models.CharField(max_length=64, blank=True, default="")
    debt_public_id = models.CharField(max_length=24, blank=True, default="")
    cause_type = models.CharField(max_length=32, blank=True, default="")
    source_identity = models.CharField(max_length=128, blank=True, default="")

    before_remaining = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    applied = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    after_remaining = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    would_close = models.BooleanField(default=False)
    closed_after = models.BooleanField(default=False)

    debt_settlement = models.ForeignKey(
        DebtSettlement,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="provider_settlement_allocations",
    )
    debtor_payment = models.ForeignKey(
        DebtorPayment,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="provider_settlement_allocations",
    )
    creditor_receipt = models.ForeignKey(
        CreditorReceipt,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="provider_settlement_allocations",
    )

    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        db_table = "debts_providersettlementallocation"
        constraints = [
            models.UniqueConstraint(
                fields=["action", "sequence"],
                name="uniq_psa_alloc_action_sequence",
            ),
            models.CheckConstraint(check=models.Q(before_remaining__gte=0), name="psa_alloc_before_ge0"),
            models.CheckConstraint(check=models.Q(applied__gte=0), name="psa_alloc_applied_ge0"),
            models.CheckConstraint(check=models.Q(after_remaining__gte=0), name="psa_alloc_after_ge0"),
            models.CheckConstraint(
                check=models.Q(applied__lte=models.F("before_remaining")),
                name="psa_alloc_applied_lte_before",
            ),
            models.CheckConstraint(
                check=models.Q(after_remaining=models.F("before_remaining") - models.F("applied")),
                name="psa_alloc_after_matches_before_minus_applied",
            ),
        ]
        indexes = [
            models.Index(fields=["action", "sequence"]),
            models.Index(fields=["debt_source", "direction", "currency"]),
        ]

    def __str__(self) -> str:
        return (
            f"ProviderSettlementAllocation action={self.action_id} seq={self.sequence} "
            f"{self.currency} applied={self.applied}"
        )
