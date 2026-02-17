from __future__ import annotations

import importlib
import json
from decimal import Decimal
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from accounts.models import AccountProfile
from catalog.models import ProductCollection, ProductSet, Product, UnitType


class ImportArchivedProductBlockingTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="manager", password="pass")
        AccountProfile.objects.create(user=self.user, role=AccountProfile.Role.MANAGER)

        self.collection = ProductCollection.objects.create(name="C1")
        self.set_obj = ProductSet.objects.create(collection=self.collection, name="S1")

        self.archived = Product.objects.create(
            name="Archived Name",
            set=self.set_obj,
            unit_primary=UnitType.PIECE,
            unit_secondary="",
            conversion_factor=None,
            cost=Decimal("1.0000"),
            price=Decimal("2.0000"),
            is_active=False,
        )

    def test_commit_stage_rejects_archived_product_update(self):
        with TemporaryDirectory() as tmpdir:
            with override_settings(MEDIA_ROOT=tmpdir):
                from catalog import import_engine as IE

                IE = importlib.reload(IE)
                IE._ensure_dirs()

                sid = "testsid"
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
                                "name": self.archived.name,
                                "set": self.set_obj.name,
                                "unit_primary": UnitType.PIECE,
                                "unit_secondary": "",
                                "conversion_factor": "",
                                "cost": "1.0000",
                                "price": "2.0000",
                                "stock_qty": "",
                                "barcodes_u1": [],
                                "barcodes_u2": [],
                                "unit_ids_u1": [],
                                "unit_ids_u2": [],
                                "notes": "",
                                "dup_action": "update",
                            },
                            "errors": {},
                        }
                    ],
                }

                with open(IE._p(sid, ".meta.json"), "w", encoding="utf-8") as f:
                    json.dump(meta, f)
                with open(IE._p(sid, ".stage.json"), "w", encoding="utf-8") as f:
                    json.dump(stage, f)

                with self.assertRaises(IE.StageError):
                    IE.commit_stage(sid, actor=self.user)

        self.archived.refresh_from_db()
        self.assertFalse(self.archived.is_active)
        self.assertEqual(Product.objects.filter(name=self.archived.name).count(), 1)
