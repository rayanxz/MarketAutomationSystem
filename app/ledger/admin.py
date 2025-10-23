# app/ledger/admin.py
from django.contrib import admin
from .models import Account, CashRegister, CashSession, JournalEntry, JournalLine, CashDrawerEvent, CashCount, DailyBalance

@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ("code","name","type","is_active")
    list_filter = ("type","is_active")
    search_fields = ("code","name")

@admin.register(CashRegister)
class CashRegisterAdmin(admin.ModelAdmin):
    list_display = ("code","name","cash_account")

@admin.register(CashSession)
class CashSessionAdmin(admin.ModelAdmin):
    list_display = ("register","cashier","opened_at","is_closed","closed_at","expected_minor","counted_minor","over_short_minor")
    list_filter = ("is_closed","register","cashier")

class JournalLineInline(admin.TabularInline):
    model = JournalLine
    extra = 0
    readonly_fields = ("account","dc","amount_minor","extra")

@admin.register(JournalEntry)
class JournalEntryAdmin(admin.ModelAdmin):
    list_display = ("posted_at","actor","session","source_app","source_model","source_id","idempotency_key","voided_at")
    search_fields = ("idempotency_key","source_app","source_model","source_id")
    inlines = [JournalLineInline]
    readonly_fields = ("posted_at","actor","session","source_app","source_model","source_id","idempotency_key","memo","voided_at","void_reason","voided_by")
