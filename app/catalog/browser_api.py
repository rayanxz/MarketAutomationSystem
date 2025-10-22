# catalog/browser_api.py
from math import ceil
from django.http import JsonResponse
from django.views.decorators.http import require_GET
from django.db.models.functions import Lower

from accounts.models import AccountProfile
from catalog.models import ProductCollection, ProductSet, Product, ProductBarcode  # noqa: F401
from catalog.views import role_required  # reuse the same decorator

PAGE_SIZE = 15

def _page(qs, page: int, size: int = PAGE_SIZE):
    total = qs.count()
    pages = max(1, ceil(total / size))
    page = max(1, min(page, pages))
    off = (page - 1) * size
    return total, pages, page, off, size

@require_GET
@role_required(AccountProfile.Role.MANAGER)
def api_browser_collections(request):
    try:
        page = int(request.GET.get("page", "1"))
    except ValueError:
        page = 1
    qs = ProductCollection.objects.order_by(Lower("name"), "id").values("id", "name", "code")
    total, pages, page, off, size = _page(qs, page)
    return JsonResponse({
        "ok": True,
        "level": "collections",
        "items": list(qs[off:off+size]),
        "total": total, "page": page, "total_pages": pages
    })

@require_GET
@role_required(AccountProfile.Role.MANAGER)
def api_browser_sets(request):
    try:
        cid  = int(request.GET.get("cid") or "0")
        page = int(request.GET.get("page", "1"))
    except ValueError:
        return JsonResponse({"ok": False, "error": "bad params"}, status=400)
    if not cid:
        return JsonResponse({"ok": True, "items": [], "total": 0, "page": 1, "total_pages": 1})
    qs = ProductSet.objects.filter(collection_id=cid).order_by(Lower("name"), "id").values("id", "name", "code")
    total, pages, page, off, size = _page(qs, page)
    return JsonResponse({
        "ok": True,
        "level": "sets",
        "items": list(qs[off:off+size]),
        "total": total, "page": page, "total_pages": pages
    })

@require_GET
@role_required(AccountProfile.Role.MANAGER)
def api_browser_products(request):
    try:
        sid  = int(request.GET.get("sid") or "0")
        page = int(request.GET.get("page", "1"))
    except ValueError:
        return JsonResponse({"ok": False, "error": "bad params"}, status=400)
    if not sid:
        return JsonResponse({"ok": True, "items": [], "total": 0, "page": 1, "total_pages": 1})
    qs = (Product.objects.filter(set_id=sid, is_active=True)
          .order_by("product_number" , "id")
          .values("id", "name", "product_number"))
    total, pages, page, off, size = _page(qs, page)
    items = [{"id": p["id"], "name": p["name"], "code": f'{(p["product_number"] or 0):03d}',
              } 
              for p in qs[off:off+size]]
    return JsonResponse({
        "ok": True,
        "level": "products",
        "items": items,
        "total": total, "page": page, "total_pages": pages
    })