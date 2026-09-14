"""Generate a synthetic insurance program for two sample clients.

Each client gets a full tower of binders plus a premium invoice, so the Loss Run
Request and Invoicing Assistant skills can be demonstrated end to end without
real documents. Every figure is fabricated.

Carriers
--------
Most of the tower sits on ADMITTED paper, with the carriers matched to the lines
they actually write in the US admitted market:

  GL / Auto / WC      Travelers, Hartford - the large admitted casualty writers.
                      Fleet auto for a trucking risk goes to Old Republic, which
                      is a dominant admitted commercial-auto market.
  Umbrella / Excess   Zurich and CNA lead umbrellas on admitted paper; Great
                      American and Berkshire Hathaway Specialty write admitted
                      excess casualty above them.
  FINPRO              Chubb (Federal Insurance Company) and AIG (National Union)
                      are the two largest admitted D&O/EPL markets; Travelers
                      Casualty & Surety and CNA write fiduciary and crime.
  Cyber               Coalition and Beazley both issue on admitted paper.
  Marine cargo        Chubb and Starr are leading ocean-marine markets.
  Property            Zurich for a distribution risk; Affiliated FM for a
                      manufacturing/technology risk, which is its core appetite.

Admitted paper carries no surplus lines tax or stamping fee, so those binders
model the charges admitted business actually shows: separately stated terrorism
(TRIA) premium, state workers compensation assessments, and policy fees.

A real tower is not all admitted, though. High excess layers and tough-occupancy
property routinely sit on E&S paper, so each client also carries non-admitted
placements through Lexington (AIG), Westchester (Chubb), Evanston (Markel) and
Scottsdale (Nationwide). Those are the binders that carry a surplus lines tax, a
stamping fee and a municipal surcharge.

Between them the documents exercise every branch of the money parser: each
charge kind (tax, fee, surcharge, terrorism), premium components and
carrier-stated subtotals that must not be double counted, a binder printing only
a net-of-commission figure, and a charge whose label the vocabulary has never
seen, recoverable only from the hole it leaves against the printed total.

Label vocabulary
----------------
`engine/loss_run.py` keys off "Named Insured", "Insurance Carrier", "Policy
Number", "Policy Period" and "Line of Business"; the invoicing money parser keys
off "Total Premium", "Commission", "Terrorism Premium", "Assessment" and "Total
Amount Due". The documents below use those exact labels.

Output lands in uploads/sample-documents/ (gitignored - PDFs are never committed).

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
LABEL_W = 2.6 * inch


def _money(v: float) -> str:
    return f"${v:,.2f}"


class Doc:
    def __init__(self, path: pathlib.Path, title: str):
        self.c = canvas.Canvas(str(path), pagesize=LETTER)
        self.y = LETTER[1] - 0.9 * inch
        self.c.setFont("Helvetica-Bold", 14)
        self.c.drawString(LEFT, self.y, title)
        self.y -= 0.32 * inch
        self.c.setStrokeColorRGB(0.75, 0.75, 0.75)
        self.c.line(LEFT, self.y + 6, LETTER[0] - LEFT, self.y + 6)
        self.y -= 0.14 * inch

    def heading(self, text: str):
        self.y -= 0.1 * inch
        self.c.setFont("Helvetica-Bold", 10.5)
        self.c.drawString(LEFT, self.y, text)
        self.y -= 0.21 * inch

    def row(self, label: str, value: str, bold: bool = False):
        self.c.setFont("Helvetica", 10)
        self.c.drawString(LEFT, self.y, f"{label}:")
        self.c.setFont("Helvetica-Bold" if bold else "Helvetica", 10)
        self.c.drawString(LEFT + LABEL_W, self.y, value)
        self.y -= 0.205 * inch

    def save(self):
        self.y -= 0.08 * inch
        self.c.setFont("Helvetica-Oblique", 8.5)
        self.c.setFillColorRGB(0.45, 0.45, 0.45)
        self.c.drawString(LEFT, self.y, "Synthetic sample document. All figures fabricated; not a real binder.")
        self.c.setFillColorRGB(0, 0, 0)
        self.c.showPage()
        self.c.save()


def _charges_block(d: Doc, premium, commission_pct, charges):
    d.heading("PREMIUM SUMMARY")
    d.row("Total Premium", _money(premium))
    d.row("Commission", f"{commission_pct:.1f}% ({_money(round(premium * commission_pct / 100, 2))})")
    total = premium
    for label, amt in charges:
        d.row(label, _money(amt))
        total += amt
    d.row("Total Amount Due", _money(total), bold=True)
    return round(total, 2)


def binder(fn, *, insured, carrier, policy, period, coverage, limits,
           premium, commission_pct, charges=(), extra=(), underlying=None):
    d = Doc(OUT / fn, "BINDER OF INSURANCE")
    d.row("Named Insured", insured)
    d.row("Insurance Carrier", carrier)
    d.row("Policy Number", policy)
    d.row("Policy Period", period)
    d.row("Line of Business", coverage)
    d.row("Limit of Liability", limits)
    d.row("Paper", "Admitted")
    for label, value in extra:
        d.row(label, value)
    total = _charges_block(d, premium, commission_pct, charges)
    if underlying:
        # Kept in its own block below the premium summary. The extractor's
        # negative-context guard suppresses carrier and policy-number values
        # that sit near "underlying", which is exactly what should happen here.
        d.heading("UNDERLYING SCHEDULE")
        for label, value in underlying:
            d.row(label, value)
    d.save()
    return {"file": fn, "carrier": carrier, "coverage": coverage,
            "premium": premium, "total": total}


def invoice(fn, *, insured, carrier, policy, period, coverage,
            premium, commission_pct, charges=()):
    d = Doc(OUT / fn, "PREMIUM INVOICE")
    d.row("Named Insured", insured)
    d.row("Insurance Carrier", carrier)
    d.row("Policy Number", policy)
    d.row("Policy Period", period)
    d.row("Line of Business", coverage)
    total = _charges_block(d, premium, commission_pct, charges)
    d.heading("REMITTANCE")
    d.row("Payment Terms", "Net 30")
    d.save()
    return {"file": fn, "carrier": carrier, "coverage": coverage,
            "premium": premium, "total": total}


def surplus_binder(fn, *, insured, carrier, policy, period, coverage, limits,
                   premium, commission_pct, charges, licensee, home_state,
                   components=(), subtotals=(), underlying=None):
    """A non-admitted (surplus lines) placement.

    E&S paper is what actually carries a surplus lines tax and a stamping fee,
    so these are the documents that exercise that side of the money parser.
    """
    d = Doc(OUT / fn, "BINDER OF INSURANCE")
    d.row("Named Insured", insured)
    d.row("Insurance Carrier", carrier)
    d.row("Policy Number", policy)
    d.row("Policy Period", period)
    d.row("Line of Business", coverage)
    d.row("Limit of Liability", limits)
    d.row("Paper", "Non-Admitted (Surplus Lines)")
    d.row("Surplus Lines Licensee", licensee)
    d.row("Home State", home_state)

    d.heading("PREMIUM SUMMARY")
    for label, amt in components:
        d.row(label, _money(amt))
    d.row("Total Premium", _money(premium))
    d.row("Commission", f"{commission_pct:.1f}% ({_money(round(premium * commission_pct / 100, 2))})")
    total = premium
    for label, amt in charges:
        d.row(label, _money(amt))
        total += amt
    for label, amt in subtotals:
        d.row(label, _money(amt))
    d.row("Total Amount Due", _money(round(total, 2)), bold=True)
    if underlying:
        d.heading("UNDERLYING SCHEDULE")
        for label, value in underlying:
            d.row(label, value)
    d.save()
    return {"file": fn, "carrier": carrier, "coverage": coverage,
            "premium": premium, "total": round(total, 2)}


def net_due_binder(fn, *, insured, carrier, policy, period, coverage, limits,
                   premium, commission_pct):
    """A binder that prints only the net-of-commission figure.

    Some carriers state what the broker remits rather than what the client is
    billed. The parser has to reconcile against premium less commission instead
    of against a gross total; this is the document that proves it.
    """
    d = Doc(OUT / fn, "BINDER OF INSURANCE")
    d.row("Named Insured", insured)
    d.row("Insurance Carrier", carrier)
    d.row("Policy Number", policy)
    d.row("Policy Period", period)
    d.row("Line of Business", coverage)
    d.row("Limit of Liability", limits)
    d.row("Paper", "Admitted")
    d.heading("PREMIUM SUMMARY")
    commission = round(premium * commission_pct / 100, 2)
    d.row("Total Premium", _money(premium))
    d.row("Commission", f"{commission_pct:.1f}% ({_money(commission)})")
    d.row("Net Premium Due", _money(round(premium - commission, 2)), bold=True)
    d.save()
    return {"file": fn, "carrier": carrier, "coverage": coverage,
            "premium": premium, "total": round(premium - commission, 2)}


TRAVELERS = "Travelers Property Casualty Company of America"
TRAV_SURETY = "Travelers Casualty and Surety Company of America"
OLD_REPUBLIC = "Old Republic Insurance Company"
ZURICH = "Zurich American Insurance Company"
GREAT_AMERICAN = "Great American Insurance Company"
CHUBB = "Federal Insurance Company"
COALITION = "Coalition Insurance Company"
HARTFORD = "Hartford Fire Insurance Company"
CNA = "Continental Casualty Company"
BHSI = "Berkshire Hathaway Specialty Insurance Company"
AIG = "National Union Fire Insurance Company of Pittsburgh, Pa."
BEAZLEY = "Beazley Insurance Company, Inc."
STARR = "Starr Indemnity & Liability Company"
AFM = "Affiliated FM Insurance Company"

# Non-admitted (E&S) paper. Real towers mix admitted and surplus lines: high
# excess layers and tough-occupancy property routinely sit on E&S paper, which
# is what carries surplus lines tax and a stamping fee.
LEXINGTON = "Lexington Insurance Company"                      # AIG E&S
WESTCHESTER = "Westchester Surplus Lines Insurance Company"    # Chubb E&S
EVANSTON = "Evanston Insurance Company"                        # Markel E&S
SCOTTSDALE = "Scottsdale Insurance Company"                    # Nationwide E&S


def vantage():
    """Freight and logistics: heavy fleet and workers compensation exposure."""
    V = "Vantage Logistics Holdings, L.P."
    CAS = "01/16/2027 to 01/16/2028"      # casualty + FINPRO track
    PROP = "12/31/2026 to 12/31/2027"     # property track
    CYB = "03/21/2027 to 03/21/2028"      # cyber track
    INTL = "05/01/2027 to 05/01/2028"     # international / marine track
    out = []

    # ── GAWC ────────────────────────────────────────────────────────────────
    out.append(binder("vantage_01_general_liability_binder.pdf", insured=V, carrier=TRAVELERS,
        policy="VL-GL-2027-004417", period=CAS, coverage="Commercial General Liability",
        limits="$1,000,000 per occurrence / $2,000,000 general aggregate",
        premium=186_400.00, commission_pct=12.5,
        charges=[("Terrorism Premium", 3_728.00), ("Policy Fee", 750.00)]))

    out.append(binder("vantage_02_commercial_auto_binder.pdf", insured=V, carrier=OLD_REPUBLIC,
        policy="VL-CA-2027-118840", period=CAS, coverage="Commercial Automobile Liability",
        limits="$1,000,000 combined single limit",
        premium=412_000.00, commission_pct=12.5,
        extra=[("Fleet Size", "284 power units / 611 trailers")],
        charges=[("Terrorism Premium", 4_120.00)]))

    out.append(binder("vantage_03_workers_compensation_binder.pdf", insured=V, carrier=TRAVELERS,
        policy="VL-WC-2027-770901", period=CAS, coverage="Workers Compensation and Employers Liability",
        limits="Statutory / $1,000,000 Employers Liability",
        premium=298_500.00, commission_pct=10.0,
        extra=[("Experience Modifier", "0.94")],
        charges=[("State Assessment", 5_970.00)]))

    out.append(binder("vantage_04_umbrella_binder.pdf", insured=V, carrier=ZURICH,
        policy="VL-UM-2027-330218", period=CAS, coverage="Commercial Umbrella Liability",
        limits="$10,000,000 each occurrence and aggregate",
        premium=124_000.00, commission_pct=12.5,
        charges=[("Terrorism Premium", 2_480.00)]))

    # ── Excess $15M xs $10M ─────────────────────────────────────────────────
    out.append(binder("vantage_05_excess_liability_binder.pdf", insured=V, carrier=GREAT_AMERICAN,
        policy="VL-XS-2027-556104", period=CAS, coverage="Excess Liability",
        limits="$15,000,000 excess of $10,000,000",
        premium=86_500.00, commission_pct=12.5,
        charges=[("Terrorism Premium", 1_730.00)],
        underlying=[("Underlying Umbrella Carrier", ZURICH),
                    ("Underlying Limit", "$10,000,000"),
                    ("Underlying Policy", "VL-UM-2027-330218")]))

    # ── FINPRO ──────────────────────────────────────────────────────────────
    out.append(binder("vantage_06_directors_officers_binder.pdf", insured=V, carrier=CHUBB,
        policy="VL-DO-2027-889012", period=CAS, coverage="Directors and Officers Liability",
        limits="$10,000,000 aggregate", premium=54_200.00, commission_pct=15.0))

    out.append(binder("vantage_07_employment_practices_binder.pdf", insured=V, carrier=CHUBB,
        policy="VL-EPL-2027-889013", period=CAS, coverage="Employment Practices Liability",
        limits="$5,000,000 aggregate", premium=38_900.00, commission_pct=15.0))

    out.append(binder("vantage_08_fiduciary_binder.pdf", insured=V, carrier=TRAV_SURETY,
        policy="VL-FID-2027-114227", period=CAS, coverage="Fiduciary Liability",
        limits="$5,000,000 aggregate", premium=12_400.00, commission_pct=15.0))

    out.append(binder("vantage_09_crime_binder.pdf", insured=V, carrier=TRAV_SURETY,
        policy="VL-CR-2027-114228", period=CAS, coverage="Crime",
        limits="$5,000,000 each loss", premium=16_800.00, commission_pct=15.0))

    # ── Cyber, marine, property ─────────────────────────────────────────────
    out.append(binder("vantage_10_cyber_binder.pdf", insured=V, carrier=COALITION,
        policy="VL-CY-2027-770142", period=CYB, coverage="Cyber and Technology E&O",
        limits="$5,000,000 each claim and aggregate",
        premium=68_500.00, commission_pct=15.0, charges=[("Policy Fee", 500.00)]))

    out.append(binder("vantage_11_marine_cargo_binder.pdf", insured=V, carrier=CHUBB,
        policy="VL-MC-2027-640311", period=INTL, coverage="Marine Cargo",
        limits="$2,500,000 any one conveyance",
        premium=42_300.00, commission_pct=12.5))

    out.append(binder("vantage_12_property_binder.pdf", insured=V, carrier=ZURICH,
        policy="VL-PR-2026-118820", period=PROP, coverage="Commercial Property",
        limits="$45,000,000 blanket building and contents",
        premium=242_850.00, commission_pct=10.0,
        charges=[("Terrorism Premium", 4_857.00), ("Inspection Fee", 1_250.00)]))

    # 2nd excess layer on E&S paper: surplus lines tax, stamping fee, surcharge.
    out.append(surplus_binder("vantage_14_excess_2nd_layer_binder.pdf", insured=V, carrier=LEXINGTON,
        policy="VL-XS2-2027-771903", period=CAS, coverage="Excess Liability",
        limits="$25,000,000 excess of $25,000,000",
        premium=64_800.00, commission_pct=12.5,
        licensee="Ashford Surplus Brokers LLC", home_state="Illinois",
        charges=[("Surplus Lines Tax", 2_268.00), ("Stamping Fee", 25.92),
                 ("Municipal Surcharge", 648.00)],
        underlying=[("Underlying Excess Carrier", GREAT_AMERICAN),
                    ("Underlying Limit", "$25,000,000 total"),
                    ("Underlying Policy", "VL-XS-2027-556104")]))

    # Blended package: per-coverage premium components plus carrier-stated
    # subtotals, which the parser must read without double counting.
    out.append(surplus_binder("vantage_15_package_binder.pdf", insured=V, carrier=WESTCHESTER,
        policy="VL-PKG-2027-449028", period=CAS, coverage="Commercial Package",
        limits="Per schedule on file", premium=96_500.00, commission_pct=12.5,
        licensee="Ashford Surplus Brokers LLC", home_state="Illinois",
        components=[("Property Premium", 58_200.00), ("General Liability Premium", 38_300.00)],
        charges=[("Surplus Lines Tax", 3_377.50), ("Stamping Fee", 38.60),
                 ("Policy Fee", 500.00)],
        subtotals=[("Total Taxes", 3_377.50), ("Total Fees", 538.60)]))

    out.append(invoice("vantage_13_casualty_invoice.pdf", insured=V, carrier=TRAVELERS,
        policy="VL-GL-2027-004417", period=CAS, coverage="Commercial General Liability",
        premium=186_400.00, commission_pct=12.5,
        charges=[("Terrorism Premium", 3_728.00), ("Policy Fee", 750.00)]))
    return out


def kestrel():
    """Technology manufacturer: lighter fleet, heavier FINPRO and cyber."""
    K = "Kestrel Robotics Inc"
    PC = "10/31/2026 to 10/31/2027"       # P&C track
    CYB = "02/28/2027 to 02/28/2028"      # cyber track
    out = []

    out.append(binder("kestrel_01_general_liability_binder.pdf", insured=K, carrier=HARTFORD,
        policy="KR-GL-2026-220145", period=PC, coverage="Commercial General Liability",
        limits="$1,000,000 per occurrence / $2,000,000 general aggregate",
        premium=62_800.00, commission_pct=12.5,
        charges=[("Terrorism Premium", 1_256.00), ("Policy Fee", 350.00)]))

    out.append(binder("kestrel_02_commercial_auto_binder.pdf", insured=K, carrier=HARTFORD,
        policy="KR-CA-2026-220146", period=PC, coverage="Commercial Automobile Liability",
        limits="$1,000,000 combined single limit",
        premium=48_300.00, commission_pct=12.5,
        extra=[("Fleet Size", "42 owned vehicles")],
        charges=[("Terrorism Premium", 483.00)]))

    out.append(binder("kestrel_03_workers_compensation_binder.pdf", insured=K, carrier=HARTFORD,
        policy="KR-WC-2026-220147", period=PC, coverage="Workers Compensation and Employers Liability",
        limits="Statutory / $1,000,000 Employers Liability",
        premium=94_600.00, commission_pct=10.0,
        extra=[("Experience Modifier", "0.88")],
        charges=[("State Assessment", 1_892.00)]))

    out.append(binder("kestrel_04_umbrella_binder.pdf", insured=K, carrier=CNA,
        policy="KR-UM-2026-448120", period=PC, coverage="Commercial Umbrella Liability",
        limits="$10,000,000 each occurrence and aggregate",
        premium=58_200.00, commission_pct=12.5,
        charges=[("Terrorism Premium", 1_164.00)]))

    out.append(binder("kestrel_05_excess_liability_binder.pdf", insured=K, carrier=BHSI,
        policy="KR-XS-2026-901772", period=PC, coverage="Excess Liability",
        limits="$15,000,000 excess of $10,000,000",
        premium=41_700.00, commission_pct=12.5,
        charges=[("Terrorism Premium", 834.00)],
        underlying=[("Underlying Umbrella Carrier", CNA),
                    ("Underlying Limit", "$10,000,000"),
                    ("Underlying Policy", "KR-UM-2026-448120")]))

    out.append(binder("kestrel_06_directors_officers_binder.pdf", insured=K, carrier=AIG,
        policy="KR-DO-2026-556301", period=PC, coverage="Directors and Officers Liability",
        limits="$10,000,000 aggregate", premium=94_200.00, commission_pct=15.0))

    out.append(binder("kestrel_07_employment_practices_binder.pdf", insured=K, carrier=AIG,
        policy="KR-EPL-2026-556302", period=PC, coverage="Employment Practices Liability",
        limits="$5,000,000 aggregate", premium=46_500.00, commission_pct=15.0))

    out.append(binder("kestrel_08_fiduciary_binder.pdf", insured=K, carrier=CNA,
        policy="KR-FID-2026-448121", period=PC, coverage="Fiduciary Liability",
        limits="$3,000,000 aggregate", premium=14_900.00, commission_pct=15.0))

    out.append(binder("kestrel_09_crime_binder.pdf", insured=K, carrier=CNA,
        policy="KR-CR-2026-448122", period=PC, coverage="Crime",
        limits="$3,000,000 each loss", premium=18_200.00, commission_pct=15.0))

    out.append(binder("kestrel_10_cyber_binder.pdf", insured=K, carrier=BEAZLEY,
        policy="KR-CY-2027-310556", period=CYB, coverage="Cyber and Technology E&O",
        limits="$10,000,000 each claim and aggregate",
        premium=77_400.00, commission_pct=15.0, charges=[("Policy Fee", 500.00)]))

    out.append(binder("kestrel_11_marine_cargo_binder.pdf", insured=K, carrier=STARR,
        policy="KR-MC-2027-201884", period=CYB, coverage="Marine Cargo",
        limits="$2,500,000 any one conveyance",
        premium=37_750.00, commission_pct=12.5, charges=[("Policy Fee", 350.00)]))

    out.append(binder("kestrel_12_property_binder.pdf", insured=K, carrier=AFM,
        policy="KR-PR-2026-770430", period=PC, coverage="Commercial Property",
        limits="$62,000,000 blanket building, contents and equipment",
        premium=168_400.00, commission_pct=10.0,
        charges=[("Terrorism Premium", 3_368.00)]))

    out.append(surplus_binder("kestrel_14_excess_2nd_layer_binder.pdf", insured=K, carrier=EVANSTON,
        policy="KR-XS2-2026-660412", period=PC, coverage="Excess Liability",
        limits="$25,000,000 excess of $25,000,000",
        premium=28_400.00, commission_pct=12.5,
        licensee="Ashford Surplus Brokers LLC", home_state="Massachusetts",
        charges=[("Surplus Lines Tax", 1_136.00), ("Stamping Fee", 11.36)],
        underlying=[("Underlying Excess Carrier", BHSI),
                    ("Underlying Limit", "$25,000,000 total"),
                    ("Underlying Policy", "KR-XS-2026-901772")]))

    # Carries a charge the label vocabulary has never seen. It is identifiable
    # only by the hole it leaves against the printed total, which is the gap
    # adoption path.
    out.append(surplus_binder("kestrel_15_property_surplus_binder.pdf", insured=K, carrier=SCOTTSDALE,
        policy="KR-PRX-2026-118377", period=PC, coverage="Commercial Property",
        limits="$15,000,000 excess property", premium=51_600.00, commission_pct=12.5,
        licensee="Ashford Surplus Brokers LLC", home_state="Massachusetts",
        charges=[("Surplus Lines Tax", 2_064.00), ("Stamping Fee", 20.64),
                 ("Guaranty Association Recoupment", 387.00)]))

    # Prints only what the broker remits, not what the client is billed.
    out.append(net_due_binder("kestrel_16_equipment_floater_binder.pdf", insured=K, carrier=HARTFORD,
        policy="KR-EF-2026-220148", period=PC, coverage="Inland Marine",
        limits="$4,000,000 scheduled equipment",
        premium=22_800.00, commission_pct=12.5))

    out.append(invoice("kestrel_13_directors_officers_invoice.pdf", insured=K, carrier=AIG,
        policy="KR-DO-2026-556301", period=PC, coverage="Directors and Officers Liability",
        premium=94_200.00, commission_pct=15.0))
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.pdf"):
        old.unlink()

    made = vantage() + kestrel()
    print(f"wrote {len(made)} documents to {OUT.relative_to(ROOT)}\n")
    print(f"  {'file':<46}{'coverage':<40}{'carrier':<34}{'total':>13}")
    for m in made:
        print(f"  {m['file']:<46}{m['coverage'][:38]:<40}{m['carrier'][:32]:<34}{_money(m['total']):>13}")
    for label, pref in (("Vantage", "vantage_"), ("Kestrel", "kestrel_")):
        rows = [m for m in made if m["file"].startswith(pref) and "invoice" not in m["file"]]
        print(f"\n  {label}: {len(rows)} binders, premium {_money(sum(r['premium'] for r in rows))}, "
              f"billed {_money(sum(r['total'] for r in rows))}")


if __name__ == "__main__":
    main()
