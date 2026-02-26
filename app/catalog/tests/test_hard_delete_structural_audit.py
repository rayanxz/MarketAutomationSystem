from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.db.models.signals import pre_delete
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from accounts.models import AccountProfile
from catalog.models import Product, ProductCollection, ProductSet, UnitType
from catalog.services.deletion_policy import can_hard_delete_collection, hard_delete_product
from catalog import signals as catalog_signals
from inventory.models import ProductMovement
from pos.models import SalesBill, SalesBillRow


class HardDeleteStructuralAuditTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="manager", password="pass")
        AccountProfile.objects.create(user=self.user, role=AccountProfile.Role.MANAGER)
        self.client.force_login(self.user)

    def _create_collection(self, name: str) -> ProductCollection:
        return ProductCollection.objects.create(name=name)

    def _create_set(self, *, col: ProductCollection, name: str) -> ProductSet:
        return ProductSet.objects.create(collection=col, name=name)

    def _create_product(self, *, st: ProductSet, name: str) -> Product:
        return Product.objects.create(
            name=name,
            set=st,
            unit_primary=UnitType.PIECE,
            unit_secondary="",
            conversion_factor=None,
        )

    def _movement(self, *, p: Product, source_id: str) -> ProductMovement:
        return ProductMovement.objects.create(
            product=p,
            qty_primary=Decimal("0.000"),
            unit_index=1,
            unit_cost=Decimal("0.0000"),
            total_cost=Decimal("0.000"),
            movement_type=ProductMovement.MovementType.ADJUSTMENT,
            source_app="tests",
            source_model="HardDeleteStructuralAuditTests",
            source_id=source_id,
        )

    def test_signal_propagation_scope_and_non_crash_edges(self):
        col_a = self._create_collection("AUD-SIG-C-A")
        set_a = self._create_set(col=col_a, name="AUD-SIG-S-A")
        p_a = self._create_product(st=set_a, name="AUD-SIG-P-A")

        col_b = self._create_collection("AUD-SIG-C-B")
        set_b = self._create_set(col=col_b, name="AUD-SIG-S-B")
        p_b = self._create_product(st=set_b, name="AUD-SIG-P-B")

        self.assertTrue(set_a.can_be_hard_deleted)
        self.assertTrue(col_a.can_be_hard_deleted)
        self.assertTrue(set_b.can_be_hard_deleted)
        self.assertTrue(col_b.can_be_hard_deleted)

        with patch(
            "catalog.signals.mark_hierarchy_non_hard_deletable",
            wraps=catalog_signals.mark_hierarchy_non_hard_deletable,
        ) as marker:
            mv = self._movement(p=p_a, source_id="sig-1")
            self.assertEqual(marker.call_count, 1)
            self.assertEqual(marker.call_args[0][0], p_a.id)

            mv.source_id = "sig-1-upd"
            mv.save(update_fields=["source_id"])
            self.assertEqual(marker.call_count, 1)

        set_a.refresh_from_db()
        col_a.refresh_from_db()
        set_b.refresh_from_db()
        col_b.refresh_from_db()
        self.assertFalse(set_a.can_be_hard_deleted)
        self.assertFalse(col_a.can_be_hard_deleted)
        self.assertTrue(set_b.can_be_hard_deleted)
        self.assertTrue(col_b.can_be_hard_deleted)

        bill = SalesBill.objects.create()
        SalesBillRow.objects.create(
            bill=bill,
            product_id=999999999,
            product_name="orphan-product-id",
            qty=Decimal("1.000"),
            uom_index=1,
            unit_price=Decimal("1.000"),
            sale_currency="SYP",
        )

    def test_collection_hard_delete_order_and_integrity(self):
        col = self._create_collection("AUD-ORD-C")
        st = self._create_set(col=col, name="AUD-ORD-S")
        p1 = self._create_product(st=st, name="AUD-ORD-P1")
        p2 = self._create_product(st=st, name="AUD-ORD-P2")

        delete_order: list[str] = []

        def on_product_delete(sender, instance, **kwargs):
            delete_order.append(f"product:{instance.id}")

        def on_set_delete(sender, instance, **kwargs):
            delete_order.append(f"set:{instance.id}")

        def on_collection_delete(sender, instance, **kwargs):
            delete_order.append(f"collection:{instance.id}")

        pre_delete.connect(on_product_delete, sender=Product, weak=False, dispatch_uid="audit-prod-del")
        pre_delete.connect(on_set_delete, sender=ProductSet, weak=False, dispatch_uid="audit-set-del")
        pre_delete.connect(on_collection_delete, sender=ProductCollection, weak=False, dispatch_uid="audit-col-del")
        try:
            resp = self.client.post(reverse("api_collection_cascade_delete", kwargs={"pk": col.id}))
        finally:
            pre_delete.disconnect(sender=Product, dispatch_uid="audit-prod-del")
            pre_delete.disconnect(sender=ProductSet, dispatch_uid="audit-set-del")
            pre_delete.disconnect(sender=ProductCollection, dispatch_uid="audit-col-del")

        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Product.objects.filter(id__in=[p1.id, p2.id]).exists())
        self.assertFalse(ProductSet.objects.filter(id=st.id).exists())
        self.assertFalse(ProductCollection.objects.filter(id=col.id).exists())
        self.assertEqual(Product.objects.filter(set_id=st.id).count(), 0)
        self.assertEqual(ProductSet.objects.filter(collection_id=col.id).count(), 0)

        first_non_product = next((x for x in delete_order if not x.startswith("product:")), "")
        self.assertTrue(first_non_product.startswith("set:"))
        self.assertTrue(delete_order[-1].startswith("collection:"))

    def test_mixed_case_collection_delete_fails_atomically(self):
        col = self._create_collection("AUD-MIX-C")
        st = self._create_set(col=col, name="AUD-MIX-S")
        p1 = self._create_product(st=st, name="AUD-MIX-P1")
        p2 = self._create_product(st=st, name="AUD-MIX-P2")
        p3 = self._create_product(st=st, name="AUD-MIX-P3")
        self._movement(p=p1, source_id="mix-1")

        resp = self.client.post(reverse("api_collection_cascade_delete", kwargs={"pk": col.id}))
        self.assertEqual(resp.status_code, 400)
        self.assertTrue(ProductCollection.objects.filter(id=col.id).exists())
        self.assertTrue(ProductSet.objects.filter(id=st.id).exists())
        self.assertTrue(Product.objects.filter(id=p1.id).exists())
        self.assertTrue(Product.objects.filter(id=p2.id).exists())
        self.assertTrue(Product.objects.filter(id=p3.id).exists())

    def test_backend_bypass_blocks_when_flags_false(self):
        col = self._create_collection("AUD-BYP-C")
        st = self._create_set(col=col, name="AUD-BYP-S")

        ProductSet.objects.filter(id=st.id).update(can_be_hard_deleted=False)
        ProductCollection.objects.filter(id=col.id).update(can_be_hard_deleted=False)

        resp_set = self.client.post(
            reverse("edit_apply_batch"),
            data=json.dumps({"ops": [{"op": "delete", "type": "set", "id": st.id}]}),
            content_type="application/json",
        )
        self.assertEqual(resp_set.status_code, 200)
        self.assertFalse(resp_set.json()["results"][0]["ok"])

        resp_col = self.client.post(reverse("api_collection_cascade_delete", kwargs={"pk": col.id}))
        self.assertEqual(resp_col.status_code, 400)
        self.assertFalse(resp_col.json()["ok"])

    def test_permanent_flag_behavior_after_other_product_hard_delete(self):
        col = self._create_collection("AUD-PERM-C")
        st = self._create_set(col=col, name="AUD-PERM-S")
        p_with_history = self._create_product(st=st, name="AUD-PERM-P1")
        p_clean = self._create_product(st=st, name="AUD-PERM-P2")

        self._movement(p=p_with_history, source_id="perm-1")
        st.refresh_from_db()
        col.refresh_from_db()
        self.assertFalse(st.can_be_hard_deleted)
        self.assertFalse(col.can_be_hard_deleted)

        hard_delete_product(p_clean)
        st.refresh_from_db()
        col.refresh_from_db()
        self.assertFalse(st.can_be_hard_deleted)
        self.assertFalse(col.can_be_hard_deleted)

    def test_synthetic_policy_check_large_collection_query_shape(self):
        col = self._create_collection("AUD-PERF-C")
        st = self._create_set(col=col, name="AUD-PERF-S")
        for i in range(100):
            self._create_product(st=st, name=f"AUD-PERF-P{i}")

        with CaptureQueriesContext(connection) as ctx:
            allowed = can_hard_delete_collection(col)

        self.assertTrue(allowed)
        # Audit guardrail: should complete and stay within a bounded query budget.
        self.assertLess(len(ctx.captured_queries), 5000)


