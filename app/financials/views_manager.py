from __future__ import annotations

from decimal import Decimal
import logging
from typing import Dict, Any, List, Optional

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Prefetch
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from accounts.decorators import role_required
from accounts.models import AccountProfile

from financials.models import (
    MoneyContainer,
    Currency,
    PostingLine,
    PostingTargetType,
    Receipt,
    ReceiptStatus,
    ReceiptKind,
    FxSettings,
    ContainerFeature,
)
from financials import services as FSV
from financials.forms import MoneyContainerForm, build_opening_formset, FxSettingsForm

from financials.models import MoneyContainerCurrency 

from django.db import IntegrityError


logger = logging.getLogger(__name__)


def _secondary_menu_ctx(active: str) -> Dict[str, Any]:
    return {"fin_active": active}


def _allowed_users_columns(form: MoneyContainerForm) -> Dict[str, Any]:
    selected_raw = form["allowed_users"].value() or []
    if not isinstance(selected_raw, (list, tuple)):
        selected_raw = [selected_raw]

    selected_ids: set[int] = set()
    for value in selected_raw:
        try:
            selected_ids.add(int(value))
        except (TypeError, ValueError):
            continue

    users = list(form.fields["allowed_users"].queryset.select_related("account_profile"))
    cashiers: list[dict[str, Any]] = []
    managers: list[dict[str, Any]] = []
    owner_profile = (
        AccountProfile.objects.select_related("user")
        .filter(role=AccountProfile.Role.OWNER)
        .order_by("id")
        .first()
    )
    owner_name = ""
    if owner_profile and owner_profile.user_id:
        owner_name = owner_profile.user.get_full_name() or owner_profile.user.username

    for user in users:
        profile = getattr(user, "account_profile", None)
        role = getattr(profile, "role", "")
        row = {
            "id": user.id,
            "label": (user.get_full_name() or user.username),
            "checked": user.id in selected_ids,
        }
        if role == AccountProfile.Role.CASHIER:
            cashiers.append(row)
        else:
            # Non-cashier selectable users are shown with managers.
            managers.append(row)

    return {
        "allowed_cashier_users": cashiers,
        "allowed_manager_users": managers,
        "owner_account": {
            "name": owner_name,
            "exists": bool(owner_profile),
        },
    }


@login_required
@role_required(AccountProfile.Role.MANAGER)
@transaction.atomic
def fx_settings(request: HttpRequest) -> HttpResponse:
    current = (
        FxSettings.objects
        .filter(is_active=True)
        .order_by("-updated_at", "-id")
        .first()
    )

    if request.method == "POST":
        form = FxSettingsForm(request.POST, instance=current)
        if form.is_valid():
            rate = form.cleaned_data["rate_syp_per_usd"]
            FSV.set_current_fx(actor=request.user, rate_syp_per_usd=rate)
            messages.success(request, "تم تحديث سعر الصرف وسيتم تطبيقه على كل الحركات القادمة.")
            return redirect("financials:fx_settings")
        messages.error(request, "في خطأ بقيمة سعر الصرف.")
    else:
        form = FxSettingsForm(instance=current)

    ctx = {
        **_secondary_menu_ctx("fx"),
        "form": form,
        "current": current,
    }
    return render(request, "financials/manager/fx_settings.html", ctx)


@login_required
@role_required(AccountProfile.Role.MANAGER)
def container_list(request: HttpRequest) -> HttpResponse:
    containers = list(MoneyContainer.objects.all().order_by("name"))
    currencies = list(Currency.objects.filter(is_active=True).order_by("code"))

    # balances per container
    balances: Dict[int, Dict[str, Decimal]] = {}
    for c in containers:
        balances[c.id] = FSV.container_balance(container_id=c.id)

    ctx = {
        **_secondary_menu_ctx("list"),
        "containers": containers,
        "currencies": currencies,
        "balances": balances,
    }
    return render(request, "financials/manager/container_list.html", ctx)


@login_required
@role_required(AccountProfile.Role.MANAGER)
@transaction.atomic
def container_create(request: HttpRequest) -> HttpResponse:
    all_currencies = list(Currency.objects.filter(is_active=True).order_by("code"))

    if request.method == "POST":
        form = MoneyContainerForm(request.POST)
        opening_forms = build_opening_formset(currencies=all_currencies, data=request.POST)

        ok = form.is_valid()
        for f in opening_forms:
            ok = ok and f.is_valid()

        if not ok:
            messages.error(request, "في أخطاء بالنموذج. راجع القيم وحاول مرة ثانية.")
            account_columns = _allowed_users_columns(form)
            return render(
                request,
                "financials/manager/container_form.html",
                {
                    "fin_active": "create",
                    "form": form,
                    "currencies": all_currencies,
                    "opening_forms": opening_forms,
                    **account_columns,
                },
            )

        # ===== create container (ref_code allocated safely) =====
        container: MoneyContainer = form.save(commit=False)
        container.created_by = request.user

        # allocate readable ref_code (CASH-01 / SAFE-01 / BANK-01 style)
        # retry a few times in case of rare race
        for _ in range(5):
            try:
                container.ref_code = FSV.alloc_ref_code(container_type=container.container_type)
                container.save()
                break
            except IntegrityError:
                continue
        else:
            # if we failed 5 times, something is wrong
            raise IntegrityError("Failed to allocate unique ref_code for MoneyContainer")

        # ===== ensure per-currency state rows exist =====
        FSV.ensure_currency_states(container=container)

        # ===== enable/disable currencies based on form =====
        selected_currencies = list(form.cleaned_data["currencies"])
        selected_ids = {c.id for c in selected_currencies}
        selected_codes = {c.code for c in selected_currencies}

        # disable all then enable selected (single source of truth)
        MoneyContainerCurrency.objects.filter(container=container).update(is_enabled=False)
        MoneyContainerCurrency.objects.filter(container=container, currency_id__in=selected_ids).update(is_enabled=True)

        # ===== M2M =====
        container.features.set(form.cleaned_data.get("features"))
        container.allowed_users.set(form.cleaned_data.get("allowed_users"))

        # ===== Opening balance (backend safety) =====
        # even if user posts amounts for unchecked currencies, we IGNORE them
        amounts: Dict[str, Decimal] = {}
        raw_amounts: Dict[str, Decimal] = {}
        quantized_amounts: Dict[str, Decimal] = {}
        any_nonzero = False
        currency_by_code = {c.code: c for c in all_currencies}

        for f in opening_forms:
            code = f.cleaned_data["currency_code"]
            amt = Decimal(f.cleaned_data.get("amount") or 0)
            raw_amounts[code] = amt

            # ✅ extra safety: ignore unchecked currency amounts
            if code not in selected_codes:
                amt = Decimal("0")

            quantized = FSV.q_currency(amt, currency=currency_by_code[code])
            quantized_amounts[code] = quantized
            if quantized != 0:
                any_nonzero = True

            amounts[code] = quantized

        logger.info(
            "container_create opening amounts raw=%s quantized=%s amounts_for_post=%s any_nonzero=%s selected_codes=%s",
            raw_amounts,
            quantized_amounts,
            amounts,
            any_nonzero,
            sorted(selected_codes),
        )

        if any_nonzero:
            # post_initial_balance will also block disabled currencies,
            # but we already zeroed unchecked ones anyway.
            FSV.post_initial_balance(
                actor=request.user,
                container_id=container.id,
                amounts_by_code=amounts,
                note="رصيد افتتاحي",
            )

        messages.success(request, "تم إنشاء الحاوية بنجاح.")
        return redirect("financials:container_list")

    # ===== GET =====
    # default: check the 2 main currencies if they exist, otherwise check all
    preferred_codes = {"SYP", "USD"}
    initial_currency_ids = [c.id for c in all_currencies if c.code in preferred_codes]
    if not initial_currency_ids:
        initial_currency_ids = [c.id for c in all_currencies]
    initial_feature_ids = list(
        ContainerFeature.objects.filter(is_active=True).values_list("id", flat=True)
    )

    form = MoneyContainerForm(
        initial={
            "is_active": True,
            "currencies": initial_currency_ids,
            "container_type": MoneyContainer.ContainerType.DRAWER,
            "features": initial_feature_ids,
        }
    )

    # opening forms should show ALL currencies (UI will disable per checkbox via JS),
    # backend will ignore unchecked anyway.
    opening_forms = build_opening_formset(currencies=all_currencies, data=None)

    ctx = {
        "fin_active": "create",
        "form": form,
        "currencies": all_currencies,
        "opening_forms": opening_forms,
        **_allowed_users_columns(form),
    }
    return render(request, "financials/manager/container_form.html", ctx)

@login_required
@role_required(AccountProfile.Role.MANAGER)
@transaction.atomic
def container_edit(request: HttpRequest, container_id: int) -> HttpResponse:
    container = get_object_or_404(MoneyContainer, pk=container_id)

    # always ensure currency state rows exist
    FSV.ensure_currency_states(container=container)

    if request.method == "POST":
        form = MoneyContainerForm(request.POST, instance=container)

        # ✅ MUST be before is_valid()
        form.fields["ref_code"].disabled = True
        form.fields["container_type"].disabled = True

        if form.is_valid():
            # ✅ safer: preserve ref + type even if something slips through
            obj: MoneyContainer = form.save(commit=False)
            obj.ref_code = container.ref_code
            obj.container_type = container.container_type
            obj.save()

            # m2m
            obj.features.set(form.cleaned_data.get("features"))
            obj.allowed_users.set(form.cleaned_data.get("allowed_users"))

            # currency enable/disable
            selected_ids = set(form.cleaned_data["currencies"].values_list("id", flat=True))

            MoneyContainerCurrency.objects.filter(container=obj).update(is_enabled=False)
            MoneyContainerCurrency.objects.filter(container=obj, currency_id__in=selected_ids).update(is_enabled=True)

            messages.success(request, "تم تعديل الحاوية بنجاح.")
            return redirect("financials:container_list")

        messages.error(request, "في أخطاء بالنموذج.")

    else:
        enabled_ids = list(
            MoneyContainerCurrency.objects
            .filter(container=container, is_enabled=True, currency__is_active=True)
            .values_list("currency_id", flat=True)
        )

        form = MoneyContainerForm(instance=container, initial={
            "currencies": enabled_ids,
            # ✅ these 2 lines are the fix:
            "features": list(container.features.values_list("id", flat=True)),
            "allowed_users": list(container.allowed_users.values_list("id", flat=True)),
        })

        form.fields["ref_code"].disabled = True
        form.fields["container_type"].disabled = True


    ctx = {
        **_secondary_menu_ctx("list"),
        "form": form,
        "container": container,
        **_allowed_users_columns(form),
    }
    return render(request, "financials/manager/container_edit.html", ctx)

@login_required
@role_required(AccountProfile.Role.MANAGER)
def container_movements(request: HttpRequest) -> HttpResponse:
    """
    Shows PostingLine rows with filters.
    """
    container_id = request.GET.get("container") or ""
    currency_code = request.GET.get("currency") or ""
    kind = request.GET.get("kind") or ""
    status = request.GET.get("status") or ""
    actor = request.GET.get("actor") or ""
    date_from = request.GET.get("from") or ""
    date_to = request.GET.get("to") or ""
    target_type = request.GET.get("target") or ""  # container/counterparty or empty

    qs = (
        PostingLine.objects
        .select_related("receipt", "receipt__actor", "currency", "container", "counterparty")
        .all()
        .order_by("-id")
    )

    # Default: show effective ledger (POSTED + REVERSED), hide drafts/void unless asked
    if status:
        qs = qs.filter(receipt__status=status)
    else:
        qs = qs.filter(receipt__status__in=[ReceiptStatus.POSTED, ReceiptStatus.REVERSED])

    if kind:
        qs = qs.filter(receipt__kind=kind)

    if target_type:
        qs = qs.filter(target_type=target_type)

    if container_id:
        qs = qs.filter(container_id=int(container_id))

    if currency_code:
        qs = qs.filter(currency__code=currency_code)

    if actor:
        # allow filtering by username substring
        qs = qs.filter(receipt__actor__username__icontains=actor)

    if date_from:
        qs = qs.filter(receipt__created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(receipt__created_at__date__lte=date_to)

    containers = list(MoneyContainer.objects.all().order_by("name"))
    currencies = list(Currency.objects.filter(is_active=True).order_by("code"))

    ctx = {
        **_secondary_menu_ctx("movements"),
        "rows": qs[:500],  # keep it fast; paginate later
        "containers": containers,
        "currencies": currencies,
        "kinds": ReceiptKind.choices,
        "statuses": ReceiptStatus.choices,
        "target_types": PostingTargetType.choices,
        "filters": {
            "container": container_id,
            "currency": currency_code,
            "kind": kind,
            "status": status,
            "actor": actor,
            "from": date_from,
            "to": date_to,
            "target": target_type,
        },
    }
    return render(request, "financials/manager/container_movements.html", ctx)


@login_required
@role_required(AccountProfile.Role.MANAGER)
def container_manual_events(request: HttpRequest) -> HttpResponse:
    containers = list(MoneyContainer.objects.all().order_by("name"))
    currencies = list(Currency.objects.filter(is_active=True).order_by("code"))
    fx = (
        FxSettings.objects.filter(is_active=True)
        .order_by("-updated_at", "-id")
        .first()
    )

    receipts = (
        Receipt.objects
        .filter(source_app="financials", source_model="ManualContainerEvent")
        .prefetch_related("lines", "lines__container", "lines__currency", "actor")
        .order_by("-id")[:200]
    )
    rows = []
    for r in receipts:
        from_ln = None
        to_ln = None
        for ln in r.lines.all():
            if ln.target_type != PostingTargetType.CONTAINER:
                continue
            if ln.amount < 0 and from_ln is None:
                from_ln = ln
            elif ln.amount > 0 and to_ln is None:
                to_ln = ln
        rows.append({"receipt": r, "from_line": from_ln, "to_line": to_ln})

    ctx = {
        **_secondary_menu_ctx("manual"),
        "containers": containers,
        "currencies": currencies,
        "fx_rate": fx.rate_syp_per_usd if fx else None,
        "rows": rows,
    }
    return render(request, "financials/manager/container_manual_events.html", ctx)
