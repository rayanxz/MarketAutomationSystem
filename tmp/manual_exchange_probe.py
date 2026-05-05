from decimal import Decimal
from django.contrib.auth import get_user_model
from financials.models import MoneyContainer, Currency, MoneyContainerCurrency, FxSettings
from financials import manual_events as ManualSV
from financials import services as FSV

out = {}
User = get_user_model()
user = User.objects.filter(is_superuser=True).first() or User.objects.first()
if not user:
    user = User.objects.create_user(username='audit_tmp_u', password='x')

syp = Currency.objects.filter(code='SYP').first()
usd = Currency.objects.filter(code='USD').first()
if syp is None:
    syp = Currency.objects.create(code='SYP', name='Syrian Pound', decimals=2, is_active=True)
if usd is None:
    usd = Currency.objects.create(code='USD', name='US Dollar', decimals=2, is_active=True)

c = MoneyContainer.objects.filter(name='AUDIT_TMP_MC').first()
if c is None:
    c = MoneyContainer.objects.create(name='AUDIT_TMP_MC', created_by=user, container_type='drawer', is_active=True)

c.allowed_users.add(user)
MoneyContainerCurrency.objects.update_or_create(container=c, currency=syp, defaults={'is_enabled': True})
MoneyContainerCurrency.objects.update_or_create(container=c, currency=usd, defaults={'is_enabled': True})

fx = FxSettings.objects.filter(is_active=True).order_by('-updated_at', '-id').first()
if fx is None:
    FxSettings.objects.create(rate_syp_per_usd=Decimal('10000.00'), is_active=True, updated_by=user)

try:
    FSV.post_cash_add(actor=user, container_id=c.id, currency_code='USD', amount=Decimal('10.00'), note='seed')
except Exception as exc:
    out['seed'] = f'ignored {type(exc).__name__}: {exc}'

try:
    r = ManualSV.post_manual_exchange(
        actor=user,
        from_container_id=c.id,
        to_container_id=c.id,
        currency_from='USD',
        currency_to='SYP',
        amount_from=Decimal('1.237'),
        fx_syp_per_usd=Decimal('10000.00'),
        note='audit probe',
    )
    out['manual_exchange_1_237'] = f'ACCEPTED receipt={r.id}'
except Exception as exc:
    out['manual_exchange_1_237'] = f'REJECTED {type(exc).__name__}: {exc}'

print(out)
