# catalog/urls.py
from django.urls import path
from catalog.browser_api import api_browser_collections, api_browser_sets, api_browser_products
from catalog.views import api_collection_stats, api_collection_cascade_delete

from inventory import views as inv_views


from catalog.views import (
    # Pages
    manager_collections,
    manager_product_new,
    manager_product_edit,
    collection_rename,
    collection_delete,   # (kept; not used by the new UI)
    manager_product_delete,

    # JSON APIs (two-pane + search + autocompletes)
    api_collection_products,
    api_product_search,
    api_collections_ac,
    api_sets_ac,
    api_sets_create,

    api_sets_search,
)



from catalog.import_views import (
    import_start,
    import_analyze,
    import_map,
    import_stage_rows,
    import_update_row,
    import_commit,
)

from catalog.export_views import (
export_start,
export_prepare,
export_download,
)


from catalog.edit_api import edit_apply_batch  # NEW

urlpatterns = [
    # Manager products main (two-pane)
    path("manager/products/", manager_collections, name="manager_collections"),

    # Collections management (legacy endpoints still available)
    path("manager/products/collections/<int:pk>/rename/", collection_rename, name="collection_rename"),
    path("manager/products/collections/<int:pk>/delete/", collection_delete, name="collection_delete"),

    # Product create / edit
    path("manager/products/new/", manager_product_new, name="manager_product_new"),
    path("manager/products/edit/<int:pk>/", manager_product_edit, name="manager_product_edit"),
    path("manager/products/<int:pk>/edit/", manager_product_edit, name="manager_product_edit"),

    # Two-pane JSON APIs
    path("manager/products/api/collections/<int:cid>/products/", api_collection_products, name="api_collection_products"),
    path("manager/products/api/search/", api_product_search, name="api_product_search"),

    # Autocomplete + parent set create
    path("manager/products/api/ac/collections/", api_collections_ac, name="api_collections_ac"),
    path("manager/products/api/ac/sets/",        api_sets_ac,        name="api_sets_ac"),
    path("manager/products/api/sets/create/",    api_sets_create,    name="api_sets_create"),

    path("manager/products/api/sets/search/",    api_sets_search,    name="api_sets_search"),

    # Product delete
    path("manager/products/delete/<int:pk>/", manager_product_delete, name="manager_product_delete"),

    # Hierarchy browser APIs
    path("manager/products/api/browser/collections/", api_browser_collections, name="api_browser_collections"),
    path("manager/products/api/browser/sets/",        api_browser_sets,        name="api_browser_sets"),
    path("manager/products/api/browser/products/",    api_browser_products,    name="api_browser_products"),

    # Collection stats + cascade delete
    path("manager/products/api/collections/<int:pk>/stats/",           api_collection_stats,           name="api_collection_stats"),
    path("manager/products/api/collections/<int:pk>/cascade_delete/",  api_collection_cascade_delete,  name="api_collection_cascade_delete"),

    # Batch editor (rename / adjust / delete) for set or collection
    path("manager/products/api/edit/apply/", edit_apply_batch, name="edit_apply_batch"),

    path("manager/products/import/",               import_start,       name="products_import"),
    path("manager/products/import/analyze/",       import_analyze,     name="products_import_analyze"),
    path("manager/products/import/map/",           import_map,         name="products_import_map"),
    path("manager/products/import/stage/",         import_stage_rows,  name="products_import_stage"),
    path("manager/products/import/update-row/",    import_update_row,  name="products_import_update_row"),
    path("manager/products/import/commit/",        import_commit,      name="products_import_commit"),


    path("manager/products/export/", export_start, name="products_export"),
    path("manager/products/export/prepare/", export_prepare, name="products_export_prepare"),
    path("manager/products/export/download/<str:k>/", export_download, name="products_export_download"),


        # Product movements (inventory)
    path("manager/products/movements/", inv_views.manager_product_movements, name="product_movements"),

]
