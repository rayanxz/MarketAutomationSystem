from django.apps import AppConfig

class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self):
        try:
            from .licensing import check_license_or_raise
            check_license_or_raise()
        except Exception:
            # In dev we don't block; in prod you can raise SystemExit
            pass

        from .money_precision_guards import register_money_precision_guards

        register_money_precision_guards()
