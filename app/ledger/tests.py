# app/ledger/tests.py
from django.test import TestCase
from django.contrib.auth import get_user_model
from ledger.models import Account, CashRegister, CashSession
from ledger import services
from ledger.choices import DC

User = get_user_model()

class PostingTests(TestCase):
    def test_balanced_journal(self):
        u = User.objects.create_user(username="x")
        cash = Account.objects.get(code="CASH_REGISTER_1")
        reg = CashRegister.objects.get(code="REG1")  # <-- use seeded register
        sess = CashSession.objects.create(register=reg, cashier=u, opened_by=u, opening_float_minor=1000)

        e = services.post_journal(
            actor=u,
            session=sess,
            lines=[
                services.LineSpec(cash.code, DC.DEBIT, 100),
                services.LineSpec("OPENING_FLOAT_EQUITY", DC.CREDIT, 100),
            ],
        )
        self.assertEqual(e.lines.count(), 2)
