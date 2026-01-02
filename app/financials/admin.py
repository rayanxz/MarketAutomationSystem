from django.contrib import admin
from .models import Currency, MoneyContainer, Counterparty, Receipt, PostingLine


@admin.register(Currency)
class CurrencyAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "decimals", "is_active")
    list_filter = ("is_active",)
    search_fields = ("code", "name")


@admin.register(MoneyContainer)
class MoneyContainerAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active", "created_by", "created_at")
    list_filter = ("is_active",)
    search_fields = ("name",)


@admin.register(Counterparty)
class CounterpartyAdmin(admin.ModelAdmin):
    list_display = ("type", "name", "is_active", "created_at")
    list_filter = ("type", "is_active")
    search_fields = ("name",)


class PostingLineInline(admin.TabularInline):
    model = PostingLine
    extra = 0
    readonly_fields = ("target_type", "container", "counterparty", "currency", "amount", "meta_json")


@admin.register(Receipt)
class ReceiptAdmin(admin.ModelAdmin):
    list_display = ("serial", "kind", "status", "actor", "created_at", "posted_at", "source_app", "source_model", "source_id")
    list_filter = ("kind", "status", "created_at")
    search_fields = ("serial", "note", "source_app", "source_model", "source_id")
    inlines = [PostingLineInline]


@admin.register(PostingLine)
class PostingLineAdmin(admin.ModelAdmin):
    list_display = ("id", "receipt", "target_type", "container", "counterparty", "currency", "amount")
    list_filter = ("target_type", "currency")
    search_fields = ("receipt__serial",)
