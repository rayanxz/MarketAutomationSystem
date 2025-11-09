from __future__ import annotations
from decimal import Decimal
from typing import Dict, Any

from debts.models import DebtorDebt as DebtorEntry, CreditorDebt as CreditorEntry

# ---------- Debtor ----------
def debtor_row(d: DebtorEntry) -> Dict[str, Any]:
    from billing.models import Bill
    serial = None
    provider_id = d.provider_id
    provider_name = d.provider.name if d.provider_id else ""

    if (d.source_app, d.source_model) == ("billing", "Bill"):
        try:
            b = Bill.objects.only("id", "serial", "provider_id", "provider__name").get(pk=int(d.source_id))
            doc_id = b.id
            serial = b.serial
            provider_id = b.provider_id
            provider_name = b.provider.name
        except Bill.DoesNotExist:
            doc_id = d.source_id
    else:
        doc_id = d.id
        serial = getattr(d, "doc_serial", None)

    ui_status = "paid" if d.remaining <= 0 else ("partial" if (d.paid_amount or 0) > 0 else "unpaid")
    ptype = getattr(d, "party_type", None) or "provider"
    pname = getattr(d, "party_name", None) or provider_name

    return {
        "entry_id": d.id,
        "id": doc_id,
        "serial": serial,
        "party_type": ptype,
        "party_name": pname,
        "provider": {"id": provider_id, "name": provider_name},
        "total": str(d.total),
        "paid_amount": str(d.paid_amount),
        "remaining": str(d.remaining),
        "status": ui_status,
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "manual": (d.source_model == "ManualDebt"),
    }

# ---------- Creditor ----------
def creditor_row(c: CreditorEntry) -> Dict[str, Any]:
    from billing.models import ProviderReturn
    serial = None
    provider_id = c.provider_id
    provider_name = c.provider.name if c.provider_id else ""

    if (c.source_app, c.source_model) == ("billing", "ProviderReturn"):
        try:
            r = ProviderReturn.objects.only("id", "serial", "provider_id", "provider__name").get(pk=int(c.source_id))
            doc_id = r.id
            serial = r.serial
            provider_id = r.provider_id
            provider_name = r.provider.name
        except ProviderReturn.DoesNotExist:
            doc_id = c.source_id
    else:
        doc_id = c.id
        serial = getattr(c, "doc_serial", None)

    ui_status = "paid" if c.remaining <= 0 else ("partial" if (c.collected or 0) > 0 else "unpaid")
    ptype = getattr(c, "party_type", None) or "provider"
    pname = getattr(c, "party_name", None) or provider_name

    return {
        "entry_id": c.id,
        "id": doc_id,
        "serial": serial,
        "party_type": ptype,
        "party_name": pname,
        "provider": {"id": provider_id, "name": provider_name},
        "total": str(c.total),
        "paid_amount": str(c.collected),
        "remaining": str(c.remaining),
        "status": ui_status,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "manual": (c.source_model == "ManualDebt"),
    }
