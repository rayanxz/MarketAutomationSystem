from decimal import Decimal

from django.test import TestCase

from financials.forms import FxSettingsForm
from financials.models import FxSettings


class FxSettingsDisplayFormattingTests(TestCase):
    def test_unbound_form_trims_trailing_zeros_for_display(self):
        fx = FxSettings(rate_syp_per_usd=Decimal("10000.00"))
        form = FxSettingsForm(instance=fx)
        self.assertEqual(str(form["rate_syp_per_usd"].value()), "10000")

    def test_unbound_form_keeps_meaningful_fraction(self):
        fx = FxSettings(rate_syp_per_usd=Decimal("10000.250000"))
        form = FxSettingsForm(instance=fx)
        self.assertEqual(str(form["rate_syp_per_usd"].value()), "10000.25")

    def test_bound_form_keeps_user_input_value(self):
        form = FxSettingsForm(data={"rate_syp_per_usd": "10000.2500"})
        self.assertEqual(str(form["rate_syp_per_usd"].value()), "10000.2500")
