from decimal import Decimal
from typing import Optional

from django.contrib.auth import get_user_model

from accounts.models import AccountProfile
from billing.models import Provider
from catalog.models import ProductCollection, ProductSet, Product, UnitType
from financials.models import Currency, MoneyContainer, MoneyContainerCurrency, ContainerFeature, FxSettings
from stock.models import ProductContainer


def create_user_with_role(username: str, role: str):
    User = get_user_model()
    user = User.objects.create_user(username=username, password="pass1234")
    AccountProfile.objects.create(user=user, role=role)
    return user


def ensure_currency(code: str, name: str = "") -> Currency:
    cur = Currency.objects.filter(code=code).first()
    if cur:
        return cur
    return Currency.objects.create(code=code, name=name or code, is_active=True)


def ensure_fx(rate: Decimal = Decimal("10000")) -> FxSettings:
    fx = FxSettings.objects.filter(is_active=True).first()
    if fx:
        return fx
    return FxSettings.objects.create(rate_syp_per_usd=rate, is_active=True)


def create_money_container(*, name: str, user, enable_syp=True, enable_usd=True, with_pos_feature=False) -> MoneyContainer:
    mc = MoneyContainer.objects.create(
        name=name,
        container_type=MoneyContainer.ContainerType.DRAWER,
        created_by=user,
        is_active=True,
        balance_syp=Decimal("0"),
        balance_usd=Decimal("0"),
    )
    if with_pos_feature:
        feat = ContainerFeature.objects.filter(code="pos_sales").first()
        if not feat:
            feat = ContainerFeature.objects.create(code="pos_sales", name="POS Sales", is_active=True)
        mc.features.add(feat)
    syp = ensure_currency("SYP", "SYP")
    usd = ensure_currency("USD", "USD")
    MoneyContainerCurrency.objects.get_or_create(container=mc, currency=syp, defaults={"is_enabled": bool(enable_syp)})
    MoneyContainerCurrency.objects.get_or_create(container=mc, currency=usd, defaults={"is_enabled": bool(enable_usd)})
    if enable_syp:
        MoneyContainerCurrency.objects.filter(container=mc, currency=syp).update(is_enabled=True)
    if enable_usd:
        MoneyContainerCurrency.objects.filter(container=mc, currency=usd).update(is_enabled=True)
    return mc


def create_stock_container(code: str, name: str, is_store=False) -> ProductContainer:
    if is_store:
        existing_store = ProductContainer.objects.filter(is_store=True).first()
        if existing_store:
            return existing_store
    existing = ProductContainer.objects.filter(code=code).first()
    if existing:
        if is_store and not existing.is_store:
            existing.is_store = True
            existing.save(update_fields=["is_store"])
        return existing
    return ProductContainer.objects.create(code=code, name=name, is_store=is_store, is_active=True)


def create_collection_set(name: str = "C1", set_name: str = "S1"):
    col = ProductCollection.objects.create(name=name)
    pset = ProductSet.objects.create(collection=col, name=set_name)
    return col, pset


def create_product(
    *,
    name: str,
    set_obj: ProductSet,
    unit_primary: str = UnitType.PIECE,
    unit_secondary: str = "",
    conversion_factor: Optional[Decimal] = None,
    cost: Decimal = Decimal("10.0000"),
    price: Decimal = Decimal("15.0000"),
):
    prod = Product(
        name=name,
        set=set_obj,
        unit_primary=unit_primary,
        unit_secondary=unit_secondary,
        conversion_factor=conversion_factor,
        default_cost_syp=cost,
        default_price_syp=price,
        allow_syp_sales=True,
        allow_syp_purchasing=True,
        allow_usd_sales=False,
        allow_usd_purchasing=False,
    )
    prod.save()
    return prod


def create_provider(name: str = "Prov A") -> Provider:
    return Provider.objects.create(name=name)
