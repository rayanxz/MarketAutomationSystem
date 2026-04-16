# app/debts/serializers.py
from __future__ import annotations
from typing import Dict, Any

from debts.models import (
    DebtorDebt as DebtorEntry,
    CreditorDebt as CreditorEntry,
    DebtRecord,
    DebtDirection,
)
from debts.cause_refs import cause_ref_for_ui


def _safe_int(x) -> int | None:
    try:
        return int(x)
    except Exception:
        return None


# ---------- Debtor ----------
def debtor_row(d: DebtorEntry) -> Dict[str, Any]:
    # For billing->Bill debts, source_id IS the Bill id already
    if (d.source_app, d.source_model) == ("billing", "Bill"):
        doc_id = _safe_int(d.source_id) or d.source_id
        serial = getattr(d, "doc_serial", None)
    else:
        doc_id = d.id
        serial = getattr(d, "doc_serial", None)

    provider_id = d.provider_id
    provider_name = d.provider.name if d.provider_id else ""
    customer_id = getattr(d, "customer_id", None)
    customer_name = d.customer.name if getattr(d, "customer", None) else ""
    currency_code = (getattr(d, "currency_code", None) or "SYP").upper()

    ui_status = "paid" if d.remaining <= 0 else ("partial" if (d.paid_amount or 0) > 0 else "unpaid")
    ptype = (getattr(d, "party_type", None) or "provider")
    pname = (getattr(d, "party_name", None) or provider_name)

    return {
        "entry_id": d.id,
        "id": doc_id,                 # doc id (Bill id or manual id)
        "serial": serial,             # doc serial if available
        "party_type": ptype,
        "party_name": pname,
        "provider": {"id": provider_id, "name": provider_name},
        "customer": {"id": customer_id, "name": customer_name},
        "currency_code": currency_code,
        "total": str(d.total),
        "paid_amount": str(d.paid_amount or 0),
        "remaining": str(d.remaining),
        "status": ui_status,
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "manual": (d.source_model == "ManualDebt"),
    }


# ---------- Creditor ----------
def creditor_row(c: CreditorEntry) -> Dict[str, Any]:
    # For billing->ProviderReturn debts, source_id IS the ProviderReturn id already
    if (c.source_app, c.source_model) == ("billing", "ProviderReturn"):
        doc_id = _safe_int(c.source_id) or c.source_id
        serial = getattr(c, "doc_serial", None)
    else:
        doc_id = c.id
        serial = getattr(c, "doc_serial", None)

    provider_id = c.provider_id
    provider_name = c.provider.name if c.provider_id else ""
    customer_id = getattr(c, "customer_id", None)
    customer_name = c.customer.name if getattr(c, "customer", None) else ""
    currency_code = (getattr(c, "currency_code", None) or "SYP").upper()

    ui_status = "paid" if c.remaining <= 0 else ("partial" if (c.collected or 0) > 0 else "unpaid")
    ptype = (getattr(c, "party_type", None) or "provider")
    pname = (getattr(c, "party_name", None) or provider_name)

    return {
        "entry_id": c.id,
        "id": doc_id,                 # doc id (Return id or manual id)
        "serial": serial,             # doc serial if available
        "party_type": ptype,
        "party_name": pname,
        "provider": {"id": provider_id, "name": provider_name},
        "customer": {"id": customer_id, "name": customer_name},
        "currency_code": currency_code,
        "total": str(c.total),
        "paid_amount": str(c.collected or 0),
        "remaining": str(c.remaining),
        "status": ui_status,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "manual": (c.source_model == "ManualDebt"),
    }


def central_debt_row(d: DebtRecord) -> Dict[str, Any]:
    direction = (d.direction or "").lower()
    debt_type = "debtor" if direction == DebtDirection.PAYABLE else "creditor"

    other_party_name = ""
    if d.provider_id:
        other_party_name = d.provider.name
    elif d.customer_id:
        other_party_name = d.customer.name
    else:
        other_party_name = (d.other_party_id or "").strip()

    cause_ref = cause_ref_for_ui(
        cause_type=d.cause_type,
        cause_id=d.cause_id,
    )

    return {
        "debt_id": d.public_id,
        "debt_type": debt_type,
        "direction": d.direction,
        "status": d.status,
        "cause_type": d.cause_type,
        "cause_id": cause_ref,
        "other_party_type": d.other_party_type,
        "other_party_id": d.other_party_id,
        "other_party_name": other_party_name,
        "provider": {
            "name": d.provider.name if d.provider_id else "",
        },
        "customer": {
            "name": d.customer.name if d.customer_id else "",
        },
        "total_syp": str(d.total_syp or 0),
        "total_usd": str(d.total_usd or 0),
        "remaining_syp": str(d.remaining_syp or 0),
        "remaining_usd": str(d.remaining_usd or 0),
        "fx_syp_per_usd_at_creation": (
            str(d.fx_syp_per_usd_at_creation)
            if d.fx_syp_per_usd_at_creation is not None
            else None
        ),
        "actor_username": d.actor_username or "",
        "created_at": d.created_at.isoformat() if d.created_at else None,
    }
