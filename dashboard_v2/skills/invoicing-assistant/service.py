"""
Invoicing Assistant — Invoice stage, tasks #1–#3.

Takes the bound binders for one client and produces the numbers the invoice
request needs: premium, commission, and every tax, fee and surcharge, per binder
and totalled across the placement, with the client's CN and billing ID billing ID
attached from config/clients.json.

Two engines, one row per binder:

  identity — the shared loss-run extraction engine in engine/loss_run.py.
             It already resolves insured, carrier, policy number, coverage and
             effective date and is graded at 99.5% across 80 binders, so this
             skill points at it rather than growing a second copy.

  money    — money.py, next door. Pure text in, structured amounts out.

Nothing here is authoritative on its own: every figure lands in an editable
table, and rows that fail to reconcile against the total the binder printed say
so instead of quietly balancing.
"""
from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from . import money

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parents[2]
_CLIENTS = _ROOT / "config" / "clients.json"

ALLOWED_EXTENSIONS = {".pdf"}

# How many pages of a binder to read for billing content. The billing block is
# almost always in the first handful; a 200-page policy attachment is not worth
# the parse.
_SCAN_PAGES = 20
_BILLING_PAGES = 5


# ── The shared extraction engine ────────────────────────────────────────

# Identity fields (insured, carrier, policy number, coverage, effective
# date) come from the graded engine rather than a second copy grown here.
from engine import loss_run as _engine


# ── Client billing identifiers ───────────────────────────────────────────────

_clients_cache: Optional[list] = None


def _clients() -> list:
    global _clients_cache
    if _clients_cache is None:
        try:
            _clients_cache = json.loads(_CLIENTS.read_text(encoding="utf-8")).get("clients", [])
        except Exception:
            _clients_cache = []
    return _clients_cache


def _config_billing_ids(entry: dict):
    """Billing ids for one client entry, as configured."""
    return entry.get("billing_ids")


def _billing_id_list(value) -> list[str]:
    """Billing ids as a list of strings.

    An account can carry more than one, so config holds a list — but a single
    id written as a bare string or a JSON number still reads correctly, and the
    ids stay strings so a 12-digit value keeps its length and leading digits.
    """
    if value in (None, ""):
        return []
    if isinstance(value, (str, int)):
        value = [value]
    return [str(v).strip() for v in value if str(v).strip()]


def billing_ids(client_key: str = "", display_name: str = "", billing_id: str = "") -> dict:
    """CN and billing ID ids for a client, by folder_name or display name.

    `billing_id` is the id the user picked; it is echoed back when it is one this
    client actually has, and a client with exactly one id needs no picking.
    Both fields are absent from config until someone fills them in, so the shape
    of the answer never changes — only whether the values are there.
    """
    key = (client_key or "").strip().lower()
    name = (display_name or "").strip().lower()
    for c in _clients():
        if key and c.get("folder_name", "").strip().lower() == key:
            match = c
            break
        if name and c.get("display_name", "").strip().lower() == name:
            match = c
            break
    else:
        return {"client": display_name or client_key, "cn": "", "billing_id": "",
                "billing_id_options": [], "missing": ["CN", "Billing ID"]}

    cn = str(match.get("cn", "") or "").strip()
    options = _billing_id_list(_config_billing_ids(match))

    selected = str(billing_id or "").strip()
    if selected not in options:
        selected = options[0] if len(options) == 1 else ""

    missing = [label for label, value in (("CN", cn), ("Billing ID", options)) if not value]
    return {
        "client": match.get("display_name", ""),
        "folder_name": match.get("folder_name", ""),
        "cn": cn,
        "billing_id": selected,
        "billing_id_options": options,
        "missing": missing,
    }


def client_options() -> list[dict]:
    """Every active client, with its billing IDs, for the picker."""
    out = []
    for c in _clients():
        if not c.get("active", True):
            continue
        out.append({
            "display_name": c.get("display_name", ""),
            "folder_name": c.get("folder_name", ""),
            "cn": str(c.get("cn", "") or "").strip(),
            "billing_id_options": _billing_id_list(_config_billing_ids(c)),
        })
    return out


# ── One row per binder ───────────────────────────────────────────────────────

@dataclass
class BinderRow:
    filename:          str = ""
    # identity, from the loss-run engine
    insured_raw:       str = ""
    insured_resolved:  str = ""
    client_key:        str = ""
    carrier_group:     str = ""
    carrier_raw:       str = ""
    policy_number:     str = ""
    coverage_category: str = ""
    effective_date:    str = ""
    # money, from money.py
    premium:           Optional[float] = None
    premium_basis:     str = ""
    commission_pct:    Optional[float] = None
    commission_amt:    Optional[float] = None
    commission_basis:  str = ""
    charges:           list = field(default_factory=list)
    excluded:          list = field(default_factory=list)
    charges_total:     float = 0.0
    # A separately stated terrorism (TRIA) premium. Carriers print it both ways:
    # inside the premium figure, or added on top of it. money.py decides which
    # from the arithmetic and reports it here so the UI can show it as its own
    # billable line when it is additive.
    terrorism:         Optional[float] = None
    terrorism_included_in_premium: bool = True
    total:             float = 0.0
    printed_total:     Optional[float] = None
    net_due:           Optional[float] = None
    reconciliation:    str = ""
    confidence:        int = 0
    notes:             list = field(default_factory=list)
    error:             str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _billing_pages(file_bytes: bytes) -> list[dict]:
    """The pages of a binder most likely to carry the billing block, in
    layout-preserving text — columns line up, so a label keeps its number."""
    cheap = _engine._pdf_page_texts(file_bytes, _SCAN_PAGES, early_stop=False)
    if not cheap:
        return []

    scored = sorted(
        ((money.score_page_for_billing(text), idx) for idx, text in enumerate(cheap)),
        key=lambda pair: (-pair[0], pair[1]),
    )
    wanted = [idx for score, idx in scored if score > 0][:_BILLING_PAGES]
    if not wanted:
        wanted = list(range(min(3, len(cheap))))

    layout = _engine._pdf_layout_text(file_bytes, wanted)
    pages = []
    for idx in wanted:
        text = layout.get(idx, "") or (cheap[idx] if idx < len(cheap) else "")
        if text.strip():
            pages.append({"index": idx, "text": text})
    return pages


def extract_rows(files: list[tuple[bytes, str]]) -> list[dict]:
    """One row per binder. Extraction failures never raise — the row comes back
    with an error on it so the rest of the batch still lands."""
    identity = {}
    try:
        for row in _engine.process_batch(files):
            identity[row.filename] = row
    except Exception:
        identity = {}

    rows: list[BinderRow] = []
    for file_bytes, filename in files:
        row = BinderRow(filename=filename)

        ident = identity.get(filename)
        if ident is not None:
            row.insured_raw       = ident.insured_raw
            row.insured_resolved  = ident.insured_resolved
            row.client_key        = ident.client_key
            row.carrier_group     = ident.carrier_group
            row.carrier_raw       = ident.carrier_raw
            row.policy_number     = ident.policy_number
            row.coverage_category = ident.coverage_category
            row.effective_date    = ident.effective_date

        try:
            pages = _billing_pages(file_bytes)
            if not pages:
                row.error = "No readable text — the PDF may be a scan."
                rows.append(row)
                continue

            result = money.extract(pages, rank=False)
            row.premium          = result["premium"]
            row.premium_basis    = result["premium_basis"]
            row.commission_pct   = result["commission_pct"]
            row.commission_amt   = result["commission_amt"]
            row.commission_basis = result["commission_basis"]
            row.charges          = result["charges"]
            row.excluded         = result["excluded"]
            row.charges_total    = result["charges_total"]
            row.terrorism        = result.get("terrorism")
            row.terrorism_included_in_premium = result.get(
                "terrorism_included_in_premium", True)
            row.total            = result["total"]
            row.printed_total    = result["printed_total"]
            row.net_due          = result["net_due"]
            row.reconciliation   = result["reconciliation"]
            row.confidence       = result["confidence"]
            row.notes            = result["notes"]
        except Exception as e:
            row.error = f"Extraction failed: {e}"

        rows.append(row)

    return [r.to_dict() for r in rows]


# ── Placement-level totals ───────────────────────────────────────────────────

def _num(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def summarize(rows: list[dict]) -> dict:
    """Totals across every binder in the placement, plus what still needs a look."""
    premium    = round(sum(_num(r.get("premium")) for r in rows), 2)
    commission = round(sum(_num(r.get("commission_amt")) for r in rows), 2)
    charges    = round(sum(_num(r.get("charges_total")) for r in rows), 2)
    total      = round(sum(_num(r.get("total")) for r in rows), 2)

    by_kind: dict[str, float] = {}
    for r in rows:
        for line in r.get("charges", []):
            kind = line.get("kind", "other")
            by_kind[kind] = round(by_kind.get(kind, 0.0) + _num(line.get("amount")), 2)

    flagged = [r["filename"] for r in rows
               if r.get("error")
               or r.get("premium") in (None, "")
               or str(r.get("reconciliation", "")).startswith("off")]

    return {
        "binders":          len(rows),
        "premium":          premium,
        "commission":       commission,
        "commission_pct":   round(commission / premium * 100, 2) if premium else None,
        "charges_total":    charges,
        "charges_by_kind":  by_kind,
        "total":            total,
        "flagged":          flagged,
    }


# ── Export ───────────────────────────────────────────────────────────────────

_CSV_COLUMNS = [
    "Binder", "Carrier", "Policy Number", "Coverage", "Effective Date",
    "Line", "Type", "Amount",
]


def _billing_id_prompt(client: dict) -> str:
    """What to print where an billing ID should be but none was picked."""
    count = len(client.get("billing_id_options") or [])
    return f"[select one of {count}]" if count else "[missing]"


def build_csv(rows: list[dict], client: dict) -> str:
    """One row per money line, so the invoice request can be checked line by line."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")

    writer.writerow(["Client", client.get("client", "")])
    writer.writerow(["CN", client.get("cn", "")])
    writer.writerow(["Billing ID", client.get("billing_id", "") or _billing_id_prompt(client)])
    writer.writerow([])
    writer.writerow(_CSV_COLUMNS)

    for r in rows:
        head = [r.get("filename", ""), r.get("carrier_group", "") or r.get("carrier_raw", ""),
                r.get("policy_number", ""), r.get("coverage_category", ""),
                r.get("effective_date", "")]

        writer.writerow(head + ["Premium", "premium", _num(r.get("premium"))])

        if r.get("commission_amt") is not None:
            pct = r.get("commission_pct")
            label = f"Commission ({pct}%)" if pct is not None else "Commission"
            writer.writerow(head + [label, "commission", _num(r.get("commission_amt"))])

        for line in r.get("charges", []):
            writer.writerow(head + [line.get("label", ""), line.get("kind", ""),
                                    _num(line.get("amount"))])

        writer.writerow(head + ["Binder total", "total", _num(r.get("total"))])

    summary = summarize(rows)
    writer.writerow([])
    writer.writerow(["PLACEMENT TOTAL", "", "", "", "", "Premium", "premium", summary["premium"]])
    writer.writerow(["PLACEMENT TOTAL", "", "", "", "", "Commission", "commission",
                     summary["commission"]])
    writer.writerow(["PLACEMENT TOTAL", "", "", "", "", "Taxes, fees & surcharges",
                     "charges", summary["charges_total"]])
    writer.writerow(["PLACEMENT TOTAL", "", "", "", "", "Total billed", "total",
                     summary["total"]])
    return buffer.getvalue()


def build_summary_text(rows: list[dict], client: dict) -> str:
    """A paste-ready block for the invoice request email."""
    summary = summarize(rows)
    out = [
        f"Client: {client.get('client', '')}",
        f"CN: {client.get('cn') or '[missing]'}    "
        f"Billing ID: {client.get('billing_id') or _billing_id_prompt(client)}",
        "",
        f"{summary['binders']} binder(s)",
        "",
    ]
    for r in rows:
        carrier = r.get("carrier_group") or r.get("carrier_raw") or "[carrier]"
        coverage = r.get("coverage_category") or "[coverage]"
        out.append(f"{coverage} — {carrier} — {r.get('policy_number') or '[policy no.]'}")
        out.append(f"    Premium{'':>22}{_num(r.get('premium')):>14,.2f}")
        if r.get("commission_amt") is not None:
            pct = r.get("commission_pct")
            tag = f"Commission ({pct}%)" if pct is not None else "Commission"
            out.append(f"    {tag:<29}{_num(r.get('commission_amt')):>14,.2f}")
        for line in r.get("charges", []):
            out.append(f"    {line.get('label', '')[:29]:<29}{_num(line.get('amount')):>14,.2f}")
        out.append(f"    {'Binder total':<29}{_num(r.get('total')):>14,.2f}")
        if str(r.get("reconciliation", "")).startswith("off"):
            out.append(f"    ** {r['reconciliation']} — check before invoicing")
        out.append("")

    out += [
        "-" * 45,
        f"{'Premium':<29}{summary['premium']:>14,.2f}",
        f"{'Commission':<29}{summary['commission']:>14,.2f}",
        f"{'Taxes, fees & surcharges':<29}{summary['charges_total']:>14,.2f}",
        f"{'Total billed':<29}{summary['total']:>14,.2f}",
    ]
    if summary["flagged"]:
        out += ["", "Needs review before sending:"] + [f"  - {f}" for f in summary["flagged"]]
    return "\n".join(out)
