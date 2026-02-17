from decimal import Decimal
from typing import Optional

from django.core.exceptions import ValidationError
from django.test import TestCase

from accounts.models import AccountProfile
from catalog.models import UnitType
from inventory import services as InvSV

from tests_product.utils import (
    create_user_with_role,
    create_collection_set,
    create_product,
    create_stock_container,
    ensure_currency,
    ensure_fx,
)


class ProductLockAfterMovementTests(TestCase):
    def setUp(self):
        self.user = create_user_with_role("mgr_lock", AccountProfile.Role.MANAGER)
        ensure_currency("SYP", "SYP")
        ensure_currency("USD", "USD")
        ensure_fx(Decimal("10000"))
        self.container = create_stock_container("store", "Store", is_store=True)

    def _make_product(self, *, name: str, unit_secondary: str = UnitType.GRAM, conv: Optional[Decimal] = Decimal("2")):
        _, pset = create_collection_set(f"C-{name}", f"S-{name}")
        return create_product(
            name=name,
            set_obj=pset,
            unit_primary=UnitType.PIECE,
            unit_secondary=unit_secondary,
            conversion_factor=conv,
            cost=Decimal("5.0000"),
            price=Decimal("9.0000"),
        )

    def test_product_edit_before_history_allowed(self):
        prod = self._make_product(name="ProdNoHist")
        _, new_set = create_collection_set("C-New", "S-New")

        prod.name = "ProdNoHist-NEW"
        prod.set = new_set
        prod.unit_primary = UnitType.LITER
        prod.unit_secondary = UnitType.BNDL
        prod.conversion_factor = Decimal("10")
        prod.save()

        prod.refresh_from_db()
        self.assertEqual(prod.name, "ProdNoHist-NEW")
        self.assertEqual(prod.set_id, new_set.id)
        self.assertEqual(prod.unit_primary, UnitType.LITER)
        self.assertEqual(prod.unit_secondary, UnitType.BNDL)
        self.assertEqual(prod.conversion_factor, Decimal("10"))

    def test_product_edit_after_history_blocked(self):
        prod = self._make_product(name="ProdHist")
        InvSV.record_purchase_item(
            actor=self.user,
            product=prod,
            unit_index=1,
            qty_primary=Decimal("3"),
            unit_cost=Decimal("5.0000"),
            source_app="tests",
            source_model="Seed",
            source_id="1",
            container=self.container,
        )
        self.assertTrue(prod.has_history())

        _, new_set = create_collection_set("C-Block", "S-Block")

        cases = [
            ("name", "ProdHist-NEW"),
            ("set", new_set),
            ("unit_primary", UnitType.LITER),
            ("unit_secondary", UnitType.BNDL),
            ("conversion_factor", Decimal("10")),
        ]

        for field, value in cases:
            prod.refresh_from_db()
            setattr(prod, field, value)
            with self.assertRaises(ValidationError, msg=f"Expected lock on {field}"):
                prod.save()

    def test_product_edit_after_history_nonlocked_allowed(self):
        prod = self._make_product(name="ProdHist2")
        InvSV.record_purchase_item(
            actor=self.user,
            product=prod,
            unit_index=1,
            qty_primary=Decimal("3"),
            unit_cost=Decimal("5.0000"),
            source_app="tests",
            source_model="Seed",
            source_id="1",
            container=self.container,
        )

        prod.refresh_from_db()
        prod.notes = "updated"
        prod.cost = Decimal("7.0000")
        prod.price = Decimal("11.0000")
        prod.is_active = False
        prod.save()

        prod.refresh_from_db()
        self.assertEqual(prod.notes, "updated")
        self.assertEqual(prod.cost, Decimal("7.0000"))
        self.assertEqual(prod.price, Decimal("11.0000"))
        self.assertFalse(prod.is_active)

    def test_has_history_detects_movement(self):
        prod = self._make_product(name="ProdHist3")
        self.assertFalse(prod.has_history())
        InvSV.record_purchase_item(
            actor=self.user,
            product=prod,
            unit_index=1,
            qty_primary=Decimal("1"),
            unit_cost=Decimal("5.0000"),
            source_app="tests",
            source_model="Seed",
            source_id="1",
            container=self.container,
        )
        prod.refresh_from_db()
        self.assertTrue(prod.has_history())

    def test_history_product_save_normalizes_stale_single_unit_conversion(self):
        prod = self._make_product(name="ProdHistNorm")
        InvSV.record_purchase_item(
            actor=self.user,
            product=prod,
            unit_index=1,
            qty_primary=Decimal("1"),
            unit_cost=Decimal("5.0000"),
            source_app="tests",
            source_model="Seed",
            source_id="1",
            container=self.container,
        )
        self.assertTrue(prod.has_history())

        # Seed legacy bad data without running model validation.
        prod.__class__.objects.filter(pk=prod.pk).update(
            unit_primary=UnitType.PIECE,
            unit_secondary=UnitType.PIECE,
            conversion_factor=Decimal("12"),
        )

        prod.refresh_from_db()
        prod.notes = "touch"
        prod.save()
        prod.refresh_from_db()
        self.assertEqual(prod.conversion_factor, Decimal("1"))

        prod.unit_secondary = UnitType.BNDL
        with self.assertRaises(ValidationError):
            prod.save()
