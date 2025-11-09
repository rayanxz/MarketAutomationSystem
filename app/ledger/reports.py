# app/ledger/reports.py
from django.db.models import Sum, Q
from .models import JournalEntry, JournalLine, CashSession, Account

def sales_by_cashier(date_from, date_to):
    sales = Account.objects.get(code="SALES")
    qs = JournalLine.objects.filter(
        account=sales, entry__posted_at__gte=date_from, entry__posted_at__lt=date_to
    ).values("entry__actor__id","entry__actor__username").annotate(total=Sum("amount_minor"))
    return list(qs)

def cash_account_balance(account_code):
    try:
        acc = Account.objects.get(code=account_code)
    except Account.DoesNotExist:
        return 0
    agg = acc.journal_lines.aggregate(
        d=Sum("amount_minor", filter=Q(dc="D")),
        c=Sum("amount_minor", filter=Q(dc="C")),
    )
    return (agg["d"] or 0) - (agg["c"] or 0)
