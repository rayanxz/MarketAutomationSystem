# app/billing/serializers.py
from __future__ import annotations
from decimal import Decimal
from typing import Dict, Any

from billing.models import Provider, Bill, ProviderReturn, DebtorEntry, CreditorEntry

def provider_row(p: Provider) -> Dict[str, Any]:
    return {
        "id": p.id,
        "name": p.name,
        "phone": p.phone or "",
        "is_active": bool(getattr(p, "is_active", True)),
        "bills_count": int(getattr(p, "bills_count", 0) or 0),
        "unpaid_bills_count": int(getattr(p, "unpaid_bills_count", 0) or 0),
        "total_debt": str(getattr(p, "total_debt", Decimal("0")) or 0),
    }

def bill_row(b: Bill) -> Dict[str, Any]:
    remaining_val = getattr(b, "remaining", None)
    if remaining_val is None:
        remaining_val = Decimal("0")
    return {
        "id": b.id,
        "serial": b.serial,
        "provider": {"id": b.provider_id, "name": b.provider.name if b.provider_id else ""},
        "total": str(b.total),
        "status": b.status,                   # "paid" | "partial" | "unpaid"
        "paid_amount": str(b.paid_amount),
        "remaining": str(remaining_val),
        "created_at": b.created_at.isoformat() if b.created_at else None,
    }

# ---------- NEW: emit bill-like rows for debtor sub-ledger ----------
def debtor_row(d: DebtorEntry) -> Dict[str, Any]:
    # Try to display the commercial document id/serial if source is Bill
    doc_id = d.source_id
    serial = None
    if (d.source_app, d.source_model) == ("billing", "Bill"):
        try:
            b = Bill.objects.only("id", "serial", "provider_id", "provider__name").get(pk=int(d.source_id))
            doc_id = b.id
            serial = b.serial
            provider_id = b.provider_id
            provider_name = b.provider.name
        except Bill.DoesNotExist:
            provider_id = d.provider_id
            provider_name = d.provider.name if d.provider_id else ""
    else:
        provider_id = d.provider_id
        provider_name = d.provider.name if d.provider_id else ""

    # Map sub-ledger status to UI status
    ui_status = "paid" if d.remaining <= 0 else ("partial" if (d.paid_amount or 0) > 0 else "unpaid")

    return {
        "id": doc_id,                          # show Bill id
        "serial": serial,                      # may be None
        "provider": {"id": provider_id, "name": provider_name},
        "total": str(d.total),
        "paid_amount": str(d.paid_amount),
        "remaining": str(d.remaining),
        "status": ui_status,                   # "paid" | "partial" | "unpaid"
        "created_at": d.created_at.isoformat() if d.created_at else None,
    }

# ---------- NEW: emit bill-like rows for creditor sub-ledger ----------
def creditor_row(c: CreditorEntry) -> Dict[str, Any]:
    # If the source is ProviderReturn, expose its id/serial
    doc_id = c.source_id
    serial = None
    if (c.source_app, c.source_model) == ("billing", "ProviderReturn"):
        try:
            r = ProviderReturn.objects.only("id", "serial", "provider_id", "provider__name").get(pk=int(c.source_id))
            doc_id = r.id
            serial = r.serial
            provider_id = r.provider_id
            provider_name = r.provider.name
        except ProviderReturn.DoesNotExist:
            provider_id = c.provider_id
            provider_name = c.provider.name if c.provider_id else ""
    else:
        provider_id = c.provider_id
        provider_name = c.provider.name if c.provider_id else ""

    ui_status = "paid" if c.remaining <= 0 else ("partial" if (c.collected or 0) > 0 else "unpaid")

    return {
        "id": doc_id,                          # show Return id
        "serial": serial,                      # may be None
        "provider": {"id": provider_id, "name": provider_name},
        "total": str(c.total),
        "paid_amount": str(c.collected),       # keep column name consistent
        "remaining": str(c.remaining),
        "status": ui_status,                   # "paid" | "partial" | "unpaid"
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }

def return_row(r: ProviderReturn) -> Dict[str, Any]:
    # Keep compatibility for the returns list page
    return {
        "id": r.id,
        "serial": r.serial,
        "provider": {"id": r.provider_id, "name": r.provider.name},
        "total": str(r.total),
        "paid_amount": str(r.paid_amount),     # collected
        "remaining": str(r.remaining),
        "status": r.status,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }
