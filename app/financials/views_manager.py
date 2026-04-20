from __future__ import annotations

from decimal import Decimal
import logging
from typing import Dict, Any, List, Optional
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Prefetch, Q, Count
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from accounts.decorators import role_required
from accounts.models import AccountProfile

from financials.models import (
    MoneyContainer,
    Counterparty,
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
from core.date_filters import parse_filter_date


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

            # extra safety: ignore unchecked currency amounts
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

        # MUST be before is_valid()
        form.fields["ref_code"].disabled = True
        form.fields["container_type"].disabled = True

        if form.is_valid():
            # safer: preserve ref + type even if something slips through
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
            # these 2 lines are the fix:
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
    raw_date_from = (request.GET.get("from") or "").strip()
    raw_date_to = (request.GET.get("to") or "").strip()
    date_from = parse_filter_date(raw_date_from)
    date_to = parse_filter_date(raw_date_to)
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
            "from": raw_date_from,
            "to": raw_date_to,
            "target": target_type,
        },
    }
    return render(request, "financials/manager/container_movements.html", ctx)


@login_required
@role_required(AccountProfile.Role.MANAGER)
def container_manual_events(request: HttpRequest) -> HttpResponse:
    containers = list(
        FSV.money_containers_for_user_qs(user=request.user).order_by("name")
    )
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


def _source_document_url(*, source_app: str, source_model: str, source_id: str) -> str:
    app_code = (source_app or "").strip().lower()
    model_code = (source_model or "").strip()
    sid = (source_id or "").strip()
    if not sid:
        return ""
    targeted_public_ref = _resolve_target_public_source_id(
        source_app=source_app,
        source_model=source_model,
        source_id=sid,
    )
    try:
        if app_code == "billing" and model_code == "Bill":
            if not targeted_public_ref:
                return ""
            return reverse("billing_bill_view", kwargs={"bill_id": targeted_public_ref})
        if app_code == "billing" and model_code == "ProviderReturn":
            if not targeted_public_ref:
                return ""
            return reverse("billing_return_view", kwargs={"ret_id": targeted_public_ref})
        if app_code == "pos" and model_code == "SalesBill":
            if not targeted_public_ref:
                return ""
            return reverse("pos:pos_manager_bill_detail", kwargs={"bill_id": targeted_public_ref})
        if app_code == "pos" and model_code == "SalesReturn":
            sid_int = int(sid.split(":", 1)[0])
            return reverse("pos:pos_manager_sale_return_settle", kwargs={"return_id": sid_int})
    except Exception:
        return ""
    return ""


def _targeted_public_id_prefix(*, source_app: str, source_model: str) -> str:
    app_code = (source_app or "").strip().lower()
    model_code = (source_model or "").strip()
    if app_code == "billing" and model_code == "Bill":
        return "PB-"
    if app_code == "billing" and model_code == "ProviderReturn":
        return "PR-"
    if app_code == "pos" and model_code == "SalesBill":
        return "PS-"
    return ""


def _targeted_model_class(*, source_app: str, source_model: str):
    app_code = (source_app or "").strip().lower()
    model_code = (source_model or "").strip()
    if app_code == "billing" and model_code == "Bill":
        from billing.models import Bill

        return Bill
    if app_code == "billing" and model_code == "ProviderReturn":
        from billing.models import ProviderReturn

        return ProviderReturn
    if app_code == "pos" and model_code == "SalesBill":
        from pos.models import SalesBill

        return SalesBill
    return None


def _resolve_target_public_source_id(*, source_app: str, source_model: str, source_id: str) -> str:
    token = str(source_id or "").strip()
    prefix = _targeted_public_id_prefix(source_app=source_app, source_model=source_model)
    if not token or not prefix:
        return ""

    base = token.split(":", 1)[0].strip()
    if not base:
        return ""

    if base.upper().startswith(prefix):
        return base.upper()

    if not base.isdigit():
        return ""

    model_cls = _targeted_model_class(source_app=source_app, source_model=source_model)
    if model_cls is None:
        return ""
    obj = model_cls.objects.filter(pk=int(base)).only("public_id").first()
    if obj is None:
        return ""
    return str(getattr(obj, "public_id", "") or "").strip().upper()


def _resolve_target_internal_source_id(*, source_app: str, source_model: str, source_id: str) -> str:
    token = str(source_id or "").strip()
    prefix = _targeted_public_id_prefix(source_app=source_app, source_model=source_model)
    if not token or not prefix:
        return ""

    base = token.split(":", 1)[0].strip()
    if not base:
        return ""

    if base.isdigit():
        return str(int(base))

    if not base.upper().startswith(prefix):
        return ""

    model_cls = _targeted_model_class(source_app=source_app, source_model=source_model)
    if model_cls is None:
        return ""
    obj = model_cls.objects.filter(public_id__iexact=base).only("id").first()
    if obj is None:
        return ""
    return str(int(getattr(obj, "id", 0) or 0))


def _source_lookup_variants(
    *,
    source_app: str,
    source_model: str,
    source_id: str,
    allow_numeric_targeted: bool,
) -> list[str]:
    token = str(source_id or "").strip()
    if not token:
        return []

    prefix = _targeted_public_id_prefix(source_app=source_app, source_model=source_model)
    if not prefix:
        return [token]

    base = token.split(":", 1)[0].strip()
    if base.isdigit() and not allow_numeric_targeted:
        return []

    values: set[str] = set()
    public_ref = _resolve_target_public_source_id(
        source_app=source_app,
        source_model=source_model,
        source_id=token,
    )
    if public_ref:
        values.add(public_ref)

    internal_ref = _resolve_target_internal_source_id(
        source_app=source_app,
        source_model=source_model,
        source_id=token,
    )
    if internal_ref:
        values.add(internal_ref)

    return sorted(values)


def _source_identity_lookup_q_for_values(*, source_field: str, values: list[str]) -> Q:
    q = Q(pk__in=[])
    for raw in values:
        v = str(raw or "").strip()
        if not v:
            continue
        q |= Q(**{source_field: v})
        q |= Q(**{f"{source_field}__startswith": f"{v}:"})
    return q


def _preferred_source_ref_for_ui(*, source_app: str, source_model: str, source_id: str) -> str:
    raw = str(source_id or "").strip()
    if not raw:
        return ""

    prefix = _targeted_public_id_prefix(source_app=source_app, source_model=source_model)
    if not prefix:
        return raw

    if ":" in raw:
        base, suffix = raw.split(":", 1)
        mapped = _resolve_target_public_source_id(
            source_app=source_app,
            source_model=source_model,
            source_id=base,
        ) or base.strip()
        return f"{mapped}:{suffix}"

    mapped = _resolve_target_public_source_id(
        source_app=source_app,
        source_model=source_model,
        source_id=raw,
    )
    return mapped or raw


@login_required
@role_required(AccountProfile.Role.MANAGER)
def receipt_explorer(request: HttpRequest) -> HttpResponse:
    source_app = (request.GET.get("source_app") or "").strip()
    source_model = (request.GET.get("source_model") or "").strip()
    source_id = (request.GET.get("source_id") or "").strip()
    status = (request.GET.get("status") or "").strip()
    kind = (request.GET.get("kind") or "").strip()
    action_key = (request.GET.get("action_key") or "").strip()
    currency_code = (request.GET.get("currency") or "").strip().upper()
    container_id = (request.GET.get("container_id") or "").strip()
    counterparty_id = (request.GET.get("counterparty_id") or "").strip()
    raw_date_from = (request.GET.get("date_from") or "").strip()
    raw_date_to = (request.GET.get("date_to") or "").strip()
    date_from = parse_filter_date(raw_date_from)
    date_to = parse_filter_date(raw_date_to)

    qs = (
        Receipt.objects
        .select_related("actor")
        .prefetch_related("lines", "lines__currency", "lines__container", "lines__counterparty")
        .order_by("-id")
    )

    if source_app:
        qs = qs.filter(source_app=source_app)
    if source_model:
        qs = qs.filter(source_model=source_model)
    if source_id:
        if source_model:
            targeted_prefix = _targeted_public_id_prefix(
                source_app=source_app,
                source_model=source_model,
            )
            if targeted_prefix:
                lookup_values = _source_lookup_variants(
                    source_app=source_app,
                    source_model=source_model,
                    source_id=source_id,
                    allow_numeric_targeted=False,
                )
                if not lookup_values:
                    qs = qs.none()
                else:
                    qs = qs.filter(
                        _source_identity_lookup_q_for_values(
                            source_field="source_id",
                            values=lookup_values,
                        )
                    )
            else:
                qs = qs.filter(source_id=source_id)
        else:
            qs = qs.filter(source_id=source_id)
    if kind:
        qs = qs.filter(kind=kind)
    if action_key:
        qs = qs.filter(action_key=action_key)
    if status:
        qs = qs.filter(status=status)
    else:
        qs = qs.filter(status__in=[ReceiptStatus.POSTED, ReceiptStatus.REVERSED])
    if currency_code:
        qs = qs.filter(lines__currency__code=currency_code)
    if container_id:
        qs = qs.filter(lines__container_id=int(container_id))
    if counterparty_id:
        qs = qs.filter(lines__counterparty_id=int(counterparty_id))
    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)

    rows: list[dict[str, Any]] = []
    for r in qs.distinct()[:300]:
        lines = list(r.lines.all())
        line_rows = []
        for ln in lines:
            if ln.target_type == PostingTargetType.CONTAINER:
                target_label = f"حاوية: {getattr(ln.container, 'name', '—')}"
            else:
                cp_name = getattr(ln.counterparty, "name", "—")
                cp_type = getattr(ln.counterparty, "type", "")
                target_label = f"طرف مقابل: {cp_name} ({cp_type})"
            line_rows.append(
                {
                    "target_label": target_label,
                    "currency": getattr(ln.currency, "code", "—"),
                    "amount": ln.amount,
                }
            )

        doc_url = _source_document_url(
            source_app=r.source_app,
            source_model=r.source_model,
            source_id=r.source_id,
        )
        source_ref_display = _preferred_source_ref_for_ui(
            source_app=r.source_app,
            source_model=r.source_model,
            source_id=r.source_id,
        ) or str(r.source_id or "").strip()
        trace_url = (
            f"{reverse('financials:document_trace')}?{urlencode({'source_app': r.source_app, 'source_model': r.source_model, 'source_id': source_ref_display})}"
            if r.source_app and r.source_model and source_ref_display
            else ""
        )
        rows.append(
            {
                "receipt": r,
                "line_rows": line_rows,
                "doc_url": doc_url,
                "trace_url": trace_url,
                "source_ref_display": source_ref_display,
            }
        )

    ctx = {
        **_secondary_menu_ctx("receipt_explorer"),
        "rows": rows,
        "filters": {
            "source_app": source_app,
            "source_model": source_model,
            "source_id": source_id,
            "status": status,
            "kind": kind,
            "action_key": action_key,
            "currency": currency_code,
            "container_id": container_id,
            "counterparty_id": counterparty_id,
            "date_from": raw_date_from,
            "date_to": raw_date_to,
        },
        "containers": MoneyContainer.objects.filter(is_active=True).order_by("name"),
        "counterparties": Counterparty.objects.filter(is_active=True).order_by("type", "name")[:500],
        "kinds": ReceiptKind.choices,
        "statuses": ReceiptStatus.choices,
        "source_apps": (
            Receipt.objects.exclude(source_app="").values_list("source_app", flat=True).distinct().order_by("source_app")
        ),
        "source_models": (
            Receipt.objects.exclude(source_model="").values_list("source_model", flat=True).distinct().order_by("source_model")
        ),
    }
    return render(request, "financials/manager/receipt_explorer.html", ctx)


@login_required
@role_required(AccountProfile.Role.MANAGER)
def document_trace(request: HttpRequest) -> HttpResponse:
    from debts.models import (
        DebtorDebt,
        DebtorPayment,
        CreditorDebt,
        CreditorReceipt,
        DebtRecord,
        DebtSettlement,
        DebtDirection,
        DebtCauseType,
    )

    source_app = (request.GET.get("source_app") or "").strip()
    source_model = (request.GET.get("source_model") or "").strip()
    source_id = (request.GET.get("source_id") or "").strip()
    source_ref_display = source_id

    receipts = Receipt.objects.none()
    debtor_entries = DebtorDebt.objects.none()
    creditor_entries = CreditorDebt.objects.none()
    debtor_payments = DebtorPayment.objects.none()
    creditor_receipts = CreditorReceipt.objects.none()
    central_debts = DebtRecord.objects.none()
    central_settlements = DebtSettlement.objects.none()
    reversal_rows = Receipt.objects.none()
    source_document = None
    source_document_url = ""
    source_document_label = ""

    if source_app and source_model and source_id:
        source_ref_display = _preferred_source_ref_for_ui(
            source_app=source_app,
            source_model=source_model,
            source_id=source_id,
        ) or source_id
        lookup_values = _source_lookup_variants(
            source_app=source_app,
            source_model=source_model,
            source_id=source_id,
            allow_numeric_targeted=False,
        )

        if lookup_values:
            receipts = (
                Receipt.objects
                .select_related("actor")
                .prefetch_related("lines", "lines__currency", "lines__container", "lines__counterparty")
                .filter(source_app=source_app, source_model=source_model)
                .filter(
                    _source_identity_lookup_q_for_values(
                        source_field="source_id",
                        values=lookup_values,
                    )
                )
                .order_by("created_at", "id")
            )
            debt_lookup_q = Q(pk__in=[])
            for value in lookup_values:
                v = str(value or "").strip()
                if not v:
                    continue
                debt_lookup_q |= Q(source_id=v)
                debt_lookup_q |= Q(source_id__startswith=f"{v}:")
                debt_lookup_q |= Q(legacy_source_id=v)
                debt_lookup_q |= Q(legacy_source_id__startswith=f"{v}:")

            debtor_entries = (
                DebtorDebt.objects
                .filter(source_app=source_app, source_model=source_model)
                .filter(debt_lookup_q)
                .order_by("id")
            )
            creditor_entries = (
                CreditorDebt.objects
                .filter(source_app=source_app, source_model=source_model)
                .filter(debt_lookup_q)
                .order_by("id")
            )
            debtor_payments = (
                DebtorPayment.objects
                .select_related("receipt", "money_container", "entry")
                .filter(entry__in=debtor_entries)
                .order_by("created_at", "id")
            )
            creditor_receipts = (
                CreditorReceipt.objects
                .select_related("receipt", "money_container", "entry")
                .filter(entry__in=creditor_entries)
                .order_by("created_at", "id")
            )
        app_code = source_app.lower()
        cause_type = None
        direction_filter = None
        if app_code == "billing" and source_model == "Bill":
            cause_type = DebtCauseType.PURCHASE_BILL
            direction_filter = DebtDirection.PAYABLE
        elif app_code == "billing" and source_model == "ProviderReturn":
            cause_type = DebtCauseType.PROVIDER_RETURN
            direction_filter = DebtDirection.RECEIVABLE
        elif app_code == "pos" and source_model == "SalesBill":
            cause_type = DebtCauseType.POS_BILL
            direction_filter = DebtDirection.RECEIVABLE
        if cause_type:
            cause_refs = [str(v or "").strip() for v in lookup_values if str(v or "").strip()]
            source_ref_norm = str(source_ref_display or "").strip()
            targeted_prefix = _targeted_public_id_prefix(
                source_app=source_app,
                source_model=source_model,
            )
            allow_direct_ref = bool(cause_refs) or not bool(targeted_prefix)
            if allow_direct_ref and source_ref_norm and source_ref_norm not in cause_refs:
                cause_refs.insert(0, source_ref_norm)
            central_debts = DebtRecord.objects.filter(
                cause_type=cause_type,
                cause_id__in=cause_refs,
            )
            if direction_filter:
                central_debts = central_debts.filter(direction=direction_filter)
            central_debts = central_debts.order_by("id")
            central_settlements = (
                DebtSettlement.objects
                .select_related("debt", "receipt", "money_container")
                .filter(debt__in=central_debts)
                .order_by("created_at", "id")
            )
        reversal_rows = (
            Receipt.objects
            .filter(reverses_id__in=[r.id for r in receipts])
            .select_related("reverses", "actor")
            .order_by("created_at", "id")
        )

        try:
            if app_code == "billing" and source_model == "Bill":
                from billing.models import Bill

                source_document = (
                    Bill.objects
                    .select_related("provider")
                    .filter(public_id__iexact=source_ref_display)
                    .first()
                )
                if source_document is not None:
                    source_document_label = f"Purchase Bill #{source_document.public_id}"
            elif app_code == "billing" and source_model == "ProviderReturn":
                from billing.models import ProviderReturn

                source_document = (
                    ProviderReturn.objects
                    .select_related("provider")
                    .filter(public_id__iexact=source_ref_display)
                    .first()
                )
                if source_document is not None:
                    source_document_label = f"Provider Return #{source_document.public_id}"
            elif app_code == "pos" and source_model == "SalesBill":
                from pos.models import SalesBill

                source_document = (
                    SalesBill.objects
                    .select_related("customer")
                    .filter(public_id__iexact=source_ref_display)
                    .first()
                )
                if source_document is not None:
                    source_document_label = f"POS Sale Bill #{source_document.public_id}"
            elif app_code == "pos" and source_model == "SalesReturn":
                from pos.models import SalesReturn

                sid_int = int(source_id.split(":", 1)[0])
                source_document = (
                    SalesReturn.objects
                    .select_related("sale_bill")
                    .filter(pk=sid_int)
                    .first()
                )
                if source_document is not None:
                    source_document_label = f"POS Return #{source_document.serial or source_document.id}"
        except Exception:
            source_document = None
        source_document_url = _source_document_url(
            source_app=source_app,
            source_model=source_model,
            source_id=source_ref_display,
        )
    currency_totals: dict[str, Decimal] = {}
    for r in receipts:
        for ln in r.lines.all():
            cur = getattr(ln.currency, "code", "SYP")
            currency_totals[cur] = (currency_totals.get(cur, Decimal("0")) + (ln.amount or Decimal("0")))

    ctx = {
        **_secondary_menu_ctx("document_trace"),
        "source_app": source_app,
        "source_model": source_model,
        "source_id": source_id,
        "source_ref_display": source_ref_display,
        "source_document": source_document,
        "source_document_url": source_document_url,
        "source_document_label": source_document_label,
        "receipts": receipts,
        "debtor_entries": debtor_entries,
        "creditor_entries": creditor_entries,
        "debtor_payments": debtor_payments,
        "creditor_receipts": creditor_receipts,
        "central_debts": central_debts,
        "central_settlements": central_settlements,
        "reversal_rows": reversal_rows,
        "currency_totals": currency_totals,
    }
    return render(request, "financials/manager/document_trace.html", ctx)


@login_required
@role_required(AccountProfile.Role.MANAGER)
def reconciliation_dashboard(request: HttpRequest) -> HttpResponse:
    from billing.models import Bill, ProviderReturn
    from pos.models import SalesBill, SalesReturn

    def _receipt_target_internal_id(*, source_app: str, source_model: str, source_id: str) -> Optional[int]:
        values = _source_lookup_variants(
            source_app=source_app,
            source_model=source_model,
            source_id=str(source_id or "").strip(),
            allow_numeric_targeted=True,
        )
        for value in values:
            value = str(value or "").strip()
            if value.isdigit():
                return int(value)
        return None

    bill_receipt_ids = set()
    for sid in Receipt.objects.filter(source_app="billing", source_model="Bill").values_list("source_id", flat=True):
        internal_id = _receipt_target_internal_id(
            source_app="billing",
            source_model="Bill",
            source_id=str(sid or ""),
        )
        if internal_id is not None:
            bill_receipt_ids.add(internal_id)
    return_receipt_ids = set()
    for sid in Receipt.objects.filter(source_app="billing", source_model="ProviderReturn").values_list("source_id", flat=True):
        internal_id = _receipt_target_internal_id(
            source_app="billing",
            source_model="ProviderReturn",
            source_id=str(sid or ""),
        )
        if internal_id is not None:
            return_receipt_ids.add(internal_id)
    pos_bill_receipt_ids = set()
    for sid in Receipt.objects.filter(source_app="pos", source_model="SalesBill").values_list("source_id", flat=True):
        internal_id = _receipt_target_internal_id(
            source_app="pos",
            source_model="SalesBill",
            source_id=str(sid or ""),
        )
        if internal_id is not None:
            pos_bill_receipt_ids.add(internal_id)
    pos_return_receipt_ids = set()
    for sid in Receipt.objects.filter(source_app="pos", source_model="SalesReturn").values_list("source_id", flat=True):
        try:
            pos_return_receipt_ids.add(int(str(sid).split(":", 1)[0]))
        except Exception:
            continue

    missing_bill_receipts = (
        Bill.objects
        .filter(Q(total_syp__gt=0) | Q(total_usd__gt=0))
        .exclude(id__in=bill_receipt_ids)
        .select_related("provider")
        .order_by("-id")[:50]
    )
    missing_provider_return_receipts = (
        ProviderReturn.objects
        .filter(Q(total_syp__gt=0) | Q(total_usd__gt=0))
        .exclude(id__in=return_receipt_ids)
        .select_related("provider")
        .order_by("-id")[:50]
    )
    missing_pos_bill_receipts = (
        SalesBill.objects
        .filter(finalized=True, parked=False)
        .filter(Q(total_syp__gt=0) | Q(total_usd__gt=0))
        .exclude(id__in=pos_bill_receipt_ids)
        .select_related("customer")
        .order_by("-id")[:50]
    )
    missing_pos_return_receipts = (
        SalesReturn.objects
        .filter(status=SalesReturn.Status.POSTED)
        .filter(Q(total_syp__gt=0) | Q(total_usd__gt=0))
        .exclude(id__in=pos_return_receipt_ids)
        .select_related("sale_bill")
        .order_by("-id")[:50]
    )

    orphan_receipts = (
        Receipt.objects
        .filter(Q(source_app="") | Q(source_model="") | Q(source_id=""))
        .order_by("-id")[:100]
    )

    duplicate_action_keys = (
        Receipt.objects
        .exclude(action_key__isnull=True)
        .exclude(action_key="")
        .values("action_key")
        .annotate(c=Count("id"))
        .filter(c__gt=1)
        .order_by("-c", "action_key")
    )

    fx_anomalies = (
        Receipt.objects
        .filter(status__in=[ReceiptStatus.POSTED, ReceiptStatus.REVERSED])
        .filter(Q(fx_syp_per_usd__isnull=True) | Q(fx_syp_per_usd__lte=0))
        .order_by("-id")[:100]
    )
    fx_anomaly_rows = [
        {
            "receipt": r,
            "source_ref_display": _preferred_source_ref_for_ui(
                source_app=r.source_app,
                source_model=r.source_model,
                source_id=r.source_id,
            ) or (r.source_id or ""),
        }
        for r in fx_anomalies
    ]

    container_currency_mismatches = []
    rows = (
        PostingLine.objects
        .select_related("receipt", "container", "currency")
        .filter(target_type=PostingTargetType.CONTAINER, container__isnull=False)
        .order_by("-id")[:1000]
    )
    for ln in rows:
        enabled = MoneyContainerCurrency.objects.filter(
            container_id=ln.container_id,
            currency_id=ln.currency_id,
            is_enabled=True,
        ).exists()
        if not enabled:
            container_currency_mismatches.append(ln)

    ctx = {
        **_secondary_menu_ctx("reconciliation"),
        "missing_bill_receipts": missing_bill_receipts,
        "missing_provider_return_receipts": missing_provider_return_receipts,
        "missing_pos_bill_receipts": missing_pos_bill_receipts,
        "missing_pos_return_receipts": missing_pos_return_receipts,
        "orphan_receipts": orphan_receipts,
        "duplicate_action_keys": duplicate_action_keys,
        "fx_anomaly_rows": fx_anomaly_rows,
        "container_currency_mismatches": container_currency_mismatches[:100],
    }
    return render(request, "financials/manager/reconciliation_dashboard.html", ctx)

