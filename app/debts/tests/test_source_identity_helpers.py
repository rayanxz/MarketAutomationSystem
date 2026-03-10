from __future__ import annotations

from decimal import Decimal

from django.test import TestCase

from billing.models import Provider
from debts.models import DebtorDebt, PartyType
from debts.source_identity import (
    canonical_source_identity,
    source_identity_lookup_q,
    source_identity_numeric_base,
)


class SourceIdentityHelperTests(TestCase):
    def setUp(self):
        self.provider = Provider.objects.create(name="Identity Provider")

    def test_canonical_source_identity_normalizes_legacy_usd_suffix(self):
        src, legacy, cur = canonical_source_identity(source_id="123:USD", currency_code="SYP")
        self.assertEqual(src, "123")
        self.assertEqual(legacy, "123:USD")
        self.assertEqual(cur, "USD")

    def test_lookup_q_matches_canonical_and_legacy_variants(self):
        DebtorDebt.objects.create(
            provider=self.provider,
            source_app="billing",
            source_model="Bill",
            source_id="321",
            legacy_source_id="",
            total=Decimal("10.000"),
            paid_amount=Decimal("0.000"),
            status=DebtorDebt.Status.OPEN,
            party_type=PartyType.PROVIDER,
            party_name=self.provider.name,
            currency_code="USD",
        )
        DebtorDebt.objects.create(
            provider=self.provider,
            source_app="billing",
            source_model="Bill",
            source_id="321:USD",
            legacy_source_id="321:USD",
            total=Decimal("5.000"),
            paid_amount=Decimal("0.000"),
            status=DebtorDebt.Status.OPEN,
            party_type=PartyType.PROVIDER,
            party_name=self.provider.name,
            currency_code="USD",
        )

        rows = DebtorDebt.objects.filter(
            source_app="billing",
            source_model="Bill",
        ).filter(source_identity_lookup_q(source_ids=[321]))
        self.assertEqual(rows.count(), 2)

    def test_numeric_base_prefers_parseable_source(self):
        self.assertEqual(
            source_identity_numeric_base(source_id="444:USD", legacy_source_id=""),
            444,
        )
        self.assertEqual(
            source_identity_numeric_base(source_id="", legacy_source_id="555:USD"),
            555,
        )
        self.assertIsNone(source_identity_numeric_base(source_id="manual:777", legacy_source_id=""))
