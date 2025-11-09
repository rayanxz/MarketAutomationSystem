# app/ledger/perms.py
from django.contrib.auth.decorators import user_passes_test

def is_cashier(user): return getattr(user, "account_profile", None) and user.account_profile.role in ("CASHIER","MANAGER","OWNER")
def is_manager(user): return getattr(user, "account_profile", None) and user.account_profile.role in ("MANAGER","OWNER")
def is_owner(user):   return getattr(user, "account_profile", None) and user.account_profile.role == "OWNER"

cashier_required = user_passes_test(is_cashier)
manager_required = user_passes_test(is_manager)
owner_required   = user_passes_test(is_owner)
