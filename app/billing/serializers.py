# app/billing/serializers.py
from __future__ import annotations
from decimal import Decimal
from typing import Dict, Any

from billing.models import Provider, Bill, ProviderReturn



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


def return_row(r: ProviderReturn) -> Dict[str, Any]:
    # Keep compatibility for the returns list page
    return {
        "id": r.id,
        "serial": r.serial,
        "provider": {"id": r.provider_id, "name": r.provider.name},
        "total": str(r.total),
        "paid_amount": str(r.paid_amount),     # collected so far
        "remaining": str(r.remaining),
        "status": r.status,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "source_bill_serial": r.source_bill_serial,
    }