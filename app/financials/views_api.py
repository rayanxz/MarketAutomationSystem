# financials/views_api.py
from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpRequest
from django.views.decorators.http import require_GET

from accounts.decorators import role_required
from accounts.models import AccountProfile

from financials.models import MoneyContainer
from financials import services as FSV


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
