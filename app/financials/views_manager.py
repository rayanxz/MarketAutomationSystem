from __future__ import annotations

from decimal import Decimal
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

from financials.models import MoneyContainer, Currency, PostingLine, PostingTargetType, Receipt, ReceiptStatus, ReceiptKind
from financials import services as FSV
from financials.forms import MoneyContainerForm, build_opening_formset


def _secondary_menu_ctx(active: str) -> Dict[str, Any]:
    return {"fin_active": active}


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
    currencies = list(Currency.objects.filter(is_active=True).order_by("code"))

    if request.method == "POST":
        form = MoneyContainerForm(request.POST)
        opening_forms = build_opening_formset(currencies=currencies, data=request.POST)

        ok = form.is_valid()
        for f in opening_forms:
            ok = ok and f.is_valid()

        if ok:
            container: MoneyContainer = form.save(commit=False)
            container.created_by = request.user
            container.save()

            # Create opening receipts (one per currency with amount != 0)
            # We use CASH_ADD to container with source pointing to container creation
            any_opening = False
            for f in opening_forms:
                cur_id = f.cleaned_data["currency_id"]
                amount = Decimal(f.cleaned_data.get("amount") or 0)
                if amount == 0:
                    continue
                cur = next(x for x in currencies if x.id == cur_id)

                if amount > 0:
                    FSV.post_cash_add(
                        actor=request.user,
                        container_id=container.id,
                        currency_code=cur.code,
                        amount=amount,
                        note="رصيد افتتاحي",
                        source_app="financials",
                        source_model="MoneyContainer",
                        source_id=str(container.id),
                    )
                else:
                    # allow negative opening (rare, but you said containers can be negative)
                    FSV.post_cash_withdraw(
                        actor=request.user,
                        container_id=container.id,
                        currency_code=cur.code,
                        amount=abs(amount),
                        note="رصيد افتتاحي (سالب)",
                        source_app="financials",
                        source_model="MoneyContainer",
                        source_id=str(container.id),
                    )

                any_opening = True

            messages.success(request, "تم إنشاء الحاوية بنجاح." + (" (مع رصيد افتتاحي)" if any_opening else ""))
            return redirect("financials:container_list")

        messages.error(request, "في أخطاء بالنموذج. راجع القيم وحاول مرة ثانية.")
    else:
        form = MoneyContainerForm()
        opening_forms = build_opening_formset(currencies=currencies, data=None)

    ctx = {
        **_secondary_menu_ctx("create"),
        "form": form,
        "currencies": currencies,
        "opening_forms": opening_forms,
    }
    return render(request, "financials/manager/container_form.html", ctx)


@login_required
@role_required(AccountProfile.Role.MANAGER)
@transaction.atomic
def container_edit(request: HttpRequest, container_id: int) -> HttpResponse:
    container = get_object_or_404(MoneyContainer, pk=container_id)

    if request.method == "POST":
        form = MoneyContainerForm(request.POST, instance=container)
        if form.is_valid():
            form.save()
            messages.success(request, "تم تعديل الحاوية بنجاح.")
            return redirect("financials:container_list")
        messages.error(request, "في أخطاء بالنموذج.")
    else:
        form = MoneyContainerForm(instance=container)

    balances = FSV.container_balance(container_id=container.id)
    ctx = {
        **_secondary_menu_ctx("list"),
        "form": form,
        "container": container,
        "balances": balances,
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
