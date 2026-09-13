"""Deterministic seed data + PDF generation.

Targets the headline numbers for the Meridian Logistics book:
    47 claims  ·  12 carrier policies  ·  paid $412,655  ·  incurred $538,190

12 policies are on the book; 11 are retrievable. The 12th (Bluehaven) blocks on a
security question the vault cannot answer, so it escalates as `Input required` --
which is why the tracker reads "8 of 12" mid-run.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import config

TARGET_CLAIMS = 47
TARGET_PAID = 412_655.0
TARGET_INCURRED = 538_190.0

PERIOD_START = date(2021, 1, 1)
PERIOD_END = date(2025, 12, 31)
VALUATION_CURRENT = date(2026, 1, 12)
VALUATION_PRIOR = date(2025, 1, 15)


@dataclass
class Policy:
    policy_number: str
    carrier: str
    line: str
    account: str
    # policy number as the carrier's portal search actually wants it
    portal_search_value: str | None = None


@dataclass
class SeedClaim:
    claim_number: str
    policy_number: str
    carrier: str
    line: str
    date_of_loss: date
    status: str
    description: str
    paid: float
    expense: float
    reserved: float
    total_incurred: float
    litigation: bool = False
    # values as they appear on the large-loss-detail document, when they differ
    detail_overrides: dict = field(default_factory=dict)
    only_in_detail: bool = False
    # prior-valuation incurred, for the legitimate development case
    prior_incurred: float | None = None


ACCOUNTS = [
    {"id": "meridian", "name": "Meridian Logistics", "expires": date(2026, 3, 1)},
    {"id": "kestrel", "name": "Kestrel Foods Group", "expires": date(2026, 3, 15)},
    {"id": "aldridge", "name": "Aldridge Manufacturing", "expires": date(2026, 4, 1)},
    {"id": "northvale", "name": "Northvale Transit", "expires": date(2026, 4, 12)},
]

MERIDIAN_POLICIES = [
    Policy("NB-WC-100221", "Northbridge", "WC", "meridian"),
    Policy("NB-WC-100238", "Northbridge", "WC", "meridian"),
    Policy("NB-AU-100544", "Northbridge", "AUTO", "meridian"),
    Policy("HS-GL-220417", "Harborstone", "GL", "meridian"),
    Policy("HS-GL-220492", "Harborstone", "GL", "meridian"),
    # Redstone rejects separators; the agent must reformat to RS2210567
    Policy("RS-221-0567", "Redstone", "PROP", "meridian", portal_search_value="RS2210567"),
    Policy("RS-221-0912", "Redstone", "GL", "meridian", portal_search_value="RS2210912"),
    Policy("SM-AU-330190", "Summit", "AUTO", "meridian"),
    Policy("SM-AU-330244", "Summit", "AUTO", "meridian"),
    Policy("IW-PR-440255", "Ironwood", "PROP", "meridian"),
    Policy("IW-PR-440318", "Ironwood", "PROP", "meridian"),
    # blocks on a security question -> Input required, never retrieved
    Policy("BH-WC-550022", "Bluehaven", "WC", "meridian"),
]

RETRIEVABLE = [p for p in MERIDIAN_POLICIES if p.carrier != "Bluehaven"]

# The five claims shown on the summary card.
ANCHORS = [
    SeedClaim("WC-4471902", "NB-WC-100221", "Northbridge", "WC", date(2022, 3, 14),
              "closed", "Lifting injury - warehouse", 18_240, 0, 0, 18_240),
    SeedClaim("GL-0088314", "HS-GL-220417", "Harborstone", "GL", date(2022, 11, 2),
              "open", "Third party bodily injury - loading dock", 132_500, 0, 31_500, 164_000,
              litigation=True, detail_overrides={"total_incurred": 158_500}),
    SeedClaim("AU-7712045", "SM-AU-330190", "Summit", "AUTO", date(2023, 6, 29),
              "closed", "Tractor collision - I-80", 41_780, 0, 0, 47_300),
    SeedClaim("WC-4471988", "NB-WC-100238", "Northbridge", "WC", date(2024, 2, 8),
              "closed", "Slip and fall - yard", 6_915, 0, 0, 9_400),
    SeedClaim("PR-2210567", "IW-PR-440255", "Ironwood", "PROP", date(2024, 9, 17),
              "open", "Storm damage - roof", 0, 0, 25_000, 25_000),
]

DESCRIPTIONS = {
    "WC": ["Strain - repetitive motion", "Laceration - hand", "Fall from ladder",
           "Struck by falling freight", "Heat exhaustion - dock"],
    "GL": ["Slip and fall - customer", "Property damage - third party",
           "Product liability claim", "Premises liability"],
    "AUTO": ["Rear-end collision", "Backing incident - lot", "Cargo shift damage",
             "Windshield - road debris", "Jackknife - ice"],
    "PROP": ["Water damage - sprinkler", "Wind damage - siding",
             "Hail damage - roof", "Fire - electrical panel"],
}

CLAIM_PREFIX = {"WC": "WC", "GL": "GL", "AUTO": "AU", "PROP": "PR"}


# The legitimate-development claim. Sized to fit inside the book totals so the
# hero-card figures still reconcile exactly.
DEV_PRIOR_INCURRED = 18_200.0
DEV_PAID = 21_700.0
DEV_RESERVED = 20_000.0
DEV_INCURRED = DEV_PAID + DEV_RESERVED  # 41,700


def _attempt(seed: int) -> list[SeedClaim] | None:
    """Build the filler set for one RNG seed; None if the totals cannot balance."""
    rng = random.Random(seed)
    used = {c.claim_number for c in ANCHORS}
    pool = list(RETRIEVABLE)

    n_filler = TARGET_CLAIMS - len(ANCHORS)          # 42
    # random + date-conflict case + dev + balancer
    n_random = n_filler - 3

    def new_claim(i: int) -> SeedClaim:
        pol = pool[i % len(pool)]
        while True:
            num = f"{CLAIM_PREFIX[pol.line]}-{rng.randint(1_000_000, 9_999_999)}"
            if num not in used:
                used.add(num)
                break
        dol = PERIOD_START + timedelta(days=rng.randint(30, 1750))
        is_open = rng.random() < 0.25
        paid = float(rng.randrange(500, 9_000, 5))
        if is_open:
            reserved = float(rng.randrange(500, 6_000, 5))
            expense = float(rng.randrange(0, 400, 5))
        else:
            reserved = 0.0
            expense = float(rng.randrange(0, 400, 5))
        return SeedClaim(
            num, pol.policy_number, pol.carrier, pol.line, dol,
            "open" if is_open else "closed",
            rng.choice(DESCRIPTIONS[pol.line]),
            paid, expense, reserved, paid + reserved + expense,
            litigation=is_open and rng.random() < 0.2,
        )

    fillers = [new_claim(i) for i in range(n_random)]

    # A large Harborstone claim, so the two Harborstone documents can disagree
    # about its date of loss (discrepancy #2).
    hs_pol = next(p for p in RETRIEVABLE if p.carrier == "Harborstone")
    while True:
        hnum = f"GL-{rng.randint(1_000_000, 9_999_999)}"
        if hnum not in used:
            used.add(hnum)
            break
    date_case = SeedClaim(
        hnum, hs_pol.policy_number, "Harborstone", "GL",
        date(2023, 8, 22), "open", "Warehouse collapse - third party property",
        22_400.0, 0.0, 16_000.0, 38_400.0, litigation=True,
    )
    date_case.detail_overrides["date_of_loss"] = date_case.date_of_loss + timedelta(days=3)
    fillers.append(date_case)

    # The development claim must sit on a Summit policy (Summit sends two
    # valuations), and is fixed rather than random.
    summit_pol = next(p for p in RETRIEVABLE if p.carrier == "Summit")
    while True:
        dnum = f"{CLAIM_PREFIX[summit_pol.line]}-{rng.randint(1_000_000, 9_999_999)}"
        if dnum not in used:
            used.add(dnum)
            break
    dev = SeedClaim(
        dnum, summit_pol.policy_number, "Summit", summit_pol.line,
        date(2023, 11, 6), "open", "Trailer fire - total loss",
        DEV_PAID, 0.0, DEV_RESERVED, DEV_INCURRED,
        litigation=True, prior_incurred=DEV_PRIOR_INCURRED,
    )
    fillers.append(dev)

    # Balancer absorbs the remainder so the book totals land exactly.
    bal_pol = pool[n_random % len(pool)]
    while True:
        bnum = f"{CLAIM_PREFIX[bal_pol.line]}-{rng.randint(1_000_000, 9_999_999)}"
        if bnum not in used:
            used.add(bnum)
            break

    head_paid = sum(c.paid for c in fillers) + sum(c.paid for c in ANCHORS)
    head_inc = sum(c.total_incurred for c in fillers) + sum(c.total_incurred for c in ANCHORS)
    bal_paid = round(TARGET_PAID - head_paid, 2)
    bal_inc = round(TARGET_INCURRED - head_inc, 2)
    slack = round(bal_inc - bal_paid, 2)
    if bal_paid < 250 or slack < 0 or bal_inc < 250:
        return None

    balancer = SeedClaim(
        bnum, bal_pol.policy_number, bal_pol.carrier, bal_pol.line,
        PERIOD_START + timedelta(days=rng.randint(30, 1750)),
        "open" if slack > 0 else "closed",
        rng.choice(DESCRIPTIONS[bal_pol.line]),
        bal_paid, 0.0, slack, bal_inc,
    )
    fillers.append(balancer)
    return fillers


def _build_claims() -> list[SeedClaim]:
    """47 claims across the 11 retrievable policies, hitting the exact targets."""
    fillers = None
    for seed in range(20260301, 20260301 + 500):
        fillers = _attempt(seed)
        if fillers is not None:
            break
    if fillers is None:
        raise AssertionError("no feasible seed found for the target totals")

    claims: list[SeedClaim] = list(ANCHORS) + fillers

    # --- injected reconciliation cases -------------------------------------
    # (1) amount conflict on GL-0088314 -- declared on the anchor itself
    # (2) date-of-loss conflict -- declared on the fixed Harborstone claim above
    # (3) a large loss that appears ONLY on the detail document -- anomalous,
    #     a large loss should also appear on the full carrier run
    ghost = SeedClaim("GL-0089930", "HS-GL-220492", "Harborstone", "GL",
                      date(2023, 4, 19), "open",
                      "Contractor injury - third party action",
                      12_000.0, 0.0, 34_000.0, 46_000.0,
                      litigation=True, only_in_detail=True)
    claims.append(ghost)

    return claims


CLAIMS: list[SeedClaim] = _build_claims()


def claims_for_policy(policy_number: str, include_detail_only: bool = False) -> list[SeedClaim]:
    out = [c for c in CLAIMS if c.policy_number == policy_number]
    if not include_detail_only:
        out = [c for c in out if not c.only_in_detail]
    return sorted(out, key=lambda c: c.date_of_loss)


def claims_for_carrier(carrier: str, include_detail_only: bool = False) -> list[SeedClaim]:
    out = [c for c in CLAIMS if c.carrier == carrier]
    if not include_detail_only:
        out = [c for c in out if not c.only_in_detail]
    return sorted(out, key=lambda c: c.date_of_loss)


def book_totals() -> dict:
    counted = [c for c in CLAIMS if not c.only_in_detail]
    return {
        "claims": len(counted),
        "paid": round(sum(c.paid for c in counted), 2),
        "incurred": round(sum(c.total_incurred for c in counted), 2),
        "policies": len(MERIDIAN_POLICIES),
    }


# --------------------------------------------------------------------------
# PDF generation -- six visually distinct carrier layouts.
# --------------------------------------------------------------------------

def _fmt_money(v: float, style: str) -> str:
    if style == "bare":
        return f"{v:,.2f}"
    if style == "dollar":
        return f"${v:,.0f}"
    return f"{v:,.0f}"


def _fmt_date(d: date, style: str) -> str:
    if style == "iso":
        return d.isoformat()
    if style == "dmy":
        return d.strftime("%d-%b-%Y")
    return d.strftime("%m/%d/%Y")


# carrier -> (headers, date style, money style, column order)
LAYOUTS = {
    "Northbridge": {
        "headers": ["Claim No", "Date of Loss", "Cov", "Status", "Paid", "Reserved", "Total Incurred"],
        "date": "mdy", "money": "dollar",
        "fields": ["claim_number", "date_of_loss", "line", "status", "paid", "reserved", "total_incurred"],
    },
    "Harborstone": {
        "headers": ["Claim Number", "Loss Date", "Line", "Status", "Paid Loss", "Paid Exp", "Reserve", "Total Inc."],
        "date": "iso", "money": "bare",
        "fields": ["claim_number", "date_of_loss", "line", "status", "paid", "expense", "reserved", "total_incurred"],
    },
    "Redstone": {
        "headers": ["CLAIM", "DOL", "TYPE", "STATUS", "INDEMNITY PAID", "OUTSTANDING", "INCURRED"],
        "date": "dmy", "money": "plain",
        "fields": ["claim_number", "date_of_loss", "line", "status", "paid", "reserved", "total_incurred"],
    },
    "Summit": {
        "headers": ["Claim", "Date of Loss", "Coverage", "Open/Closed", "Paid", "Expense", "Reserve", "Incurred"],
        "date": "mdy", "money": "dollar",
        "fields": ["claim_number", "date_of_loss", "line", "status", "paid", "expense", "reserved", "total_incurred"],
    },
    "Ironwood": {
        "headers": ["Claim Ref", "Occurrence Date", "Peril", "Status", "Payments", "Reserves", "Total"],
        "date": "iso", "money": "dollar",
        "fields": ["claim_number", "date_of_loss", "line", "status", "paid", "reserved", "total_incurred"],
    },
    "Bluehaven": {
        "headers": ["Claim No", "Date of Loss", "Cov", "Status", "Paid", "Reserved", "Total Incurred"],
        "date": "mdy", "money": "dollar",
        "fields": ["claim_number", "date_of_loss", "line", "status", "paid", "reserved", "total_incurred"],
    },
}


def _value(c: SeedClaim, fieldname: str, layout: dict, use_detail: bool) -> str:
    ov = c.detail_overrides if use_detail else {}
    if fieldname == "claim_number":
        return c.claim_number
    if fieldname == "date_of_loss":
        return _fmt_date(ov.get("date_of_loss", c.date_of_loss), layout["date"])
    if fieldname == "line":
        return c.line
    if fieldname == "status":
        return c.status.upper()
    val = ov.get(fieldname, getattr(c, fieldname))
    return _fmt_money(float(val), layout["money"])


def build_pdf(path: Path, carrier: str, policy_number: str, claims: list[SeedClaim],
              valuation: date, title: str, doc_kind: str = "loss_run",
              use_detail_values: bool = False, account_name: str = "Meridian Logistics") -> Path:
    """Render a loss run PDF in the carrier's own layout."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter, landscape
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle)

    layout = LAYOUTS[carrier]
    styles = getSampleStyleSheet()
    h = ParagraphStyle("h", parent=styles["Heading1"], fontSize=15, spaceAfter=4)
    sub = ParagraphStyle("s", parent=styles["Normal"], fontSize=8.5, textColor=colors.HexColor("#444444"))

    path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(path), pagesize=landscape(letter),
        leftMargin=0.5 * inch, rightMargin=0.5 * inch,
        topMargin=0.5 * inch, bottomMargin=0.5 * inch,
        title=title, author=carrier,
    )

    story = [
        Paragraph(f"{carrier} Insurance Company", h),
        Paragraph(title, ParagraphStyle("t", parent=styles["Heading2"], fontSize=11, spaceAfter=6)),
        Paragraph(f"Insured: {account_name}", sub),
        Paragraph(f"Policy Number: {policy_number}", sub),
        Paragraph(
            f"Policy Period: {_fmt_date(PERIOD_START, layout['date'])} to "
            f"{_fmt_date(PERIOD_END, layout['date'])}", sub),
        Paragraph(f"Valued As Of: {_fmt_date(valuation, layout['date'])}", sub),
        Spacer(1, 12),
    ]

    rows = [layout["headers"]]
    for c in claims:
        rows.append([_value(c, f, layout, use_detail_values) for f in layout["fields"]])

    # totals row
    tot_paid = sum(float(c.detail_overrides.get("paid", c.paid)) for c in claims)
    tot_inc = sum(float(c.detail_overrides.get("total_incurred", c.total_incurred)) for c in claims)
    total_row = ["TOTAL", "", "", ""] + [""] * (len(layout["headers"]) - 4)
    total_row[layout["fields"].index("paid")] = _fmt_money(tot_paid, layout["money"])
    total_row[layout["fields"].index("total_incurred")] = _fmt_money(tot_inc, layout["money"])
    rows.append(total_row)

    tbl = Table(rows, repeatRows=1, hAlign="LEFT")
    tbl.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8E8E4")),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#BBBBBB")),
        ("ALIGN", (4, 1), (-1, -1), "RIGHT"),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#F2F2EE")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(tbl)
    doc.build(story)
    return path


def artifact_name(carrier: str, kind: str = "loss_run", valuation: date = VALUATION_CURRENT) -> str:
    pfx = config.CARRIERS[carrier]["file_prefix"]
    if kind == "large_loss_detail":
        return f"{pfx}_LargeLoss_Detail.pdf"
    if kind == "prior":
        return f"{pfx}_LossRun_{VALUATION_PRIOR.year}_Prior.pdf"
    return f"{pfx}_LossRun_{PERIOD_START.year}-{PERIOD_END.year}.pdf"


def generate_all(out_dir: Path | None = None) -> list[Path]:
    """Generate every carrier document the demo can serve."""
    out_dir = out_dir or config.SEED_PDF_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    made: list[Path] = []

    for carrier in ["Northbridge", "Harborstone", "Redstone", "Summit", "Ironwood", "Bluehaven"]:
        cl = claims_for_carrier(carrier)
        if not cl and carrier != "Bluehaven":
            continue
        pols = sorted({c.policy_number for c in cl})
        pol_label = ", ".join(pols) if pols else "BH-WC-550022"

        made.append(build_pdf(
            out_dir / artifact_name(carrier), carrier, pol_label, cl,
            VALUATION_CURRENT, "Currently Valued Loss Run",
        ))

        if carrier == "Harborstone":
            detail = [c for c in claims_for_carrier(carrier, include_detail_only=True)
                      if c.total_incurred >= config.LARGE_LOSS_THRESHOLD or c.only_in_detail]
            made.append(build_pdf(
                out_dir / artifact_name(carrier, "large_loss_detail"), carrier, pol_label,
                detail, VALUATION_CURRENT, "Large Loss Detail Report",
                doc_kind="large_loss_detail", use_detail_values=True,
            ))

        if carrier == "Summit":
            prior = []
            for c in cl:
                import copy as _copy
                p = _copy.deepcopy(c)
                if c.prior_incurred is not None:
                    p.total_incurred = c.prior_incurred
                    p.paid = min(c.paid, c.prior_incurred)
                    p.reserved = max(0.0, c.prior_incurred - p.paid)
                prior.append(p)
            made.append(build_pdf(
                out_dir / artifact_name(carrier, "prior"), carrier, pol_label, prior,
                VALUATION_PRIOR, "Loss Run - Prior Valuation",
            ))

    return made


if __name__ == "__main__":
    t = book_totals()
    print(f"claims={t['claims']} paid=${t['paid']:,.2f} incurred=${t['incurred']:,.2f} "
          f"policies={t['policies']}")
    paths = generate_all()
    for p in paths:
        print(f"  {p.name}  ({p.stat().st_size // 1024} KB)")
