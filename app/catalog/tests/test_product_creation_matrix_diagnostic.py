from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.urls import reverse

from accounts.models import AccountProfile
from catalog.models import Product, ProductCollection, ProductSet, UnitType


class ProductCreationMatrixDiagnosticTests(TestCase):
    """
    Diagnostic-only matrix to isolate backend creation behavior from frontend binding issues.
    """

    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="diag_manager", password="pass")
        AccountProfile.objects.create(user=self.user, role=AccountProfile.Role.MANAGER)
        self.client.force_login(self.user)
        self.url = reverse("manager_product_new")

    def _raw_db_units(self, product_id: int):
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT unit_primary, unit_secondary, conversion_factor
                FROM catalog_product
                WHERE id = %s
                """,
                [product_id],
            )
            return cursor.fetchone()

    def _post_payload(
        self,
        *,
        collection_name: str,
        set_name: str,
        product_name: str,
        unit_primary: str,
        unit_secondary: str,
        conversion_factor,
        cost: str,
        price: str,
        barcode: str,
    ):
        ProductCollection.objects.get_or_create(name=collection_name)
        col = ProductCollection.objects.get(name=collection_name)
        ProductSet.objects.get_or_create(collection=col, name=set_name)

        data = {
            "collection_name": collection_name,
            "set_name": set_name,
            "create_parent": "",
            "name": product_name,
            "unit_primary": unit_primary,
            "unit_secondary": unit_secondary,
            "cost": cost,
            "price": price,
            "allow_syp_sales": "on",
            "allow_syp_purchasing": "on",
            "allow_usd_sales": "",
            "allow_usd_purchasing": "",
            "default_purchase_currency": "SYP",
            "default_sale_currency": "SYP",
            "default_cost_syp": cost,
            "default_cost_usd": "0.0000",
            "default_price_syp": price,
            "default_price_usd": "0.0000",
            "notes": f"diag:{product_name}",
            "barcodes_u1[]": [barcode],
        }
        if conversion_factor is not None:
            data["conversion_factor"] = conversion_factor
        return data

    @staticmethod
    def _norm_decimal(v):
        if v in (None, ""):
            return None
        return Decimal(str(v))

    def test_product_creation_matrix_diagnostic(self):
        matrix = [
            {
                "label": "Case A",
                "unit_primary": UnitType.PIECE,
                "unit_secondary": "",
                "conversion_factor": "",
                "expected_secondary": "",
                "expected_cf": None,
                "expected_single": True,
                "collection": "DIAG-C-A",
                "set_name": "DIAG-S-A",
                "product_name": "DIAG-P-A",
                "barcode": "DIAG-BC-A",
                "cost": "1.1000",
                "price": "2.1000",
            },
            {
                "label": "Case B",
                "unit_primary": UnitType.PIECE,
                "unit_secondary": UnitType.PIECE,
                "conversion_factor": "1",
                "expect_invalid": True,
                "collection": "DIAG-C-B",
                "set_name": "DIAG-S-B",
                "product_name": "DIAG-P-B",
                "barcode": "DIAG-BC-B",
                "cost": "3.2000",
                "price": "4.2000",
            },
            {
                "label": "Case C",
                "unit_primary": UnitType.PIECE,
                "unit_secondary": UnitType.BNDL,
                "conversion_factor": "12",
                "expected_secondary": UnitType.BNDL,
                "expected_cf": Decimal("12"),
                "expected_single": False,
                "collection": "DIAG-C-C",
                "set_name": "DIAG-S-C",
                "product_name": "DIAG-P-C",
                "barcode": "DIAG-BC-C",
                "cost": "5.3000",
                "price": "6.3000",
            },
            {
                "label": "Case D",
                "unit_primary": UnitType.LITER,
                "unit_secondary": UnitType.PKG,
                "conversion_factor": "130",
                "expected_secondary": UnitType.PKG,
                "expected_cf": Decimal("130"),
                "expected_single": False,
                "collection": "DIAG-C-D",
                "set_name": "DIAG-S-D",
                "product_name": "DIAG-P-D",
                "barcode": "DIAG-BC-D",
                "cost": "7.4000",
                "price": "8.4000",
            },
            {
                "label": "Case E",
                "unit_primary": UnitType.LITER,
                "unit_secondary": UnitType.LITER,
                "conversion_factor": "1",
                "expect_invalid": True,
                "collection": "DIAG-C-E",
                "set_name": "DIAG-S-E",
                "product_name": "DIAG-P-E",
                "barcode": "DIAG-BC-E",
                "cost": "9.5000",
                "price": "10.5000",
            },
        ]

        total = len(matrix)
        passed = 0
        failed = 0

        print("\n=== PRODUCT CREATION MATRIX DIAGNOSTIC START ===")
        for case in matrix:
            payload = self._post_payload(
                collection_name=case["collection"],
                set_name=case["set_name"],
                product_name=case["product_name"],
                unit_primary=case["unit_primary"],
                unit_secondary=case["unit_secondary"],
                conversion_factor=case["conversion_factor"],
                cost=case["cost"],
                price=case["price"],
                barcode=case["barcode"],
            )

            response = self.client.post(self.url, data=payload)
            with self.subTest(case=case["label"]):
                expected_invalid = bool(case.get("expect_invalid"))
                if expected_invalid:
                    self.assertEqual(response.status_code, 200)
                    self.assertFalse(Product.objects.filter(name=case["product_name"]).exists())
                    passed += 1
                    print(f"\n[{case['label']}] {case['product_name']}")
                    print("OK: same-unit creation rejected as expected.")
                    continue
                self.assertEqual(response.status_code, 302)

            product = Product.objects.get(name=case["product_name"])
            db_row = self._raw_db_units(product.id)
            db_unit_primary, db_unit_secondary, db_cf = db_row

            orm_cf = self._norm_decimal(product.conversion_factor)
            db_cf_norm = self._norm_decimal(db_cf)

            expected_primary = case["unit_primary"]
            expected_secondary = case["expected_secondary"]
            expected_cf = case["expected_cf"]
            expected_single = case["expected_single"]

            print(f"\n[{case['label']}] {case['product_name']}")
            print(
                "ORM => "
                f"id={product.id}, "
                f"unit_primary={product.unit_primary}, "
                f"unit_secondary={product.unit_secondary!r}, "
                f"conversion_factor={product.conversion_factor}, "
                f"is_single_unit={product.is_single_unit}"
            )
            print(
                "DB  => "
                f"unit_primary={db_unit_primary}, "
                f"unit_secondary={db_unit_secondary!r}, "
                f"conversion_factor={db_cf}"
            )

            mismatches = []
            if product.unit_primary != expected_primary or db_unit_primary != expected_primary:
                mismatches.append(
                    f"unit_primary expected={expected_primary} got ORM={product.unit_primary} DB={db_unit_primary}"
                )
            if product.unit_secondary != expected_secondary or db_unit_secondary != expected_secondary:
                mismatches.append(
                    f"unit_secondary expected={expected_secondary!r} got ORM={product.unit_secondary!r} DB={db_unit_secondary!r}"
                )
            if orm_cf != expected_cf or db_cf_norm != expected_cf:
                mismatches.append(
                    f"conversion_factor expected={expected_cf} got ORM={orm_cf} DB={db_cf_norm}"
                )
            if product.is_single_unit != expected_single:
                mismatches.append(
                    f"is_single_unit expected={expected_single} got ORM={product.is_single_unit}"
                )

            if mismatches:
                failed += 1
                for m in mismatches:
                    print(f"BACKEND FAILURE: {m}")
            else:
                passed += 1
                print("OK: case matched expected ORM + DB state.")

        print("\n=== PRODUCT CREATION MATRIX DIAGNOSTIC SUMMARY ===")
        print(f"Total cases: {total}")
        print(f"Passed cases: {passed}")
        print(f"Failed cases: {failed}")
        if failed == 0:
            print("BACKEND CREATION PATH IS CORRECT — ISSUE IS FRONTEND")
        else:
            print("BACKEND CREATION PATH HAS DEFECT")
        print("=== PRODUCT CREATION MATRIX DIAGNOSTIC END ===\n")

        self.assertEqual(failed, 0, "Diagnostic found backend mismatches in creation matrix.")
