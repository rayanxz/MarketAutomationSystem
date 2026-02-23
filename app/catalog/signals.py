from __future__ import annotations

from django.apps import apps
from django.db.models.signals import post_save
from django.dispatch import receiver

from catalog.services.deletion_policy import mark_hierarchy_non_hard_deletable


def _product_id_from_instance(instance) -> int | None:
    if hasattr(instance, "product_id"):
        try:
            return int(getattr(instance, "product_id"))
        except Exception:
            return None
    if hasattr(instance, "product") and getattr(instance, "product", None) is not None:
        try:
            return int(getattr(instance.product, "id"))
        except Exception:
            return None
    return None


def _mark_product_hierarchy(instance) -> None:
    product_id = _product_id_from_instance(instance)
    if product_id:
        mark_hierarchy_non_hard_deletable(product_id)


ProductMovement = apps.get_model("inventory", "ProductMovement")
BillItem = apps.get_model("billing", "BillItem")
ProviderReturnItem = apps.get_model("billing", "ProviderReturnItem")
SalesBillRow = apps.get_model("pos", "SalesBillRow")
SalesReturnRow = apps.get_model("pos", "SalesReturnRow")
StockEntry = apps.get_model("stock", "StockEntry")
StockFifoLayer = apps.get_model("stock", "StockFifoLayer")


@receiver(post_save, sender=ProductMovement)
@receiver(post_save, sender=BillItem)
@receiver(post_save, sender=ProviderReturnItem)
@receiver(post_save, sender=SalesBillRow)
@receiver(post_save, sender=SalesReturnRow)
@receiver(post_save, sender=StockEntry)
@receiver(post_save, sender=StockFifoLayer)
def mark_tree_as_not_hard_deletable_on_first_history(sender, instance, created, **kwargs):
    if not created:
        return
    _mark_product_hierarchy(instance)
