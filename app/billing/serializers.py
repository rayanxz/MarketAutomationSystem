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

    u = getattr(b, "created_by", None)
    created_by_name = ""
    if u:
        # Works for Django User; if you use a custom user, still fine.
        fn = (getattr(u, "get_full_name", None)() if callable(getattr(u, "get_full_name", None)) else "").strip()
        created_by_name = fn or (getattr(u, "username", "") or str(getattr(u, "id", "")))

    return {
        "id": b.id,
        "serial": b.serial,
        "provider": {"id": b.provider_id, "name": b.provider.name if b.provider_id else ""},
        "total": str(b.total),
        "status": b.status,
        "paid_amount": str(b.paid_amount),
        "remaining": str(remaining_val),
        "created_at": b.created_at.isoformat() if b.created_at else None,

        "created_by": {"id": u.id, "name": created_by_name} if u else None,
        "created_by_name": created_by_name or None,
    }



def return_row(r: ProviderReturn) -> Dict[str, Any]:
    u = getattr(r, "created_by", None)
    created_by_name = ""
    if u:
        fn = (u.get_full_name() or "").strip() if hasattr(u, "get_full_name") else ""
        created_by_name = fn or (getattr(u, "username", "") or str(getattr(u, "id", "")))

    return {
        "id": r.id,
        "serial": r.serial,
        "provider": {"id": r.provider_id, "name": r.provider.name},
        "total": str(r.total),
        "paid_amount": str(r.paid_amount),
        "remaining": str(r.remaining),
        "status": r.status,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "source_bill_serial": r.source_bill_serial,

        "created_by": {"id": u.id, "name": created_by_name} if u else None,
        "created_by_name": created_by_name or None,
    }
