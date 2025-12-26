# catalog/export_views.py
from __future__ import annotations
import os, mimetypes
from typing import List

from django.http import HttpRequest, HttpResponse, JsonResponse, FileResponse, Http404
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.csrf import ensure_csrf_cookie

from accounts.models import AccountProfile
from catalog.views import role_required
from catalog.models import ProductCollection
from catalog.export_engine import make_file, DEFAULT_COLUMNS, HEADER_LABELS, TMP_DIR

from catalog.io_records import CatalogDataJob
from audit_log.services import log_create

@require_GET
@ensure_csrf_cookie
@role_required(AccountProfile.Role.MANAGER)
def export_start(request: HttpRequest) -> HttpResponse:
    cols = list(
        ProductCollection.objects.only("id", "name", "code")
        .order_by("name").values("id", "name", "code")
    )
    return render(request, "manager/products_export.html", {
        "collections": cols,
        "default_columns": DEFAULT_COLUMNS,
        "column_labels": HEADER_LABELS,
    })

@require_POST
@role_required(AccountProfile.Role.MANAGER)
def export_prepare(request: HttpRequest) -> HttpResponse:
    scope = (request.POST.get("scope") or "all").strip().lower()
    file_type = (request.POST.get("file_type") or "xlsx").strip().lower()
    include_header = request.POST.get("include_header") in {"1", "true", "on"}

    ids: List[int] = []
    for raw in request.POST.getlist("ids[]"):
        try:
            ids.append(int(raw))
        except Exception:
            pass

    columns = request.POST.getlist("columns[]") or DEFAULT_COLUMNS

    try:
        key, path = make_file(
            scope, ids, columns=columns,
            include_header=include_header, file_type=file_type
        )
    except Exception as e:
        return JsonResponse({"ok": False, "error": f"export failed: {e}"}, status=500)

    headers, sample, rows_count = columns, [], 0

    # read file for preview + count (optional)
    try:
        import pandas as pd
        if path.endswith(".xlsx"):
            df = pd.read_excel(path, engine="openpyxl")
        else:
            df = pd.read_excel(path, engine="odf")

        rows_count = int(df.shape[0])
        df = df.astype(object).where(pd.notnull(df), "")
        headers = [str(c) for c in df.columns]
        sample = df.head(30).values.tolist()
    except Exception:
        # still continue; we can export without preview
        pass

    # ===== store export job + audit =====
    job = CatalogDataJob.objects.create(
        kind=CatalogDataJob.Kind.EXPORT,
        status=CatalogDataJob.Status.SUCCESS,
        actor=request.user,
    )
    job.summary_json = {"rows": rows_count}
    job.meta_json = {
        "scope": scope,
        "ids": ids,
        "columns": columns,
        "include_header": include_header,
        "file_type": file_type,
        "key": key,
    }
    job.rows_json = []  # export rows can be huge
    job.save(update_fields=["summary_text", "meta_text", "rows_text"])

    log_create(
        actor=request.user,
        target=job,
        title="Catalog Export",
        message=f"Exported catalog rows. rows={rows_count}",
        after={
            "job_id": job.id,
            "kind": job.kind,
            "status": job.status,
            "rows": rows_count,
            "scope": scope,
            "file_type": file_type,
        },
        request=request,
    )


    return JsonResponse(
        {
            "ok": True,
            "key": key,
            "headers": headers,
            "sample": sample,
            "rows": rows_count,
            "download_url": request.build_absolute_uri(f"/manager/products/export/download/{key}/"),
            "job_id": job.id,
        },
        json_dumps_params={"ensure_ascii": False, "allow_nan": False},
    )

@require_GET
@role_required(AccountProfile.Role.MANAGER)
def export_download(request: HttpRequest, k: str) -> HttpResponse:
    for ext in (".xlsx", ".ods"):
        path = os.path.join(TMP_DIR, f"{k}{ext}")
        if os.path.exists(path):
            mime = mimetypes.guess_type(path)[0] or (
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                if ext == ".xlsx" else "application/vnd.oasis.opendocument.spreadsheet"
            )
            resp = FileResponse(open(path, "rb"), as_attachment=True, filename=f"products_export{ext}")
            resp["Content-Type"] = mime
            return resp
    raise Http404("file not found")
