from __future__ import annotations

from debts.models import CreditorDebt, CreditorReceipt, DebtorDebt, DebtorPayment


DEBT_TABLE_OWNERSHIP = {
    "DebtorDebt": "billing_debtorentry",
    "DebtorPayment": "billing_debtorpayment",
    "CreditorDebt": "billing_creditorentry",
    "CreditorReceipt": "billing_creditorreceipt",
}


def managed_model_contract() -> dict[str, tuple[bool, str]]:
    """
    Returns managed-state + table-name contract for unmanaged debt mirror models.
    """
    return {
        "DebtorDebt": (DebtorDebt._meta.managed, DebtorDebt._meta.db_table),
        "DebtorPayment": (DebtorPayment._meta.managed, DebtorPayment._meta.db_table),
        "CreditorDebt": (CreditorDebt._meta.managed, CreditorDebt._meta.db_table),
        "CreditorReceipt": (CreditorReceipt._meta.managed, CreditorReceipt._meta.db_table),
    }
