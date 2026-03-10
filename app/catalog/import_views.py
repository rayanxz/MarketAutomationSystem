# catalog/import_views.py
from __future__ import annotations
import json
from django.http import JsonResponse, HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from accounts.models import AccountProfile
from accounts.decorators import role_required
from catalog.models import ProductCollection
from catalog.import_engine import (
    save_temp_upload, analyze_file, stage_file_with_mapping,
    paged_rows, update_row_in_stage, commit_stage, StageError
)

@require_GET
@role_required(AccountProfile.Role.MANAGER)
def import_start(request: HttpRequest) -> HttpResponse:
    cols = ProductCollection.objects.only("id","name").order_by("name")
    return render(request, "manager/products_import.html", {"collections": cols})

@require_POST
@role_required(AccountProfile.Role.MANAGER)
def import_analyze(request: HttpRequest) -> HttpResponse:
    f = request.FILES.get("file")
    ftype = (request.POST.get("file_type") or "").lower()  # xlsx|ods
    collection_id = request.POST.get("collection")
    collection = ProductCollection.objects.filter(id=collection_id).first()
    has_header = (request.POST.get("has_header") in {"1","true","on"})
    if not f or not collection or ftype not in {"xlsx","ods"}:
        return JsonResponse({"ok": False, "error": "المدخلات غير مكتملة."}, status=400)
    try:
        sid = save_temp_upload(f, file_type=ftype, collection=collection, has_header=has_header)
        info = analyze_file(sid)
        return JsonResponse({"ok": True, "staging_id": sid, **info})
    except StageError as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)

@require_POST
@role_required(AccountProfile.Role.MANAGER)
def import_map(request: HttpRequest) -> HttpResponse:
    sid = request.POST.get("staging_id") or ""
    mode = (request.POST.get("import_mode") or "add_update").lower()  # new | add_update | update
    try:
        mapping = json.loads(request.POST.get("mapping_json") or "{}")
    except Exception:
        return JsonResponse({"ok": False, "error": "خريطة الأعمدة غير صالحة."}, status=400)
    options = {
        "create_missing_sets": request.POST.get("create_missing_sets") == "1",
        "import_mode": mode,
    }
    try:
        stats = stage_file_with_mapping(sid, mapping, options)
        # stats already includes initial erroring by mode
        return JsonResponse({"ok": True, **stats})
    except StageError as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)

@require_GET
@role_required(AccountProfile.Role.MANAGER)
def import_stage_rows(request: HttpRequest) -> HttpResponse:
    sid = request.GET.get("staging_id") or ""
    page = int(request.GET.get("page", "1"))
    size = int(request.GET.get("size", "200"))
    data = paged_rows(sid, page=page, size=size)
    return JsonResponse({"ok": True, **data})

@require_POST
@role_required(AccountProfile.Role.MANAGER)
def import_update_row(request: HttpRequest) -> HttpResponse:
    sid = request.POST.get("staging_id") or ""
    rid = int(request.POST.get("rid") or "0")
    field = request.POST.get("field") or ""
    value = request.POST.get("value") or ""
    try:
        row, errors_total = update_row_in_stage(sid, rid, field, value)
        return JsonResponse({"ok": True, "row": row, "errors_total": errors_total})
    except StageError as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)

@require_POST
@role_required(AccountProfile.Role.MANAGER)
def import_commit(request: HttpRequest) -> HttpResponse:
    sid = request.POST.get("staging_id") or ""
    try:
        summary = commit_stage(sid, actor=request.user, request=request)
        return JsonResponse({"ok": True, **summary})
    except StageError as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)
