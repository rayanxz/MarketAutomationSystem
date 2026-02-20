from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum

from inventory.models import q3
from inventory.models import ProductMovement
from billing.models import BillItem, ProviderReturnItem
from pos.models import SalesBillRow, SalesReturnRow
from stock.models import StockEntry, StockFifoLayer, ProductContainer
from catalog.models import ProductBarcode, ProductUnitId, Product

DEC0 = Decimal("0.000")
DEFAULT_CONTAINER_CODES: tuple[str, ...] = ("store", "wh1", "wh2")


class ProductDeletionPolicyError(ValidationError):
    """Raised when a product disable/reactivate/hard-delete policy is violated."""


class ProductDisableBlockedError(ProductDeletionPolicyError):
    def __init__(self, *, stock_by_container: dict[str, Decimal]):
        self.stock_by_container = stock_by_container
        super().__init__("Cannot disable product while stock is not zero in all containers.")


class ProductHardDeleteBlockedError(ProductDeletionPolicyError):
    def __init__(
        self,
        *,
        has_history: bool,
        stock_by_container: dict[str, Decimal],
    ):
        self.has_history = has_history
        self.stock_by_container = stock_by_container
        super().__init__("Hard delete is not allowed for this product.")


def stock_by_container(
    product: Product,
    *,
    container_codes: tuple[str, ...] | None = None,
) -> dict[str, Decimal]:
    codes = container_codes or DEFAULT_CONTAINER_CODES
    containers = list(ProductContainer.objects.filter(code__in=codes).only("id", "code"))
    by_code: dict[str, Decimal] = {code: DEC0 for code in codes}

    for c in containers:
        agg = (
            StockFifoLayer.objects
            .filter(product=product, container_id=c.id)
            .aggregate(s=Sum("qty_remaining"))
        )
        by_code[c.code] = q3(agg["s"] or DEC0)

    return by_code


def all_zero_stock(
    product: Product,
    *,
    container_codes: tuple[str, ...] | None = None,
) -> bool:
    by_code = stock_by_container(product, container_codes=container_codes)
    return all(q3(qty) == DEC0 for qty in by_code.values())


def has_any_history(product: Product) -> bool:
    if ProductMovement.objects.filter(product=product).exists():
        return True
    if BillItem.objects.filter(product=product).exists():
        return True
    if ProviderReturnItem.objects.filter(product=product).exists():
        return True
    if SalesBillRow.objects.filter(product_id=product.id).exists():
        return True
    if SalesReturnRow.objects.filter(product=product).exists():
        return True
    if StockEntry.objects.filter(product=product).exists():
        return True
    if StockFifoLayer.objects.filter(product=product).exists():
        return True
    return False


def can_hard_delete(product: Product) -> bool:
    return (not has_any_history(product)) and all_zero_stock(product)


def can_soft_delete(product: Product) -> bool:
    return all_zero_stock(product)


@transaction.atomic
def disable_product(
    product: Product,
    *,
    container_codes: tuple[str, ...] | None = None,
) -> tuple[Product, dict[str, Decimal]]:
    locked = Product.objects.select_for_update().get(pk=product.pk)
    stock_map = stock_by_container(locked, container_codes=container_codes)
    if not all(q3(v) == DEC0 for v in stock_map.values()):
        raise ProductDisableBlockedError(stock_by_container=stock_map)

    if not locked.is_active:
        return locked, stock_map

    locked.is_active = False
    locked.save(update_fields=["is_active"])
    sync_identifiers_for_product(locked, is_active=False)
    return locked, stock_map


@transaction.atomic
def reactivate_product(product: Product) -> Product:
    locked = Product.objects.select_for_update().get(pk=product.pk)
    if locked.is_active:
        return locked
    locked.is_active = True
    locked.save(update_fields=["is_active"])
    sync_identifiers_for_product(locked, is_active=True)
    return locked


@transaction.atomic
def hard_delete_product(
    product: Product,
    *,
    container_codes: tuple[str, ...] | None = None,
) -> tuple[dict[str, Decimal], bool]:
    locked = Product.objects.select_for_update().get(pk=product.pk)
    stock_map = stock_by_container(locked, container_codes=container_codes)
    history = has_any_history(locked)
    all_zero = all(q3(v) == DEC0 for v in stock_map.values())
    if history or (not all_zero):
        raise ProductHardDeleteBlockedError(
            has_history=history,
            stock_by_container=stock_map,
        )
    locked.delete()
    return stock_map, history


def sync_identifiers_for_product(product: Product, *, is_active: bool | None = None) -> None:
    active = product.is_active if is_active is None else bool(is_active)
    ProductBarcode.objects.filter(product=product).update(is_active=active)
    ProductUnitId.objects.filter(product=product).update(is_active=active)


def sync_identifiers_for_products(qs, *, is_active: bool) -> None:
    ProductBarcode.objects.filter(product__in=qs).update(is_active=is_active)
    ProductUnitId.objects.filter(product__in=qs).update(is_active=is_active)
