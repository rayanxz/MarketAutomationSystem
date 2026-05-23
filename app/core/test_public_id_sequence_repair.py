from __future__ import annotations

from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from billing.models import (
    BILL_PUBLIC_ID_PREFIX,
    BILL_PUBLIC_ID_SEQUENCE_KEY,
    PROVIDER_PUBLIC_ID_PREFIX,
    PROVIDER_PUBLIC_ID_SEQUENCE_KEY,
    PROVIDER_RETURN_PUBLIC_ID_PREFIX,
    PROVIDER_RETURN_PUBLIC_ID_SEQUENCE_KEY,
    Bill,
    Provider,
    ProviderReturn,
)
from core.models import PublicIdSequence
from debts.models import (
    DEBT_PUBLIC_ID_PREFIX,
    DEBT_PUBLIC_ID_SEQUENCE_KEY,
    DebtCauseType,
    DebtDirection,
    DebtPublicIdSequence,
    DebtRecord,
    DebtStatus,
    OtherPartyType,
)
from core.public_ids import expected_next_public_id_value
from pos.models import SALES_BILL_PUBLIC_ID_PREFIX, SALES_BILL_PUBLIC_ID_SEQUENCE_KEY, SalesBill


class RepairPublicIdSequencesCommandTests(TestCase):
    def setUp(self):
        self.provider = Provider.objects.create(name="Seq Repair Provider")

    def _create_debt(self, cause_id: str) -> DebtRecord:
        return DebtRecord.objects.create(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.MANUAL,
            cause_id=cause_id,
            source_app="debts",
            other_party_type=OtherPartyType.OTHER,
            other_party_id=f"party-{cause_id}",
            total_syp=Decimal("100.000"),
            total_usd=Decimal("0.000"),
            remaining_syp=Decimal("100.000"),
            remaining_usd=Decimal("0.000"),
            status=DebtStatus.OPEN,
        )

    def test_dry_run_reports_mismatch_without_mutation(self):
        PublicIdSequence.objects.update_or_create(
            key=PROVIDER_RETURN_PUBLIC_ID_SEQUENCE_KEY,
            defaults={"next_value": 2},
        )
        self.assertEqual(ProviderReturn.objects.count(), 0)

        out = StringIO()
        call_command(
            "repair_public_id_sequences",
            "--only",
            "billing.ProviderReturn",
            stdout=out,
        )

        seq = PublicIdSequence.objects.get(key=PROVIDER_RETURN_PUBLIC_ID_SEQUENCE_KEY)
        self.assertEqual(int(seq.next_value), 2)
        output = out.getvalue()
        self.assertIn("mode=DRY-RUN", output)
        self.assertIn("action=WOULD_FIX", output)

    def test_apply_repairs_all_targets_and_provider_return_starts_from_pr_001(self):
        Bill.objects.create(provider=self.provider)  # PB-001
        PublicIdSequence.objects.update_or_create(
            key=BILL_PUBLIC_ID_SEQUENCE_KEY,
            defaults={"next_value": 99},
        )

        PublicIdSequence.objects.update_or_create(
            key=PROVIDER_RETURN_PUBLIC_ID_SEQUENCE_KEY,
            defaults={"next_value": 2},
        )

        SalesBill.objects.create(customer_name="S1")  # PS-001
        SalesBill.objects.create(customer_name="S2")  # PS-002
        PublicIdSequence.objects.filter(key=SALES_BILL_PUBLIC_ID_SEQUENCE_KEY).delete()

        self._create_debt("seed")  # D-001
        DebtPublicIdSequence.objects.update_or_create(
            key=DEBT_PUBLIC_ID_SEQUENCE_KEY,
            defaults={"next_value": 9},
        )

        out = StringIO()
        call_command("repair_public_id_sequences", "--apply", stdout=out)
        output = out.getvalue()

        bill_seq = PublicIdSequence.objects.get(key=BILL_PUBLIC_ID_SEQUENCE_KEY)
        provider_seq = PublicIdSequence.objects.get(key=PROVIDER_PUBLIC_ID_SEQUENCE_KEY)
        ret_seq = PublicIdSequence.objects.get(key=PROVIDER_RETURN_PUBLIC_ID_SEQUENCE_KEY)
        sales_seq = PublicIdSequence.objects.get(key=SALES_BILL_PUBLIC_ID_SEQUENCE_KEY)
        debt_seq = DebtPublicIdSequence.objects.get(key=DEBT_PUBLIC_ID_SEQUENCE_KEY)

        self.assertEqual(
            int(bill_seq.next_value),
            expected_next_public_id_value(
                model=Bill,
                field_name="public_id",
                prefix=BILL_PUBLIC_ID_PREFIX,
            ),
        )
        self.assertEqual(
            int(provider_seq.next_value),
            expected_next_public_id_value(
                model=Provider,
                field_name="public_id",
                prefix=PROVIDER_PUBLIC_ID_PREFIX,
            ),
        )
        self.assertEqual(
            int(ret_seq.next_value),
            expected_next_public_id_value(
                model=ProviderReturn,
                field_name="public_id",
                prefix=PROVIDER_RETURN_PUBLIC_ID_PREFIX,
            ),
        )
        self.assertEqual(
            int(sales_seq.next_value),
            expected_next_public_id_value(
                model=SalesBill,
                field_name="public_id",
                prefix=SALES_BILL_PUBLIC_ID_PREFIX,
            ),
        )
        self.assertEqual(
            int(debt_seq.next_value),
            expected_next_public_id_value(
                model=DebtRecord,
                field_name="public_id",
                prefix=DEBT_PUBLIC_ID_PREFIX,
            ),
        )
        self.assertIn("mode=APPLY", output)
        self.assertIn("action=FIXED", output)
        self.assertIn("action=CREATED", output)

        r1 = ProviderReturn.objects.create(provider=self.provider)
        r2 = ProviderReturn.objects.create(provider=self.provider)
        self.assertEqual(r1.public_id, "PR-001")
        self.assertEqual(r2.public_id, "PR-002")
