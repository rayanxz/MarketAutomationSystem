from decimal import Decimal
from uuid import uuid4
from django.db import transaction
from django.contrib.auth import get_user_model

from debts.models import DebtorDebt, PartyType
from pos.models import SalesBill
from financials.models import PostingLine, Receipt, Currency, Counterparty, CounterpartyType

User = get_user_model()

with transaction.atomic():
    u = User.objects.order_by('id').first()
    if not u:
        u = User.objects.create_user(username='audit_tmp_user_' + uuid4().hex[:6], password='x')

    # DebtorDebt (managed=False legacy table) direct write
    sid = 'audit:' + uuid4().hex[:8]
    d = DebtorDebt.objects.create(
        provider=None,
        source_app='audit',
        source_model='Probe',
        source_id=sid,
        legacy_source_id='',
        currency_code='SYP',
        total=Decimal('1.237'),
        paid_amount=Decimal('0.000'),
        status=DebtorDebt.Status.OPEN,
        party_type=PartyType.CUSTOMER,
        party_name='Audit',
        customer=None,
        doc_serial=None,
        due_date=None,
    )

    # SalesBill direct write (paid_amount has decimal_places=3)
    b = SalesBill.objects.create(
        customer=None,
        customer_name='Audit',
        cashier=u,
        pay_status=SalesBill.PAY_PARTIAL,
        total_amount=Decimal('10.00'),
        total_syp=Decimal('10.00'),
        total_usd=Decimal('0.00'),
        paid_amount=Decimal('1.237'),
        settlement_mode=SalesBill.SETTLE_SPLIT,
        settlement_currency=None,
        fx_rate_used=None,
        parked=True,
        finalized=False,
    )

    # PostingLine amount decimal_places=6 (can store more than 2)
    cur, _ = Currency.objects.get_or_create(code='SYP', defaults={'name':'SYP','decimals':2,'is_active':True})
    cp, _ = Counterparty.objects.get_or_create(type=CounterpartyType.SYSTEM, name='Audit CP')
    r = Receipt.objects.create(
        serial='',
        kind='counterparty_inc',
        status='draft',
        actor=u,
        note='audit',
        source_app='audit',
        source_model='probe',
        source_id='x',
        fx_syp_per_usd=Decimal('20000.000000'),
    )
    r.ensure_serial(); r.save(update_fields=['serial'])
    pl = PostingLine.objects.create(
        receipt=r,
        target_type='counterparty',
        container=None,
        counterparty=cp,
        currency=cur,
        amount=Decimal('1.237000'),
        meta_json='{}',
    )

    print('DebtorDebt.total_saved=', d.total)
    print('SalesBill.paid_amount_saved=', b.paid_amount)
    print('PostingLine.amount_saved=', pl.amount)

    transaction.set_rollback(True)
