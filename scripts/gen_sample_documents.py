"""Generate synthetic binder and invoice PDFs for two sample clients.

These exist so the Loss Run Request and Invoicing Assistant skills can be
demonstrated end to end without real client documents. Every figure is made up;
the carriers are real companies named only as the issuing market, which is what
the extraction engine keys on.

The label vocabulary matters: `engine/loss_run.py` keys off "Named Insured",
"Insurance Carrier", "Policy Number", "Policy Period" and "Line of Business",
and the invoicing money parser keys off "Total Premium", "Commission",
"Surplus Lines Tax", "Stamping Fee", "Policy Fee" and "Total Amount Due". The
documents below use those exact labels.

Output lands in uploads/sample-documents/ (gitignored — PDFs are never committed).

Run from the repo root:
    .venv/Scripts/python.exe scripts/gen_sample_documents.py
"""
from __future__ import annotations

import pathlib

from reportlab.lib.pagesizes import LETTER
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "uploads" / "sample-documents"

LEFT = 0.9 * inch
LABEL_W = 2.5 * inch


def _money(v: float) -> str:
    return f"${v:,.2f}"


class Doc:
    """Minimal two-column label/value PDF writer."""

    def __init__(self, path: pathlib.Path, title: str):
        self.c = canvas.Canvas(str(path), pagesize=LETTER)
        self.y = LETTER[1] - 0.9 * inch
        self.c.setFont("Helvetica-Bold", 14)
        self.c.drawString(LEFT, self.y, title)
        self.y -= 0.34 * inch

    def rule(self):
        self.c.setStrokeColorRGB(0.75, 0.75, 0.75)
        self.c.line(LEFT, self.y + 6, LETTER[0] - LEFT, self.y + 6)
        self.y -= 0.12 * inch

    def heading(self, text: str):
        self.y -= 0.1 * inch
        self.c.setFont("Helvetica-Bold", 10.5)
        self.c.drawString(LEFT, self.y, text)
        self.y -= 0.22 * inch

    def row(self, label: str, value: str, bold: bool = False):
        self.c.setFont("Helvetica", 10)
        self.c.drawString(LEFT, self.y, f"{label}:")
        self.c.setFont("Helvetica-Bold" if bold else "Helvetica", 10)
        self.c.drawString(LEFT + LABEL_W, self.y, value)
        self.y -= 0.215 * inch

    def note(self, text: str):
        self.c.setFont("Helvetica-Oblique", 8.5)
        self.c.setFillColorRGB(0.45, 0.45, 0.45)
        self.c.drawString(LEFT, self.y, text)
        self.c.setFillColorRGB(0, 0, 0)
        self.y -= 0.2 * inch

    def save(self):
        self.note("Synthetic sample document. Not a real binder or invoice; all figures are fabricated.")
        self.c.showPage()
        self.c.save()


def binder(fn, *, insured, carrier, policy, period, coverage, limits,
           premium, commission_pct, taxes=(), fees=(), surplus_lines=False):
    d = Doc(OUT / fn, "BINDER OF INSURANCE")
    d.rule()
    d.row("Named Insured", insured)
    d.row("Insurance Carrier", carrier)
    d.row("Policy Number", policy)
    d.row("Policy Period", period)
    d.row("Line of Business", coverage)
    d.row("Limit of Liability", limits)
    if surplus_lines:
        d.row("Surplus Lines Licensee", "Ashford Surplus Brokers LLC")
        d.row("Home State", "Illinois")

    d.heading("PREMIUM SUMMARY")
    commission = round(premium * commission_pct / 100, 2)
    d.row("Total Premium", _money(premium))
    d.row("Commission", f"{commission_pct:.1f}% ({_money(commission)})")
    total = premium
    for label, amt in taxes:
        d.row(label, _money(amt)); total += amt
    for label, amt in fees:
        d.row(label, _money(amt)); total += amt
    d.row("Total Amount Due", _money(total), bold=True)
    d.save()
    return {"file": fn, "carrier": carrier, "policy": policy,
            "premium": premium, "total": round(total, 2)}


def invoice(fn, *, insured, carrier, policy, period, coverage,
            premium, commission_pct, taxes=(), fees=()):
    d = Doc(OUT / fn, "PREMIUM INVOICE")
    d.rule()
    d.row("Named Insured", insured)
    d.row("Insurance Carrier", carrier)
    d.row("Policy Number", policy)
    d.row("Policy Period", period)
    d.row("Line of Business", coverage)

    d.heading("AMOUNTS")
    commission = round(premium * commission_pct / 100, 2)
    d.row("Total Premium", _money(premium))
    d.row("Commission", f"{commission_pct:.1f}% ({_money(commission)})")
    total = premium
    for label, amt in taxes:
        d.row(label, _money(amt)); total += amt
    for label, amt in fees:
        d.row(label, _money(amt)); total += amt
    d.row("Total Amount Due", _money(total), bold=True)
    d.heading("REMITTANCE")
    d.row("Payment Terms", "Net 30")
    d.save()
    return {"file": fn, "carrier": carrier, "policy": policy,
            "premium": premium, "total": round(total, 2)}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.pdf"):
        old.unlink()

    made = []
    V = "Vantage Logistics Holdings, L.P."
    K = "Kestrel Robotics Inc"

    # ── Vantage Logistics ────────────────────────────────────────────────────
    made.append(binder(
        "vantage_casualty_binder.pdf", insured=V,
        carrier="Travelers Property Casualty Company of America",
        policy="VL-GLA-2027-004417", period="01/16/2027 to 01/16/2028",
        coverage="Commercial General Liability", limits="$1,000,000 per occurrence / $2,000,000 aggregate",
        premium=186_400.00, commission_pct=12.5,
        fees=[("Policy Fee", 750.00)]))

    made.append(binder(
        "vantage_property_binder.pdf", insured=V,
        carrier="Chubb Custom Insurance Company",
        policy="VL-PR-2026-118820", period="12/31/2026 to 12/31/2027",
        coverage="Commercial Property", limits="$45,000,000 blanket building and contents",
        premium=242_850.00, commission_pct=10.0,
        fees=[("Inspection Fee", 1_250.00)]))

    made.append(binder(
        "vantage_cyber_binder.pdf", insured=V,
        carrier="QBE Specialty Insurance Company",
        policy="VL-CY-2027-770142", period="03/21/2027 to 03/21/2028",
        coverage="Cyber and Technology E&O", limits="$5,000,000 each claim and aggregate",
        premium=68_500.00, commission_pct=15.0, surplus_lines=True,
        taxes=[("Surplus Lines Tax", 2_397.50)],
        fees=[("Stamping Fee", 27.40), ("Policy Fee", 500.00)]))

    made.append(invoice(
        "vantage_casualty_invoice.pdf", insured=V,
        carrier="Travelers Property Casualty Company of America",
        policy="VL-GLA-2027-004417", period="01/16/2027 to 01/16/2028",
        coverage="Commercial General Liability",
        premium=186_400.00, commission_pct=12.5,
        fees=[("Policy Fee", 750.00)]))

    # ── Kestrel Robotics ─────────────────────────────────────────────────────
    made.append(binder(
        "kestrel_management_liability_binder.pdf", insured=K,
        carrier="Arch Specialty Insurance Company",
        policy="KR-DO-2026-556301", period="10/31/2026 to 10/31/2027",
        coverage="Directors and Officers Liability", limits="$10,000,000 aggregate",
        premium=94_200.00, commission_pct=15.0, surplus_lines=True,
        taxes=[("Surplus Lines Tax", 3_297.00)],
        fees=[("Stamping Fee", 37.68)]))

    made.append(binder(
        "kestrel_marine_cargo_binder.pdf", insured=K,
        carrier="Everest National Insurance Company",
        policy="KR-MC-2027-201884", period="02/28/2027 to 02/28/2028",
        coverage="Marine Cargo", limits="$2,500,000 any one conveyance",
        premium=37_750.00, commission_pct=12.5,
        fees=[("Policy Fee", 350.00)]))

    made.append(invoice(
        "kestrel_management_liability_invoice.pdf", insured=K,
        carrier="Arch Specialty Insurance Company",
        policy="KR-DO-2026-556301", period="10/31/2026 to 10/31/2027",
        coverage="Directors and Officers Liability",
        premium=94_200.00, commission_pct=15.0,
        taxes=[("Surplus Lines Tax", 3_297.00)],
        fees=[("Stamping Fee", 37.68)]))

    print(f"wrote {len(made)} documents to {OUT.relative_to(ROOT)}\n")
    for m in made:
        print(f"  {m['file']:<44} {m['carrier'][:34]:<36} {_money(m['total'])}")


if __name__ == "__main__":
    main()
