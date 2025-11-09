from __future__ import annotations
# app/ledger/views.py

# Create your views here.
from datetime import datetime
from decimal import Decimal
from typing import TypedDict, Optional
from django.shortcuts import render

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils.dateparse import parse_datetime
from django.db.models import Q, F
from django.utils.timezone import make_aware
from django.conf import settings

from .models import JournalEntry, JournalLine, Account
from .choices import DC
from .perms import manager_required
from .reports import cash_account_balance

# If billing is installed, we’ll resolve provider names for nicer UX.
try:
    from billing.models import Provider
except Exception:
    Provider = None




SAFE_CODE = "SAFE"

# ---------- UI PAGES ----------

@login_required
@manager_required
def volt_home(request: HttpRequest) -> HttpResponse:
    # Redirect into movements for now (single tab)
    return volt_movements_page(request)

@login_required
@manager_required
def volt_movements_page(request: HttpRequest) -> HttpResponse:
    return render(request, "ledger/volt_movement.html", {
        "safe_code": SAFE_CODE,
    })

# ---------- SERIALIZATION HELPERS ----------

ACTION_LABELS = {
    # ledger.services memos (stable strings we set there)
    "Provider purchase": ("BILL", "bill_purchase"),  # cash out = down
    "Reversal of provider purchase": ("BILL", "bill_purchase_reversal"),
    "Provider payment from SAFE": ("BILL", "bill_payment"),
    "Provider return": ("PROVIDER_RETURN", "provider_return"),
    "Reversal of provider return": ("PROVIDER_RETURN", "provider_return_reversal"),
    "Collection from provider": ("PROVIDER_RETURN", "provider_collection"),
    "Cash In": ("CASH", "cash_in"),
    "Cash Out": ("CASH", "cash_out"),
    "Safe Drop": ("CASH", "safe_drop"),
    "Bank Deposit": ("BANK", "bank_deposit"),
    "Cash Sale": ("SALE", "cash_sale"),
    "Credit Sale": ("SALE", "credit_sale"),
    "Over/Short adjustment": ("CASH", "over_short"),
}

def _effect_for_line(dc: str) -> str:
    # Relative to SAFE: Dr increases SAFE (UP), Cr decreases SAFE (DOWN)
    return "UP" if dc == DC.DEBIT else "DOWN"

def _party_label(ptype: str) -> str:
    m = {"provider": "مورّد", "customer": "زبون", "worker": "عامل"}
    return m.get((ptype or "").lower(), "—")

def _has_line(entry: JournalEntry, account_code: str, dc: str) -> bool:
    # Any line on this entry hitting that account on that DC side?
    try:
        return entry.lines.select_related("account").filter(
            account__code=account_code, dc=dc
        ).exists()
    except Exception:
        return False


def _nice_action(entry: JournalEntry, cash_line: JournalLine) -> tuple[str, str]:
    """
    Returns (group, human_label) using memo + lines to decide full vs partial.
    """
    memo = (entry.memo or "").strip()
    sm   = (entry.source_model or "").strip()
    sa   = (entry.source_app or "").strip().lower()

    # --- Manual debts (from debts app) ---
    if sa == "debts" and sm in ("DebtorDebt", "CreditorDebt"):
        if sm == "DebtorDebt":
            return "DEBT", "إضافة دين (مدين)"
        else:
            return "DEBT", "إضافة دين (دائن)"

    # Default
    label = "—"
    group = "OTHER"

    # --- Provider purchase (Bills) ---
    if memo.startswith("Provider purchase"):
        group = "BILL"
        # If there is PROVIDER_PAYABLE credit, then it wasn't fully paid now.
        partial = _has_line(entry, "PROVIDER_PAYABLE", DC.CREDIT)
        label = "إضافة فاتورة (مدفوع جزئياً)" if partial else "إضافة فاتورة (مدفوع بالكامل)"
        return group, label

    # --- Provider payment (batch/full cash settlement after) ---
    if memo.startswith("Provider payment from SAFE"):
        group = "BILL"
        label = "دفع مستحقات فاتورة (دفعة)"
        return group, label

    # --- Provider return (store sends goods back) ---
    if memo.startswith("Provider return"):
        group = "PROVIDER_RETURN"
        # If there is PROVIDER_RECEIVABLE debit, then it wasn't fully collected now.
        partial = _has_line(entry, "PROVIDER_RECEIVABLE", DC.DEBIT)
        label = "إرجاع لمورّد (مقبوض جزئياً)" if partial else "إرجاع لمورّد (مقبوض بالكامل)"
        return group, label

    # --- Collection from provider (batch) ---
    if memo.startswith("Collection from provider"):
        group = "PROVIDER_RETURN"
        label = "تحصيل من مورّد (دفعة)"
        return group, label

    # --- Cash/session events & sales (keep your existing Arabic) ---
    for key, (grp, code) in ACTION_LABELS.items():
        if memo.startswith(key):
            group = grp
            if code == "cash_in":                  label = "إيداع نقدي"
            elif code == "cash_out":               label = "سحب نقدي"
            elif code == "safe_drop":              label = "إيداع في الخزنة"
            elif code == "bank_deposit":           label = "إيداع بنكي"
            elif code == "cash_sale":              label = "بيع نقدي"
            elif code == "credit_sale":            label = "بيع آجل"
            elif code == "over_short":             label = "تسوية زيادة/نقص"
            elif code == "bill_purchase_reversal": label = "عكس فاتورة شراء"
            elif code == "provider_return_reversal": label = "عكس إرجاع مورّد"
            break

    # Fallback: show the raw memo if we didn’t match anything
    if label == "—" and memo:
        label = memo
    return group, label


def _resolve_party(entry: JournalEntry) -> tuple[str, str]:
    """
    Best-effort (party_type, party_name) for a volt movement.
    - Billing docs (Bill / ProviderReturn): parse provider id from memo "... #<id>"
    - Manual debts (DebtorDebt / CreditorDebt): read party_type/party_name (or provider.name) from the debt row
    """
    ptype, pname = "", ""

    sm = (entry.source_model or "").strip()
    sa = (entry.source_app or "").strip().lower()

    # ---------- Billing: Bill / ProviderReturn → provider name ----------
    if sm in ("Bill", "ProviderReturn") and Provider:
        try:
            # We store provider id inside memo like: "Provider purchase #<id>"
            pid = None
            memo = entry.memo or ""
            if "#" in memo:
                tail = memo.split("#")[-1].strip().rstrip(":")
                if tail.isdigit():
                    pid = int(tail)
            # Fallback: if source_id looks numeric and you ever used it for provider id
            if not pid and (entry.source_id or "").isdigit():
                # Only treat as provider id if a Provider with that id exists
                test_pid = int(entry.source_id)
                if Provider.objects.filter(id=test_pid).exists():
                    pid = test_pid

            if pid:
                prov = Provider.objects.filter(id=pid).only("name").first()
                if prov:
                    return "provider", prov.name
        except Exception:
            pass

    # ---------- Debts: DebtorDebt / CreditorDebt → party_type / party_name ----------
    if sa == "debts" and sm in ("DebtorDebt", "CreditorDebt"):
        sid = (entry.source_id or "").strip()
        if sid.isdigit():
            try:
                # Imports are optional in module scope; guard if not available
                from debts.models import DebtorDebt, CreditorDebt  # type: ignore
            except Exception:
                DebtorDebt = CreditorDebt = None  # type: ignore

            try:
                if sm == "DebtorDebt" and DebtorDebt:
                    d = (DebtorDebt.objects
                         .select_related("provider")
                         .only("party_type", "party_name", "provider__name", "provider_id")
                         .get(pk=int(sid)))
                    ptype = (getattr(d, "party_type", "") or "provider").lower()
                    pname = (getattr(d, "party_name", "") or
                             (d.provider.name if getattr(d, "provider_id", None) else ""))
                    return ptype, pname

                if sm == "CreditorDebt" and CreditorDebt:
                    c = (CreditorDebt.objects
                         .select_related("provider")
                         .only("party_type", "party_name", "provider__name", "provider_id")
                         .get(pk=int(sid)))
                    ptype = (getattr(c, "party_type", "") or "provider").lower()
                    pname = (getattr(c, "party_name", "") or
                             (c.provider.name if getattr(c, "provider_id", None) else ""))
                    return ptype, pname
            except Exception:
                pass

    # Unknown / not resolvable
    return ptype, pname


    

def _format_amount_minor(minor: int) -> str:
    # Syrian Pounds with thousands separators, integer minor=1 SYP
    return f"{minor:,.0f} SYP"

# ---------- API: MOVEMENTS (keyset) ----------

class Cursor(TypedDict):
    posted_at: str   # ISO
    id: int

def _parse_cursor(cur: str | None) -> Optional[Cursor]:
    if not cur: return None
    # format: "<ts_iso>|<id>"
    try:
        ts, sid = cur.split("|", 1)
        dt = parse_datetime(ts)
        if dt is None:
            dt = make_aware(datetime.fromisoformat(ts))
        return {"posted_at": dt.isoformat(), "id": int(sid)}
    except Exception:
        return None

@login_required
@manager_required
def api_volt_movements(request: HttpRequest) -> JsonResponse:
    """
    Returns rows where SAFE account is touched.
    Filters:
    - effect: up/down/both
    - mtype: bill/provider_return/debt/all
    - df (date from, ISO) / dt (date to, ISO)
    - min_amt / max_amt (integers in SYP)
    - party_type: provider/worker/customer (for now only provider fully resolved)
    - party_name: substring
    Pagination:
    - use keyset: pass ?cursor=<iso>|<id> for the NEXT page (older).
    """
    limit = min(int(request.GET.get("limit", 50)), 200)

    # Base: SAFE account lines
    try:
        safe = Account.objects.get(code=SAFE_CODE, is_active=True)
    except Account.DoesNotExist:
        return JsonResponse({"rows": [], "next_cursor": None})

    qlines = JournalLine.objects.filter(account_id=safe.id).select_related("entry").order_by(
        F("entry__posted_at").desc(), F("entry_id").desc()
    )

    # Cursor (older)
    cur = _parse_cursor(request.GET.get("cursor"))
    if cur:
        qlines = qlines.filter(
            Q(entry__posted_at__lt=cur["posted_at"]) |
            Q(entry__posted_at=cur["posted_at"], entry_id__lt=cur["id"])
        )

    # Effect filter
    effect = (request.GET.get("effect") or "both").lower()
    if effect in ("up","down"):
        dc = DC.DEBIT if effect == "up" else DC.CREDIT
        qlines = qlines.filter(dc=dc)

    # Movement type filter (based on source_model/memo group)
    mtype = (request.GET.get("mtype") or "all").lower()
    # We'll post-filter after computing group to avoid duplicating parsing.

    # Date range
    df = request.GET.get("df")
    dt = request.GET.get("dt")
    if df:
        qlines = qlines.filter(entry__posted_at__gte=df)
    if dt:
        qlines = qlines.filter(entry__posted_at__lte=dt)

    # Amount range (minor = SYP)
    min_amt = request.GET.get("min_amt")
    max_amt = request.GET.get("max_amt")
    if min_amt and min_amt.isdigit():
        qlines = qlines.filter(amount_minor__gte=int(min_amt))
    if max_amt and max_amt.isdigit():
        qlines = qlines.filter(amount_minor__lte=int(max_amt))

    rows = []
    scanned = 0
    # We may need to skip rows that don't fit mtype/party filters → overfetch a little
    for ln in qlines[: limit * 3]:
        e = ln.entry
        group, action_label = _nice_action(e, ln)
        # mtype mapping
        group_map = {
            "bill": "BILL",
            "provider_return": "PROVIDER_RETURN",
            "debt": "DEBT",        # future use
            "all": None,
        }
        if mtype != "all":
            if group_map.get(mtype) and group != group_map[mtype]:
                continue

        ptype, pname = _resolve_party(e)
        # party filters
        pf_type = (request.GET.get("party_type") or "").strip().lower()
        pf_name = (request.GET.get("party_name") or "").strip()
        if pf_type and pf_type != ptype:
            continue
        if pf_name and (pname or "").find(pf_name) == -1:
            continue

        effect_str = _effect_for_line(ln.dc)
        rows.append({
            "id": e.id,
            "ts": e.posted_at.isoformat(),
            "date_disp": e.posted_at.strftime("%m/%d/%y, %H:%M:%S"),
            "effect": effect_str,                 # "UP" / "DOWN"
            "amount_minor": ln.amount_minor,
            "amount_disp": _format_amount_minor(ln.amount_minor),
            "action": action_label,               # now Arabic for debts too
            "party_type": ptype,
            "party_type_disp": _party_label(ptype),
            "party_name": pname,
        })
        scanned += 1
        if len(rows) >= limit:
            break

    next_cursor = None
    if rows:
        last = rows[-1]
        next_cursor = f"{last['ts']}|{last['id']}"

    return JsonResponse({"rows": rows, "next_cursor": next_cursor})

# ---------- API: SUMMARY (SAFE balance) ----------

@login_required
@manager_required
def api_volt_summary(request: HttpRequest) -> JsonResponse:
    try:
        bal = cash_account_balance(SAFE_CODE)
    except Exception:
        bal = 0
    return JsonResponse({
        "safe_code": SAFE_CODE,
        "balance_minor": bal,
        "balance_disp": _format_amount_minor(bal),
    })


# ---------- API: Party autocomplete (providers for now) ----------

@login_required
@manager_required
def api_volt_party_ac(request: HttpRequest) -> JsonResponse:
    q = (request.GET.get("q") or "").strip()
    results = []
    if Provider and q:
        for p in Provider.objects.filter(name__icontains=q).order_by("name")[:12]:
            results.append({"type": "provider", "name": p.name})
    return JsonResponse({"items": results})
