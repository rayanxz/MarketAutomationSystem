# billing/tests/test_fully_paid_financials_effect.py
from __future__ import annotations

from decimal import Decimal
from typing import Optional, Any, Dict

from django.test import TestCase
from django.contrib.auth import get_user_model
from django.core.exceptions import FieldDoesNotExist

from billing.models import Bill, BillItem, Provider
from catalog.models import Product
from inventory.models import ProductMovement, DEC0
from financials.models import MoneyContainer, Receipt, ReceiptStatus

from billing import services as BillingSV


# ---------- helpers ----------
def q3(x: Optional[Decimal]) -> Decimal:
    try:
        return (x or Decimal("0")).quantize(Decimal("0.001"))
    except Exception:
        return Decimal("0.000")


def _safe_getattr(obj: Any, name: str, default=None):
    return getattr(obj, name, default)


def _model_has_field(model, field_name: str) -> bool:
    try:
        model._meta.get_field(field_name)
        return True
    except FieldDoesNotExist:
        return False


def _money_ref_field() -> str:
    # Your error list shows ref_code exists, code doesn't.
    if _model_has_field(MoneyContainer, "ref_code"):
        return "ref_code"
    if _model_has_field(MoneyContainer, "code"):
        return "code"
    raise AssertionError("MoneyContainer has neither ref_code nor code. Add detection here.")


def _money_container_by_ref(ref: str) -> MoneyContainer:
    key = _money_ref_field()
    mc = MoneyContainer.objects.filter(**{key: ref}).first()
    assert mc is not None, f"MoneyContainer with {key}={ref!r} not found."
    return mc


def _ensure_money_container(ref: str, name: str, actor) -> MoneyContainer:
    key = _money_ref_field()
    mc = MoneyContainer.objects.filter(**{key: ref}).first()
    if mc:
        return mc

    create_kwargs = {key: ref, "name": name}

    if _model_has_field(MoneyContainer, "created_by"):
        create_kwargs["created_by"] = actor
    elif _model_has_field(MoneyContainer, "created_by_id"):
        create_kwargs["created_by_id"] = actor.id

    if _model_has_field(MoneyContainer, "is_active"):
        create_kwargs["is_active"] = True
    if _model_has_field(MoneyContainer, "note"):
        create_kwargs["note"] = "auto-created by test"

    return MoneyContainer.objects.create(**create_kwargs)



def _create_min_product() -> Product:
    data: Dict[str, Any] = {"name": "Test Product"}
    for k, v in [
        ("cost", Decimal("10")),
        ("price", Decimal("15")),
        ("stock_qty", Decimal("0")),
    ]:
        if _model_has_field(Product, k):
            data[k] = v
    return Product.objects.create(**data)


def _create_min_provider() -> Provider:
    data: Dict[str, Any] = {"name": "Test Provider"}
    return Provider.objects.create(**data)


class FullyPaidPurchaseBillFinancialsEffectTests(TestCase):
    """
    Fully paid purchase bill should:
      - create at least one POSTED Receipt linked to Bill
      - receipt should touch the chosen MoneyContainer (CASH-01)
      - create ProductMovements tied to BillItem rows
    """

    CASH_REF = "CASH-01"

    def setUp(self):
        User = get_user_model()
        self.actor = User.objects.create_user(username="tester", password="pw123456")

        # Ensure container exists using ref_code/code auto-detection
        self.cash = _ensure_money_container(self.CASH_REF, "صندوق #1", actor=self.actor)
        self.provider = _create_min_provider()
        self.product = _create_min_product()

    def test_fully_paid_purchase_bill_creates_posted_receipt_and_movements(self):
        qty = Decimal("2")
        cost = Decimal("7000")
        total = qty * cost  # 14000

        # ---- service call: adjust only if your signature differs ----
        bill_id = BillingSV.create_bill(
            actor=self.actor,
            provider_id=self.provider.id,
            money_container_id=self.cash.id,
            paid_amount=str(total),
            items=[
                {
                    "product_id": self.product.id,
                    "unit_index": 1,
                    "qty_primary": str(qty),
                    "cost": str(cost),
                }
            ],
        )
        # ------------------------------------------------------------

        bill = Bill.objects.get(id=bill_id)
        self.assertGreater(q3(bill.total), DEC0)

        # -------- receipts --------
        rqs = Receipt.objects.filter(
            source_app="billing",
            source_model="Bill",
            source_id=str(bill.id),
        ).order_by("id")

        self.assertGreater(rqs.count(), 0, "Expected at least 1 receipt for fully paid bill.")

        posted = rqs.filter(status=ReceiptStatus.POSTED)
        self.assertGreater(posted.count(), 0, "Expected at least 1 POSTED receipt.")

        # touches cash container
        self.assertGreater(
            rqs.filter(container_id=self.cash.id).count(),
            0,
            f"Expected receipt touching MoneyContainer id={self.cash.id} ({self.CASH_REF}).",
        )

        # Optional sign check IF your schema has amount_signed (don’t explode if not)
        if _model_has_field(Receipt, "amount_signed"):
            self.assertTrue(
                rqs.filter(amount_signed__lt=DEC0).exists()
                or rqs.filter(amount_signed__gt=DEC0).exists(),
                "Receipts exist but amount_signed never set? (Check posting logic)",
            )

        # -------- product movements (BillItem-tied) --------
        items = list(BillItem.objects.filter(bill=bill).order_by("id"))
        self.assertGreater(len(items), 0, "BillItem rows not created.")

        item_ids = [str(it.id) for it in items]

        mvs = ProductMovement.objects.filter(
            source_app="billing",
            source_model="BillItem",
            source_id__in=item_ids,
        ).order_by("id")

        self.assertGreater(mvs.count(), 0, "Expected ProductMovements tied to BillItems.")

        # purchase should increase stock -> qty_primary > 0
        for mv in mvs:
            self.assertGreater(q3(mv.qty_primary), DEC0, f"mv id={mv.id} has non-positive qty.")

        mv_sum = q3(sum((mv.qty_primary or DEC0) for mv in mvs), DEC0)
        it_sum = q3(sum((it.qty_primary or DEC0) for it in items), DEC0)
        self.assertEqual(mv_sum, it_sum, f"Movement sum {mv_sum} must equal BillItem sum {it_sum}.")
