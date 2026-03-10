# financials/views_api.py
from __future__ import annotations
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpRequest
from django.views.decorators.http import require_GET, require_POST

from accounts.decorators import role_required
from accounts.models import AccountProfile

from financials.models import MoneyContainer
from financials import services as FSV
from financials import manual_events as ManualSV


@require_GET
@login_required
@role_required(AccountProfile.Role.MANAGER)
def ref_code_preview(request: HttpRequest) -> JsonResponse:
    t = (request.GET.get("type") or "").strip()

    valid = {c[0] for c in MoneyContainer.ContainerType.choices}
    if t not in valid:
        # fallback to drawer
        t = MoneyContainer.ContainerType.DRAWER

    ref = FSV.alloc_ref_code(container_type=t)
    return JsonResponse({"ref_code": ref, "container_type": t})


@require_POST
@login_required
@role_required(AccountProfile.Role.MANAGER)
def manual_event(request: HttpRequest) -> JsonResponse:
    action = (request.POST.get("action") or "").strip().lower()
    from_id = request.POST.get("from_container")
    to_id = request.POST.get("to_container")
    currency = (request.POST.get("currency") or "").strip().upper()
    currency_from = (request.POST.get("currency_from") or "").strip().upper()
    currency_to = (request.POST.get("currency_to") or "").strip().upper()
    fx_raw = request.POST.get("fx_rate")
    note = (request.POST.get("note") or "").strip()

    def _dec(val):
        try:
            return Decimal(str(val or "0").replace(",", "."))
        except Exception:
            return Decimal("0")

    amount = _dec(request.POST.get("amount"))
    if amount <= 0:
        return JsonResponse({"ok": False, "error": "INVALID_AMOUNT"}, status=400)

    try:
        if action == "add":
            target_id = to_id or from_id
            receipt = ManualSV.post_manual_add(
                actor=request.user,
                container_id=int(target_id),
                currency_code=currency,
                amount=amount,
                note=note,
            )
        elif action == "withdraw":
            receipt = ManualSV.post_manual_withdraw(
                actor=request.user,
                container_id=int(from_id),
                currency_code=currency,
                amount=amount,
                note=note,
            )
        elif action == "transfer":
            receipt = ManualSV.post_manual_transfer(
                actor=request.user,
                from_container_id=int(from_id),
                to_container_id=int(to_id),
                currency_code=currency,
                amount=amount,
                note=note,
            )
        elif action == "exchange":
            fx_val = None
            fx_provided = fx_raw not in (None, "")
            if fx_provided:
                try:
                    fx_val = FSV.q_fx(Decimal(str(fx_raw).replace(",", ".")))
                except Exception:
                    return JsonResponse({"ok": False, "error": "INVALID_FX_RATE"}, status=400)
                if fx_val <= 0:
                    return JsonResponse({"ok": False, "error": "INVALID_FX_RATE"}, status=400)
            receipt = ManualSV.post_manual_exchange(
                actor=request.user,
                from_container_id=int(from_id),
                to_container_id=int(to_id) if to_id else None,
                currency_from=currency_from,
                currency_to=currency_to,
                amount_from=amount,
                fx_syp_per_usd=fx_val,
                note=note,
            )
        else:
            return JsonResponse({"ok": False, "error": "INVALID_ACTION"}, status=400)
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)

    return JsonResponse({"ok": True, "receipt_id": receipt.id})
