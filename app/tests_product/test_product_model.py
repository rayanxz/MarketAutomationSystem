from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db.models.deletion import ProtectedError
from django.test import TestCase

from catalog.models import ProductUnitId, ProductBarcode, UnitType
from billing import services as BillingSV
from accounts.models import AccountProfile
from catalog.models import Product
from .utils import (
    create_collection_set,
    create_product,
    create_user_with_role,
    create_provider,
    create_money_container,
    create_stock_container,
    ensure_currency,
    ensure_fx,
)


class ProductModelIntegrityTests(TestCase):
    def test_create_primary_only_clears_conversion(self):
        _, pset = create_collection_set("C-P1", "S-P1")
        p = create_product(
            name="ProdPrimaryOnly",
            set_obj=pset,
            unit_primary=UnitType.PIECE,
            unit_secondary="",
            conversion_factor=None,
        )
        self.assertIsNone(p.conversion_factor)

    def test_secondary_requires_conversion(self):
        _, pset = create_collection_set("C-P2", "S-P2")
        p = Product(
            name="ProdInvalidConv",
            set=pset,
            unit_primary=UnitType.PIECE,
            unit_secondary=UnitType.GRAM,
            conversion_factor=None,
            cost=Decimal("1.0000"),
            price=Decimal("2.0000"),
        )
        with self.assertRaises(ValidationError):
            p.full_clean()

    def test_secondary_same_as_primary_invalid(self):
        _, pset = create_collection_set("C-P3", "S-P3")
        p = Product(
            name="ProdSameUnits",
            set=pset,
            unit_primary=UnitType.PIECE,
            unit_secondary=UnitType.PIECE,
            conversion_factor=Decimal("1"),
            cost=Decimal("1.0000"),
            price=Decimal("2.0000"),
        )
        with self.assertRaises(ValidationError):
            p.full_clean()

    def test_conversion_factor_one_allowed(self):
        _, pset = create_collection_set("C-P4", "S-P4")
        p = create_product(
            name="ProdConvOne",
            set_obj=pset,
            unit_primary=UnitType.PIECE,
            unit_secondary=UnitType.GRAM,
            conversion_factor=Decimal("1"),
        )
        self.assertEqual(p.conversion_factor, Decimal("1"))

    def test_conversion_factor_zero_invalid_with_secondary(self):
        _, pset = create_collection_set("C-P4b", "S-P4b")
        p = Product(
            name="ProdConvZero",
            set=pset,
            unit_primary=UnitType.PIECE,
            unit_secondary=UnitType.GRAM,
            conversion_factor=Decimal("0.0000"),
            cost=Decimal("1.0000"),
            price=Decimal("2.0000"),
        )
        with self.assertRaises(ValidationError):
            p.full_clean()

    def test_decimal_precision_cost_price(self):
        _, pset = create_collection_set("C-P5", "S-P5")
        p = create_product(
            name="ProdPrecOK",
            set_obj=pset,
            unit_primary=UnitType.PIECE,
            unit_secondary="",
            conversion_factor=None,
            cost=Decimal("1.2345"),
            price=Decimal("9.8765"),
        )
        p.refresh_from_db()
        self.assertEqual(p.cost, Decimal("1.2345"))
        self.assertEqual(p.price, Decimal("9.8765"))

        p_bad = Product(
            name="ProdPrecBad",
            set=pset,
            unit_primary=UnitType.PIECE,
            unit_secondary="",
            conversion_factor=None,
            cost=Decimal("1.234567"),
            price=Decimal("9.876543"),
        )
        with self.assertRaises(ValidationError):
            p_bad.full_clean()

    def test_unit_id_and_barcode_uniqueness(self):
        _, pset = create_collection_set("C-P6", "S-P6")
        p = create_product(name="ProdCodes", set_obj=pset)
        ProductUnitId.objects.create(product=p, unit_index=1, value="U1-ABC")
        ProductBarcode.objects.create(product=p, unit_index=1, barcode="BC-123")
        from django.db import transaction
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ProductUnitId.objects.create(product=p, unit_index=1, value="U1-ABC")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ProductBarcode.objects.create(product=p, unit_index=1, barcode="BC-123")

    def test_product_delete_protected_after_transactions(self):
        user = create_user_with_role("mgr-del", AccountProfile.Role.MANAGER)
        ensure_currency("SYP", "SYP")
        ensure_currency("USD", "USD")
        ensure_fx(Decimal("10000"))
        container = create_stock_container("store", "Store", is_store=True)
        money_container = create_money_container(name="DrawerDel", user=user)
        provider = create_provider("ProvDel")
        _, pset = create_collection_set("C-P7", "S-P7")
        prod = create_product(name="ProdDel", set_obj=pset)
        BillingSV.create_bill(
            actor=user,
            provider_id=provider.id,
            status="paid",
            paid_amount=Decimal("1.000"),
            items=[
                {
                    "product_id": prod.id,
                    "unit_index": 1,
                    "qty_raw": "1",
                    "cost": "1.0000",
                    "price": "2.0000",
                    "currency": "SYP",
                }
            ],
            update_product_defaults=False,
            container=container,
            money_container_id=money_container.id,
            settlement_currency="SYP",
        )
        with self.assertRaises(ProtectedError):
            prod.delete()
