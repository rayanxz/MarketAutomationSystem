# app/ledger/choices.py
from django.db.models import TextChoices

class AccountType(TextChoices):
    ASSET = "ASSET", "Asset"
    LIABILITY = "LIABILITY", "Liability"
    EQUITY = "EQUITY", "Equity"
    REVENUE = "REVENUE", "Revenue"
    EXPENSE = "EXPENSE", "Expense"

class DC(TextChoices):
    DEBIT = "D", "Debit"
    CREDIT = "C", "Credit"
