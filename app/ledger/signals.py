# app/ledger/signals.py
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils.timezone import localdate
from .models import JournalLine, DailyBalance

@receiver(post_save, sender=JournalLine)
def update_daily_balance(sender, instance, created, **kwargs):
    if not created: return
    day = localdate(instance.entry.posted_at)
    db, _ = DailyBalance.objects.get_or_create(account=instance.account, day=day)
    if instance.dc == "D":
        db.debit_minor += instance.amount_minor
    else:
        db.credit_minor += instance.amount_minor
    db.save(update_fields=["debit_minor","credit_minor"])
