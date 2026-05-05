from decimal import Decimal
from django.db import transaction, connection, reset_queries
from django.contrib.auth import get_user_model

from core.formatters import round_money, parse_money_strict
from financials import services as FSV
from financials.models import Currency, MoneyContainer, MoneyContainerCurrency
from debts import services as DebtSV
from debts.models import PartyType
from pos import api_bills as PosApi

User = get_user_model()
results = []

results.append(("round_money_1.237", str(round_money("1.237"))))
try:
    parse_money_strict("1.237")
    results.append(("parse_money_strict_1.237", "UNEXPECTED_OK"))
except Exception as e:
    results.append(("parse_money_strict_1.237", f"ERR:{type(e).__name__}:{e}"))

results.append(("FSV.q_money_1.237", str(FSV.q_money(amount=Decimal("1.237"), currency_code="SYP"))))

with transaction.atomic():
    u = User.objects.filter(is_superuser=True).first()
    if not u:
        u = User.objects.create_user(username="audit_tmp_user", password="x")

    syp, _ = Currency.objects.get_or_create(code="SYP", defaults={"name": "SYP", "decimals": 2, "is_active": True})
    usd, _ = Currency.objects.get_or_create(code="USD", defaults={"name": "USD", "decimals": 2, "is_active": True})

    c = MoneyContainer.objects.create(
        name="AUDIT TMP CONTAINER",
        container_type=MoneyContainer.ContainerType.DRAWER,
        created_by=u,
        is_active=True,
    )
    c.allowed_users.add(u)
    MoneyContainerCurrency.objects.update_or_create(container=c, currency=syp, defaults={"is_enabled": True})
    MoneyContainerCurrency.objects.update_or_create(container=c, currency=usd, defaults={"is_enabled": True})

    FSV.set_current_fx(actor=u, rate_syp_per_usd=Decimal("20000.00"))

    try:
        FSV.post_cash_add(actor=u, container_id=c.id, currency_code="SYP", amount=Decimal("1.237"), note="audit")
        results.append(("FSV.post_cash_add_1.237", "UNEXPECTED_OK"))
    except Exception as e:
        results.append(("FSV.post_cash_add_1.237", f"ERR:{type(e).__name__}:{e}"))

    try:
        r = FSV.post_cash_add(actor=u, container_id=c.id, currency_code="SYP", amount=Decimal("1.23"), note="audit")
        results.append(("FSV.post_cash_add_1.23", f"OK:receipt_id={r.id}"))
    except Exception as e:
        results.append(("FSV.post_cash_add_1.23", f"ERR:{type(e).__name__}:{e}"))

    try:
        DebtSV.create_manual_debt(
            actor=u,
            direction="debtor",
            party_type=PartyType.CUSTOMER,
            provider_id=None,
            party_name="Audit Customer",
            amount=Decimal("1.237"),
            currency_code="SYP",
            initial_payment=None,
            money_container_id=None,
            due_date=None,
        )
        results.append(("DebtSV.create_manual_debt_1.237", "UNEXPECTED_OK"))
    except Exception as e:
        results.append(("DebtSV.create_manual_debt_1.237", f"ERR:{type(e).__name__}:{e}"))

    try:
        PosApi._parse_money_decimal("1.237", field_name="paid_amount")
        results.append(("PosApi._parse_money_decimal_1.237", "UNEXPECTED_OK"))
    except Exception as e:
        results.append(("PosApi._parse_money_decimal_1.237", f"ERR:{type(e).__name__}:{e}"))

    reset_queries()
    connection.force_debug_cursor = True
    for _ in range(2000):
        FSV.q_money(amount=Decimal("12.34"), currency_code="SYP")
    q_count_syp = len(connection.queries)

    reset_queries()
    for _ in range(2000):
        FSV.q_money(amount=Decimal("12.34"), currency_code="USD")
    q_count_usd = len(connection.queries)

    results.append(("FSV.q_money_queries_2000_SYP", str(q_count_syp)))
    results.append(("FSV.q_money_queries_2000_USD", str(q_count_usd)))

    transaction.set_rollback(True)

for k, v in results:
    print(f"{k} => {v}")
