from __future__ import annotations

from typing import Type

from django.db import IntegrityError, models, transaction

from core.models import PublicIdSequence


DEFAULT_MIN_WIDTH = 3


def _normalize_prefix(prefix: str) -> str:
    p = str(prefix or "").strip().upper()
    if not p:
        raise ValueError("prefix is required")
    return p[:-1] + "-" if p.endswith("-") else p + "-"


def format_public_id(*, prefix: str, number: int, min_width: int = DEFAULT_MIN_WIDTH) -> str:
    normalized = _normalize_prefix(prefix)
    width = max(int(min_width or 0), 0)
    return f"{normalized}{int(number):0{width}d}"


def extract_public_id_number(*, value: str, prefix: str) -> int | None:
    normalized = _normalize_prefix(prefix)
    raw = str(value or "").strip().upper()
    if not raw.startswith(normalized):
        return None
    number_part = raw[len(normalized):]
    if not number_part.isdigit():
        return None
    try:
        return int(number_part)
    except Exception:
        return None


def _max_existing_public_id_number(
    *,
    model: Type[models.Model],
    field_name: str,
    prefix: str,
) -> int:
    max_seen = 0
    for raw_value in model.objects.values_list(field_name, flat=True).iterator():
        num = extract_public_id_number(value=raw_value, prefix=prefix)
        if num is not None and num > max_seen:
            max_seen = num
    return max_seen


def _lock_or_create_sequence(*, key: str) -> PublicIdSequence:
    qs = PublicIdSequence.objects.select_for_update()
    seq = qs.filter(key=key).first()
    if seq is not None:
        return seq
    try:
        return PublicIdSequence.objects.create(key=key, next_value=1)
    except IntegrityError:
        return qs.get(key=key)


def _initialize_sequence_start_locked(
    *,
    sequence: PublicIdSequence,
    model: Type[models.Model],
    field_name: str,
    prefix: str,
) -> None:
    current = int(sequence.next_value or 1)
    if current > 1:
        return
    max_existing = _max_existing_public_id_number(
        model=model,
        field_name=field_name,
        prefix=prefix,
    )
    desired_next = max(1, max_existing + 1)
    if desired_next != current:
        sequence.next_value = desired_next
        sequence.save(update_fields=["next_value", "updated_at"])


def allocate_next_public_id(
    *,
    sequence_key: str,
    prefix: str,
    model: Type[models.Model],
    field_name: str = "public_id",
    min_width: int = DEFAULT_MIN_WIDTH,
) -> str:
    if not sequence_key:
        raise ValueError("sequence_key is required")

    with transaction.atomic():
        sequence = _lock_or_create_sequence(key=sequence_key)
        _initialize_sequence_start_locked(
            sequence=sequence,
            model=model,
            field_name=field_name,
            prefix=prefix,
        )

        next_value = int(sequence.next_value or 1)
        while True:
            candidate = format_public_id(
                prefix=prefix,
                number=next_value,
                min_width=min_width,
            )
            next_value += 1
            if not model.objects.filter(**{field_name: candidate}).exists():
                sequence.next_value = next_value
                sequence.save(update_fields=["next_value", "updated_at"])
                return candidate


def peek_next_public_id(
    *,
    sequence_key: str,
    prefix: str,
    model: Type[models.Model],
    field_name: str = "public_id",
    min_width: int = DEFAULT_MIN_WIDTH,
) -> str:
    if not sequence_key:
        raise ValueError("sequence_key is required")

    sequence = PublicIdSequence.objects.filter(key=sequence_key).first()
    if sequence is not None and int(sequence.next_value or 1) > 1:
        next_value = int(sequence.next_value or 1)
    else:
        next_value = _max_existing_public_id_number(
            model=model,
            field_name=field_name,
            prefix=prefix,
        ) + 1

    while True:
        candidate = format_public_id(
            prefix=prefix,
            number=next_value,
            min_width=min_width,
        )
        if not model.objects.filter(**{field_name: candidate}).exists():
            return candidate
        next_value += 1
