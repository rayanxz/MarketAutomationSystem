import importlib
import json
from decimal import Decimal
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from accounts.models import AccountProfile
from catalog.models import ProductCollection, ProductSet, UnitType
from inventory.models import ProductMovement


class ImportStockSnapshotMovementTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="manager", password="pass")
        AccountProfile.objects.create(user=self.user, role=AccountProfile.Role.MANAGER)

        self.collection = ProductCollection.objects.create(name="C-IMP-SNAP")
        self.set_obj = ProductSet.objects.create(collection=self.collection, name="S-IMP-SNAP")

    def test_import_stock_qty_creates_movement_snapshot(self):
        with TemporaryDirectory() as tmpdir:
            with override_settings(MEDIA_ROOT=tmpdir):
                from catalog import import_engine as IE

                IE = importlib.reload(IE)
                IE._ensure_dirs()

                sid = "testsid-snap"
                meta = {
                    "file_type": "xlsx",
                    "collection_id": self.collection.id,
                    "has_header": True,
                }
                stage = {
                    "mapping": {},
                    "options": {"import_mode": "add_update"},
                    "rows": [
                        {
                            "rid": 1,
                            "data": {
                                "name": "ImportedSnap",
                                "set": self.set_obj.name,
                                "unit_primary": UnitType.PIECE,
                                "unit_secondary": "",
                                "conversion_factor": "",
                                "cost": "1.0000",
                                "price": "2.0000",
                                "stock_qty": "5",
                                "product_number": None,
                                "barcodes_u1": [],
                                "barcodes_u2": [],
                                "unit_ids_u1": [],
                                "unit_ids_u2": [],
                                "notes": "",
                                "dup_action": "add",
                            },
                            "errors": {},
                        }
                    ],
                }

                with open(IE._p(sid, ".meta.json"), "w", encoding="utf-8") as f:
                    json.dump(meta, f)
                with open(IE._p(sid, ".stage.json"), "w", encoding="utf-8") as f:
                    json.dump(stage, f)

                IE.commit_stage(sid, actor=self.user)

        mv = ProductMovement.objects.get(
            source_app="catalog",
            source_model="Import",
            source_id="1",
        )
        self.assertEqual(mv.product_name_at_txn, "ImportedSnap")
        self.assertEqual(mv.qty_primary_at_txn, Decimal("5.000"))
        self.assertEqual(mv.qty_used_at_txn, Decimal("5.000"))
        self.assertTrue(mv.unit_1_label_at_txn)
