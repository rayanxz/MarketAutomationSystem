from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase

from catalog.forms import CollectionCreateForm, ProductCreateForm
from catalog.models import (
    Product,
    ProductBarcode,
    ProductCollection,
    ProductSet,
    ProductUnitId,
    UnitType,
)
from catalog.services.deletion_policy import (
    ProductDisableBlockedError,
    ProductHardDeleteBlockedError,
    disable_product,
    hard_delete_product,
    reactivate_product,
)
from inventory.models import ProductMovement
from stock.models import ProductContainer, StockFifoLayer


class CatalogIntegrityBaseTestCase(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.store, _ = ProductContainer.objects.get_or_create(
            code="store",
            defaults={"name": "Store", "is_store": True, "is_active": True},
        )
        self.wh1, _ = ProductContainer.objects.get_or_create(
            code="wh1",
            defaults={"name": "WH1", "is_active": True},
        )
        self.wh2, _ = ProductContainer.objects.get_or_create(
            code="wh2",
            defaults={"name": "WH2", "is_active": True},
        )
        self.collection_a = self.make_collection("Collection A")
        self.set_a = self.make_set(self.collection_a, "Set A")

    def make_collection(self, name: str) -> ProductCollection:
        return ProductCollection.objects.create(name=name)

    def make_set(self, collection: ProductCollection, name: str) -> ProductSet:
        return ProductSet.objects.create(collection=collection, name=name)

    def make_product(
        self,
        name: str,
        *,
        set_obj: ProductSet | None = None,
        is_active: bool = True,
        unit_primary: str = UnitType.PIECE,
        unit_secondary: str = "",
        conversion_factor: Decimal | None = None,
    ) -> Product:
        return Product.objects.create(
            name=name,
            set=set_obj or self.set_a,
            is_active=is_active,
            unit_primary=unit_primary,
            unit_secondary=unit_secondary,
            conversion_factor=conversion_factor,
            allow_syp_sales=True,
            allow_syp_purchasing=True,
            allow_usd_sales=False,
            allow_usd_purchasing=False,
            default_purchase_currency="SYP",
            default_sale_currency="SYP",
            default_cost_syp=Decimal("1.0000"),
            default_cost_usd=Decimal("0.0000"),
            default_price_syp=Decimal("2.0000"),
            default_price_usd=Decimal("0.0000"),
        )

    def add_history(self, product: Product) -> ProductMovement:
        return ProductMovement.objects.create(
            product=product,
            container=self.store,
            qty_primary=Decimal("1.000"),
            unit_index=ProductMovement.UnitIndex.PRIMARY,
            unit_cost=Decimal("1.0000"),
            total_cost=Decimal("1.000"),
            movement_type=ProductMovement.MovementType.ADJUSTMENT,
            source_app="tests",
            source_model=self.__class__.__name__,
            source_id=f"history-{product.pk}",
        )

    def add_stock(self, product: Product, *, container: ProductContainer, qty: str) -> StockFifoLayer:
        return StockFifoLayer.objects.create(
            product=product,
            container=container,
            qty_remaining=Decimal(qty),
            unit_cost=Decimal("1.0000"),
            cost_currency="SYP",
        )

    def product_form_data(self, **overrides) -> dict:
        data = {
            "collection_name": self.collection_a.name,
            "set_name": self.set_a.name,
            "create_parent": "",
            "name": "Form Product",
            "unit_primary": UnitType.PIECE,
            "unit_secondary": "",
            "conversion_factor": "",
            "allow_syp_sales": "on",
            "allow_syp_purchasing": "on",
            "allow_usd_sales": "",
            "allow_usd_purchasing": "",
            "default_purchase_currency": "SYP",
            "default_sale_currency": "SYP",
            "default_cost_syp": "1.0000",
            "default_cost_usd": "0.0000",
            "default_price_syp": "2.0000",
            "default_price_usd": "0.0000",
            "cost_syp": "0.0000",
            "cost_usd": "0.0000",
            "price_syp": "0.0000",
            "price_usd": "0.0000",
            "notes": "",
            "barcodes_u1": "",
            "barcodes_u2": "",
            "unit_primary_ids": "",
            "unit_secondary_ids": "",
        }
        data.update(overrides)
        return data


class TestCollectionLogic(CatalogIntegrityBaseTestCase):
    def test_create_collection_assigns_generated_code(self):
        c = self.make_collection("Collection B")
        self.assertTrue(c.code.startswith("#"))
        self.assertTrue(c.code[1:].isdigit())

    def test_duplicate_collection_name_rejected_case_insensitive(self):
        self.make_collection("Drinks")
        with self.assertRaises((ValidationError, IntegrityError)):
            self.make_collection("drinks")

    def test_rename_collection(self):
        self.collection_a.name = "Collection A Renamed"
        self.collection_a.save()
        self.collection_a.refresh_from_db()
        self.assertEqual(self.collection_a.name, "Collection A Renamed")

    def test_delete_collection_blocked_when_sets_exist(self):
        with self.assertRaises(ProtectedError):
            self.collection_a.delete()


class TestSetLogic(CatalogIntegrityBaseTestCase):
    def test_create_set_under_collection(self):
        s = self.make_set(self.collection_a, "Set B")
        self.assertEqual(s.collection_id, self.collection_a.id)
        self.assertTrue(s.code.startswith("@"))

    def test_duplicate_set_name_within_collection_rejected(self):
        self.make_set(self.collection_a, "Snacks")
        with self.assertRaises((ValidationError, IntegrityError)):
            self.make_set(self.collection_a, "snacks")

    def test_same_set_name_allowed_in_different_collections(self):
        other = self.make_collection("Collection B")
        self.make_set(self.collection_a, "Common Set")
        s2 = self.make_set(other, "Common Set")
        self.assertEqual(s2.collection_id, other.id)

    def test_rename_set(self):
        self.set_a.name = "Set A Renamed"
        self.set_a.save()
        self.set_a.refresh_from_db()
        self.assertEqual(self.set_a.name, "Set A Renamed")

    def test_delete_set_without_products(self):
        s = self.make_set(self.collection_a, "Delete Me")
        sid = s.id
        s.delete()
        self.assertFalse(ProductSet.objects.filter(pk=sid).exists())

    def test_delete_set_blocked_when_products_exist(self):
        s = self.make_set(self.collection_a, "Protected Set")
        self.make_product("Set Product", set_obj=s)
        with self.assertRaises(ProtectedError):
            s.delete()


class TestProductLifecycle(CatalogIntegrityBaseTestCase):
    def test_create_product_with_minimal_valid_data(self):
        p = self.make_product("Minimal Product")
        self.assertTrue(p.is_active)
        self.assertEqual(p.unit_primary, UnitType.PIECE)
        self.assertEqual(p.unit_secondary, "")
        self.assertIsNone(p.conversion_factor)

    def test_create_product_with_primary_and_secondary_units(self):
        p = self.make_product(
            "Dual Unit Product",
            unit_primary=UnitType.PIECE,
            unit_secondary=UnitType.BNDL,
            conversion_factor=Decimal("12.0000"),
        )
        self.assertEqual(p.unit_secondary, UnitType.BNDL)
        self.assertEqual(p.conversion_factor, Decimal("12.0000"))

    def test_duplicate_product_name_rejected(self):
        self.make_product("Unique Name")
        with self.assertRaises((ValidationError, IntegrityError)):
            self.make_product("unique name")

    def test_edit_product_fields(self):
        p = self.make_product("Editable")
        new_set = self.make_set(self.collection_a, "Edit Target Set")
        p.name = "Editable Renamed"
        p.notes = "edited notes"
        p.set = new_set
        p.save()
        p.refresh_from_db()
        self.assertEqual(p.name, "Editable Renamed")
        self.assertEqual(p.notes, "edited notes")
        self.assertEqual(p.set_id, new_set.id)

    def test_active_to_disabled_transition(self):
        p = self.make_product("Disable Me")
        ProductUnitId.objects.create(product=p, unit_index=1, value="U-DIS-1", is_active=True)
        ProductBarcode.objects.create(product=p, unit_index=1, barcode="B-DIS-1", is_active=True)
        disable_product(p)
        p.refresh_from_db()
        self.assertFalse(p.is_active)
        self.assertFalse(ProductUnitId.objects.filter(product=p, is_active=True).exists())
        self.assertFalse(ProductBarcode.objects.filter(product=p, is_active=True).exists())

    def test_disabled_to_active_reactivation_transition(self):
        p = self.make_product("Reactivate Me", is_active=False)
        ProductUnitId.objects.create(product=p, unit_index=1, value="U-REA-1", is_active=False)
        ProductBarcode.objects.create(product=p, unit_index=1, barcode="B-REA-1", is_active=False)
        reactivate_product(p)
        p.refresh_from_db()
        self.assertTrue(p.is_active)
        self.assertTrue(ProductUnitId.objects.filter(product=p, is_active=True).exists())
        self.assertTrue(ProductBarcode.objects.filter(product=p, is_active=True).exists())

    def test_active_to_deleted_hard_delete_when_allowed(self):
        p = self.make_product("Hard Delete Me")
        ProductUnitId.objects.create(product=p, unit_index=1, value="U-DEL-1", is_active=True)
        ProductBarcode.objects.create(product=p, unit_index=1, barcode="B-DEL-1", is_active=True)
        hard_delete_product(p)
        self.assertFalse(Product.objects.filter(pk=p.pk).exists())
        self.assertFalse(ProductUnitId.objects.filter(product_id=p.pk).exists())
        self.assertFalse(ProductBarcode.objects.filter(product_id=p.pk).exists())


class TestIdentifierLogic(CatalogIntegrityBaseTestCase):
    def test_add_primary_secondary_codes_and_barcodes(self):
        p = self.make_product(
            "Identifier Product",
            unit_secondary=UnitType.BNDL,
            conversion_factor=Decimal("10.0000"),
        )
        uid_primary = ProductUnitId.objects.create(product=p, unit_index=1, value="UID-P-1")
        uid_secondary = ProductUnitId.objects.create(product=p, unit_index=2, value="UID-S-1")
        bc_primary = ProductBarcode.objects.create(product=p, unit_index=1, barcode="BC-P-1")
        bc_secondary = ProductBarcode.objects.create(product=p, unit_index=2, barcode="BC-S-1")
        self.assertEqual(uid_primary.unit_index, 1)
        self.assertEqual(uid_secondary.unit_index, 2)
        self.assertEqual(bc_primary.unit_index, 1)
        self.assertEqual(bc_secondary.unit_index, 2)

    def test_duplicate_unit_id_rejected_globally(self):
        p1 = self.make_product("UID Product A")
        p2 = self.make_product("UID Product B")
        ProductUnitId.objects.create(product=p1, unit_index=1, value="UID-GLOBAL")
        with self.assertRaises(IntegrityError):
            ProductUnitId.objects.create(product=p2, unit_index=1, value="UID-GLOBAL")

    def test_duplicate_barcode_rejected_globally(self):
        p1 = self.make_product("BC Product A")
        p2 = self.make_product("BC Product B")
        ProductBarcode.objects.create(product=p1, unit_index=1, barcode="BC-GLOBAL")
        with self.assertRaises(IntegrityError):
            ProductBarcode.objects.create(product=p2, unit_index=1, barcode="BC-GLOBAL")

    def test_identifier_uniqueness_enforced_when_source_product_disabled(self):
        p1 = self.make_product("Inactive IDs Source", is_active=False)
        p2 = self.make_product("Inactive IDs Target")
        ProductUnitId.objects.create(product=p1, unit_index=1, value="UID-INACTIVE-SRC", is_active=False)
        ProductBarcode.objects.create(product=p1, unit_index=1, barcode="BC-INACTIVE-SRC", is_active=False)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ProductUnitId.objects.create(product=p2, unit_index=1, value="UID-INACTIVE-SRC", is_active=True)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ProductBarcode.objects.create(product=p2, unit_index=1, barcode="BC-INACTIVE-SRC", is_active=True)

    def test_edit_identifiers(self):
        p = self.make_product("Edit IDs Product")
        uid = ProductUnitId.objects.create(product=p, unit_index=1, value="UID-OLD")
        bc = ProductBarcode.objects.create(product=p, unit_index=1, barcode="BC-OLD")
        uid.value = "UID-NEW"
        uid.save()
        bc.barcode = "BC-NEW"
        bc.save()
        uid.refresh_from_db()
        bc.refresh_from_db()
        self.assertEqual(uid.value, "UID-NEW")
        self.assertEqual(bc.barcode, "BC-NEW")

    def test_delete_identifiers(self):
        p = self.make_product("Delete IDs Product")
        uid = ProductUnitId.objects.create(product=p, unit_index=1, value="UID-DEL")
        bc = ProductBarcode.objects.create(product=p, unit_index=1, barcode="BC-DEL")
        uid.delete()
        bc.delete()
        self.assertFalse(ProductUnitId.objects.filter(pk=uid.pk).exists())
        self.assertFalse(ProductBarcode.objects.filter(pk=bc.pk).exists())


class TestUnitRules(CatalogIntegrityBaseTestCase):
    def test_primary_only_normalizes_conversion_factor_to_none(self):
        p = self.make_product("Normalize Conversion", conversion_factor=Decimal("5.0000"))
        p.refresh_from_db()
        self.assertEqual(p.unit_secondary, "")
        self.assertIsNone(p.conversion_factor)

    def test_same_primary_and_secondary_units_invalid(self):
        with self.assertRaises(ValidationError):
            self.make_product(
                "Invalid Same Units",
                unit_primary=UnitType.PIECE,
                unit_secondary=UnitType.PIECE,
                conversion_factor=Decimal("2.0000"),
            )

    def test_secondary_unit_requires_positive_conversion(self):
        p = self.make_product("Missing Conversion")
        p.unit_secondary = UnitType.BNDL
        p.conversion_factor = None
        with self.assertRaises(ValidationError):
            p.save()

    def test_zero_conversion_factor_invalid(self):
        p = self.make_product("Zero Conversion")
        p.unit_secondary = UnitType.BNDL
        p.conversion_factor = Decimal("0.0000")
        with self.assertRaises(ValidationError):
            p.save()

    def test_form_validation_for_units(self):
        form_same = ProductCreateForm(
            data=self.product_form_data(
                name="Form Unit Same",
                unit_primary=UnitType.PIECE,
                unit_secondary=UnitType.PIECE,
                conversion_factor="1.0000",
            )
        )
        self.assertFalse(form_same.is_valid())
        self.assertIn("unit_secondary", form_same.errors)

        form_missing_cf = ProductCreateForm(
            data=self.product_form_data(
                name="Form Missing CF",
                unit_primary=UnitType.PIECE,
                unit_secondary=UnitType.BNDL,
                conversion_factor="",
            )
        )
        self.assertFalse(form_missing_cf.is_valid())
        self.assertIn("conversion_factor", form_missing_cf.errors)


class TestDeletionPolicy(CatalogIntegrityBaseTestCase):
    def test_disable_blocked_when_stock_not_zero(self):
        p = self.make_product("Disable With Stock")
        self.add_stock(p, container=self.store, qty="1.000")
        with self.assertRaises(ProductDisableBlockedError):
            disable_product(p)

    def test_hard_delete_blocked_when_history_exists(self):
        p = self.make_product("Delete With History")
        self.add_history(p)
        with self.assertRaises(ProductHardDeleteBlockedError):
            hard_delete_product(p)
        self.assertTrue(Product.objects.filter(pk=p.pk).exists())

    def test_hard_delete_blocked_when_stock_not_zero(self):
        p = self.make_product("Delete With Stock")
        self.add_stock(p, container=self.store, qty="2.500")
        with self.assertRaises(ProductHardDeleteBlockedError):
            hard_delete_product(p)
        self.assertTrue(Product.objects.filter(pk=p.pk).exists())

    def test_hard_delete_allowed_without_history_or_stock(self):
        p = self.make_product("Delete Allowed")
        hard_delete_product(p)
        self.assertFalse(Product.objects.filter(pk=p.pk).exists())

    def test_disable_and_reactivate_sync_identifier_flags(self):
        p = self.make_product("Disable Reactivate IDs")
        uid = ProductUnitId.objects.create(product=p, unit_index=1, value="UID-SYNC", is_active=True)
        bc = ProductBarcode.objects.create(product=p, unit_index=1, barcode="BC-SYNC", is_active=True)
        disable_product(p)
        uid.refresh_from_db()
        bc.refresh_from_db()
        self.assertFalse(uid.is_active)
        self.assertFalse(bc.is_active)
        reactivate_product(p)
        uid.refresh_from_db()
        bc.refresh_from_db()
        self.assertTrue(uid.is_active)
        self.assertTrue(bc.is_active)


class TestHistoryLockBehavior(CatalogIntegrityBaseTestCase):
    def test_product_with_history_blocks_identity_changes(self):
        p = self.make_product("History Locked")
        self.add_history(p)
        p.name = "History Locked Renamed"
        with self.assertRaises(ValidationError):
            p.save()

    def test_product_with_history_allows_non_identity_updates(self):
        p = self.make_product("History Notes")
        self.add_history(p)
        p.notes = "safe update"
        p.save()
        p.refresh_from_db()
        self.assertEqual(p.notes, "safe update")

    def test_disabled_product_with_history_still_blocks_locked_changes(self):
        p = self.make_product("History Disabled")
        self.add_history(p)
        disable_product(p)
        p.refresh_from_db()
        self.assertFalse(p.is_active)
        p.unit_primary = UnitType.GRAM
        with self.assertRaises(ValidationError):
            p.save()

    def test_history_marks_set_and_collection_non_hard_deletable(self):
        p = self.make_product("Flag Marker")
        self.assertTrue(self.set_a.can_be_hard_deleted)
        self.assertTrue(self.collection_a.can_be_hard_deleted)
        self.add_history(p)
        self.set_a.refresh_from_db()
        self.collection_a.refresh_from_db()
        self.assertFalse(self.set_a.can_be_hard_deleted)
        self.assertFalse(self.collection_a.can_be_hard_deleted)


class TestStringRepresentations(CatalogIntegrityBaseTestCase):
    def test_string_representations_return_strings(self):
        p = self.make_product("String Product")
        uid = ProductUnitId.objects.create(product=p, unit_index=1, value="UID-STR")
        bc = ProductBarcode.objects.create(product=p, unit_index=1, barcode="BC-STR")

        values = [str(self.collection_a), str(self.set_a), str(p), str(uid), str(bc)]
        for value in values:
            self.assertIsInstance(value, str)
            self.assertTrue(value.strip())

        self.assertIn(self.collection_a.name, str(self.collection_a))
        self.assertIn(self.set_a.name, str(self.set_a))
        self.assertIn(p.name, str(p))
        self.assertIn(uid.value, str(uid))
        self.assertIn(bc.barcode, str(bc))


class TestValidationLayer(CatalogIntegrityBaseTestCase):
    def test_collection_form_rejects_case_insensitive_duplicate(self):
        self.make_collection("Form Collection")
        form = CollectionCreateForm(data={"name": "form collection"})
        self.assertFalse(form.is_valid())
        self.assertIn("name", form.errors)

    def test_product_form_rejects_duplicate_name(self):
        self.make_product("Form Duplicate Product")
        form = ProductCreateForm(data=self.product_form_data(name="form duplicate product"))
        self.assertFalse(form.is_valid())
        self.assertIn("name", form.errors)

    def test_model_level_validation_works_for_invalid_units(self):
        p = self.make_product("Model Validation Product")
        p.unit_secondary = UnitType.PIECE
        p.conversion_factor = Decimal("5.0000")
        with self.assertRaises(ValidationError):
            p.save()

    def test_database_constraint_triggers_for_identifier_uniqueness(self):
        p1 = self.make_product("DB Constraint A")
        p2 = self.make_product("DB Constraint B")
        ProductBarcode.objects.create(product=p1, unit_index=1, barcode="DB-BC-UNIQUE")
        with self.assertRaises(IntegrityError):
            ProductBarcode.objects.create(product=p2, unit_index=1, barcode="DB-BC-UNIQUE")
