from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import time

from django.db import OperationalError, close_old_connections
from django.test import TestCase, TransactionTestCase

from debts.models import (
    DebtCauseType,
    DebtDirection,
    DebtRecord,
    DebtStatus,
    OtherPartyType,
)


def _create_manual_like_debt(*, cause_id: str, public_id: str | None = None) -> DebtRecord:
    payload = {
        "direction": DebtDirection.PAYABLE,
        "cause_type": DebtCauseType.MANUAL,
        "cause_id": str(cause_id),
        "source_app": "debts",
        "other_party_type": OtherPartyType.OTHER,
        "other_party_id": f"party-{cause_id}",
        "total_syp": Decimal("100.000"),
        "total_usd": Decimal("0.000"),
        "remaining_syp": Decimal("100.000"),
        "remaining_usd": Decimal("0.000"),
        "status": DebtStatus.OPEN,
    }
    if public_id is not None:
        payload["public_id"] = public_id
    return DebtRecord.objects.create(**payload)


class DebtPublicIdSequenceTests(TestCase):
    def test_new_ids_are_sequential_and_human_readable(self):
        d1 = _create_manual_like_debt(cause_id="1")
        d2 = _create_manual_like_debt(cause_id="2")
        d3 = _create_manual_like_debt(cause_id="3")

        self.assertEqual(d1.public_id, "D-001")
        self.assertEqual(d2.public_id, "D-002")
        self.assertEqual(d3.public_id, "D-003")

    def test_old_non_sequential_id_stays_unchanged_and_new_id_works(self):
        legacy = _create_manual_like_debt(
            cause_id="legacy-1",
            public_id="D-ABCDEF1234567890",
        )
        fresh = _create_manual_like_debt(cause_id="legacy-2")

        legacy.refresh_from_db()
        self.assertEqual(legacy.public_id, "D-ABCDEF1234567890")
        self.assertRegex(fresh.public_id, r"^D-\d+$")
        self.assertEqual(
            DebtRecord.objects.get(public_id=fresh.public_id).id,
            fresh.id,
        )

    def test_sequence_bootstraps_from_existing_numeric_id_if_present(self):
        _create_manual_like_debt(cause_id="seed", public_id="D-007")
        fresh = _create_manual_like_debt(cause_id="next")
        self.assertEqual(fresh.public_id, "D-008")


class DebtPublicIdConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def _create_in_thread(self, idx: int) -> str:
        for attempt in range(8):
            try:
                close_old_connections()
                debt = _create_manual_like_debt(cause_id=f"con-{idx}")
                return debt.public_id
            except OperationalError as exc:
                if "locked" not in str(exc).lower():
                    raise
                time.sleep(0.02 * (attempt + 1))
            finally:
                close_old_connections()
        raise AssertionError("could not create debt due to lock contention")

    def test_concurrent_creation_does_not_produce_duplicate_public_ids(self):
        count = 12
        with ThreadPoolExecutor(max_workers=6) as pool:
            ids = list(pool.map(self._create_in_thread, range(count)))

        self.assertEqual(len(ids), count)
        self.assertEqual(len(set(ids)), count)
        for pid in ids:
            self.assertRegex(pid, r"^D-\d+$")
