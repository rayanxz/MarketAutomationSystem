from __future__ import annotations

from dataclasses import dataclass

from django.core.management.base import BaseCommand
from django.db import IntegrityError, models, transaction

from billing.models import (
    BILL_PUBLIC_ID_PREFIX,
    BILL_PUBLIC_ID_SEQUENCE_KEY,
    PROVIDER_RETURN_PUBLIC_ID_PREFIX,
    PROVIDER_RETURN_PUBLIC_ID_SEQUENCE_KEY,
    Bill,
    ProviderReturn,
)
from core.models import PublicIdSequence
from core.public_ids import expected_next_public_id_value
from debts.models import (
    DEBT_PUBLIC_ID_PREFIX,
    DEBT_PUBLIC_ID_SEQUENCE_KEY,
    DebtPublicIdSequence,
    DebtRecord,
)
from pos.models import SALES_BILL_PUBLIC_ID_PREFIX, SALES_BILL_PUBLIC_ID_SEQUENCE_KEY, SalesBill


@dataclass(frozen=True)
class SequenceTarget:
    label: str
    sequence_model: type[models.Model]
    sequence_key: str
    document_model: type[models.Model]
    prefix: str
    field_name: str = "public_id"


@dataclass(frozen=True)
class TargetAuditRow:
    label: str
    sequence_key: str
    max_serial: int
    expected_next_value: int
    old_next_value: int | None
    new_next_value: int | None
    action: str


TARGETS: tuple[SequenceTarget, ...] = (
    SequenceTarget(
        label="billing.Bill",
        sequence_model=PublicIdSequence,
        sequence_key=BILL_PUBLIC_ID_SEQUENCE_KEY,
        document_model=Bill,
        prefix=BILL_PUBLIC_ID_PREFIX,
    ),
    SequenceTarget(
        label="billing.ProviderReturn",
        sequence_model=PublicIdSequence,
        sequence_key=PROVIDER_RETURN_PUBLIC_ID_SEQUENCE_KEY,
        document_model=ProviderReturn,
        prefix=PROVIDER_RETURN_PUBLIC_ID_PREFIX,
    ),
    SequenceTarget(
        label="pos.SalesBill",
        sequence_model=PublicIdSequence,
        sequence_key=SALES_BILL_PUBLIC_ID_SEQUENCE_KEY,
        document_model=SalesBill,
        prefix=SALES_BILL_PUBLIC_ID_PREFIX,
    ),
    SequenceTarget(
        label="debts.DebtRecord",
        sequence_model=DebtPublicIdSequence,
        sequence_key=DEBT_PUBLIC_ID_SEQUENCE_KEY,
        document_model=DebtRecord,
        prefix=DEBT_PUBLIC_ID_PREFIX,
    ),
)


def _audit_one_target(*, target: SequenceTarget, apply_changes: bool) -> TargetAuditRow:
    expected = expected_next_public_id_value(
        model=target.document_model,
        field_name=target.field_name,
        prefix=target.prefix,
    )
    max_serial = max(0, expected - 1)

    with transaction.atomic():
        qs = target.sequence_model.objects.select_for_update()
        seq = qs.filter(key=target.sequence_key).first()
        old_value = int(seq.next_value or 1) if seq is not None else None

        if seq is None:
            if not apply_changes:
                return TargetAuditRow(
                    label=target.label,
                    sequence_key=target.sequence_key,
                    max_serial=max_serial,
                    expected_next_value=expected,
                    old_next_value=None,
                    new_next_value=None,
                    action="WOULD_CREATE",
                )
            try:
                seq = target.sequence_model.objects.create(
                    key=target.sequence_key,
                    next_value=expected,
                )
            except IntegrityError:
                seq = qs.get(key=target.sequence_key)
                old_value = int(seq.next_value or 1)
                if old_value != expected:
                    seq.next_value = expected
                    seq.save(update_fields=["next_value", "updated_at"])
                    return TargetAuditRow(
                        label=target.label,
                        sequence_key=target.sequence_key,
                        max_serial=max_serial,
                        expected_next_value=expected,
                        old_next_value=old_value,
                        new_next_value=expected,
                        action="FIXED",
                    )
                return TargetAuditRow(
                    label=target.label,
                    sequence_key=target.sequence_key,
                    max_serial=max_serial,
                    expected_next_value=expected,
                    old_next_value=old_value,
                    new_next_value=old_value,
                    action="ALREADY_OK",
                )
            return TargetAuditRow(
                label=target.label,
                sequence_key=target.sequence_key,
                max_serial=max_serial,
                expected_next_value=expected,
                old_next_value=None,
                new_next_value=int(seq.next_value or expected),
                action="CREATED",
            )

        if old_value == expected:
            return TargetAuditRow(
                label=target.label,
                sequence_key=target.sequence_key,
                max_serial=max_serial,
                expected_next_value=expected,
                old_next_value=old_value,
                new_next_value=old_value,
                action="ALREADY_OK",
            )

        if not apply_changes:
            return TargetAuditRow(
                label=target.label,
                sequence_key=target.sequence_key,
                max_serial=max_serial,
                expected_next_value=expected,
                old_next_value=old_value,
                new_next_value=expected,
                action="WOULD_FIX",
            )

        seq.next_value = expected
        seq.save(update_fields=["next_value", "updated_at"])
        return TargetAuditRow(
            label=target.label,
            sequence_key=target.sequence_key,
            max_serial=max_serial,
            expected_next_value=expected,
            old_next_value=old_value,
            new_next_value=expected,
            action="FIXED",
        )


class Command(BaseCommand):
    help = (
        "Audit and repair public ID sequence rows to match max(existing serial)+1 "
        "for each targeted model."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            default=False,
            help="Persist sequence repairs. Without this flag, command runs in dry-run mode.",
        )
        parser.add_argument(
            "--only",
            action="append",
            dest="only",
            default=[],
            choices=[target.label for target in TARGETS],
            help="Audit only specific target label(s). Can be repeated.",
        )

    def handle(self, *args, **options):
        apply_changes = bool(options.get("apply"))
        only = set(options.get("only") or [])
        targets = [t for t in TARGETS if not only or t.label in only]

        mode = "APPLY" if apply_changes else "DRY-RUN"
        self.stdout.write(f"[repair_public_id_sequences] mode={mode} targets={len(targets)}")

        rows: list[TargetAuditRow] = []
        for target in targets:
            row = _audit_one_target(target=target, apply_changes=apply_changes)
            rows.append(row)
            self.stdout.write(
                (
                    f"{row.label} key={row.sequence_key} "
                    f"max_serial={row.max_serial} "
                    f"old_next={row.old_next_value if row.old_next_value is not None else 'NONE'} "
                    f"expected_next={row.expected_next_value} "
                    f"action={row.action} "
                    f"new_next={row.new_next_value if row.new_next_value is not None else 'NONE'}"
                )
            )

        actionable = {"WOULD_CREATE", "WOULD_FIX", "CREATED", "FIXED"}
        touched = sum(1 for row in rows if row.action in actionable)
        self.stdout.write(
            f"[repair_public_id_sequences] completed mode={mode} touched={touched} total={len(rows)}"
        )
