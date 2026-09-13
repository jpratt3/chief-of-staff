"""
Money extraction for the Invoicing Assistant.

Pure text in, structured money out — no Flask, no PDF parsing — so it can be
graded offline by eval/run_eval.py against cached binder text.

What it pulls off a binder:
  * the headline premium
  * commission, as a percentage and/or a dollar figure
  * every tax, fee and surcharge, as its own line
  * the printed total, when the binder states one, used to reconcile the rest

Design notes
------------
Binders label the same number a dozen ways ("Total Quoted Premium", "Term
Premium $ 70,040.00 Flat", "Total Premium Payment: USD 84,000"), and the same
page is full of numbers that are emphatically not premium — limits, deductibles,
attachment points, retentions. So every candidate carries a score and a reason,
the negative-context filter runs before scoring, and nothing is summed that the
document already summed for us.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Optional

# ── Amount and percent tokens ─────────────────────────────────────────────────
# "USD 210,000"  "$ 70,040.00"  "1,234.56"  "$0"  "(1,200.00)" (credit)
_AMOUNT_RE = re.compile(
    r"(?P<paren>\()?\s*(?:USD|US\$|\$)\s*"
    r"(?P<v1>\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)\s*(?P<close>\))?"
    r"|(?P<v2>\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?)"
)
_PERCENT_RE = re.compile(r"(\d{1,2}(?:\.\d{1,3})?)\s*%")

# A number this large on a binder is a limit, not a premium.
_MAX_REASONABLE = 50_000_000


@dataclass
class MoneyLine:
    """One labelled amount lifted off the page."""
    label:   str = ""
    kind:    str = ""          # premium | premium_component | terrorism | commission
                               # | tax | fee | surcharge | subtotal | total | net
    amount:  Optional[float] = None
    percent: Optional[float] = None
    page:    int = -1
    score:   int = 0
    raw:     str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ── Negative context — lines that carry money but never carry premium ─────────
_NEGATIVE = re.compile(
    r"\b(limit|limits|aggregate|each\s+(?:occurrence|claim|loss|accident)|"
    r"per\s+(?:occurrence|claim|loss)|deductible|retention|sir|self[- ]insured|"
    r"sublimit|sub-limit|attachment|excess\s+of|xs|underlying|combined\s+single|"
    r"capital\s+and\s+surplus|payroll|revenue|receipts|total\s+insured\s+value|tiv|"
    r"replacement\s+cost|coinsurance|phone|fax|suite|zip)\b",
    re.I,
)

# Provisions about premium, not amounts owed.
_PROVISION = re.compile(
    r"minimum\s+earned|earned\s+premium\s+endorsement|premium\s+audit|audit\s+basis|"
    r"return\s+premium|additional\s+premium\s+may|subject\s+to\s+audit|"
    r"if\s+the\s+indicated\s+premium|payment\s+of\s+premium|premium\s+payment\s+terms|"
    r"premium\s+finance|gross\s+written\s+premium|will\s+charge|installment",
    re.I,
)

# ── Label vocabulary, most specific first ─────────────────────────────────────
# (pattern, kind, score). First match wins, so the order is the whole design.
_LABELS: list[tuple[re.Pattern, str, int]] = [
    # Net of commission — what the broker remits to the carrier, not what the
    # client is billed. Checked before both commission and total because the
    # label contains the words for each.
    (re.compile(r"net\s+of\s+commission|net\s+premium\s+due|net\s+due|"
                r"premium\s+less\s+commission", re.I), "net", 9),

    # Commission — the label usually says "of premium".
    (re.compile(r"commission|brokerage\s*(?:fee|%|rate)", re.I), "commission", 10),

    # Terrorism / TRIA premium — its own line, may or may not sit inside premium.
    (re.compile(r"\b(?:tria|tripra)\b.*\b(?:premium|charge|cost)|"
                r"terrorism.*\b(?:premium|charge|coverage\s+cost)|"
                r"premium\s+for\s+certified\s+acts", re.I), "terrorism", 8),

    # Document-stated totals — reconciliation anchors, never summed.
    (re.compile(r"total\s+(?:amount\s+)?(?:due|payable|payment\s+due)|amount\s+due|"
                r"net\s+premium\s+due|total\s+estimated\s+cost|"
                r"total\s+premium\s+and\s+surcharges|estimated\s+total|grand\s+total|"
                r"^total$|^total\s*[:.]?$", re.I), "total", 10),

    # Subtotals the document already computed — used to check our own sums.
    (re.compile(r"total\s+(?:surplus\s+lines\s+)?taxes|total\s+fees?|total\s+surcharges?|"
                r"total\s+taxes?\s*[,/&]", re.I), "subtotal", 9),

    # Taxes. "Tax liability" is deliberately absent — on a binder that is a
    # coverage sublimit, not a charge.
    (re.compile(r"surplus\s+lines?\s+tax|premium\s+tax|state\s+tax|municipal\s+tax|"
                r"fire\s+marshal|assessment|taxes?\b", re.I), "tax", 7),

    # Surcharges.
    (re.compile(r"surcharge", re.I), "surcharge", 7),

    # Fees — a stamping office fee is a fee, not a tax, and is billed as one.
    (re.compile(r"stamping|(?:policy|inspection|processing|broker|service|filing|"
                r"administrative|administration|admin|market\s+policy|carrier|agency)\s+fee|"
                r"fees?\b", re.I), "fee", 6),

    # Premium — strongest label first.
    (re.compile(r"total\s+(?:policy\s+|quoted\s+|estimated\s+(?:written\s+)?|annual\s+|"
                r"written\s+|net\s+)?premium(?:\s+payment)?", re.I), "premium", 10),
    (re.compile(r"(?:policy|term|annual|flat|net|gross)\s+premium", re.I), "premium", 8),
    (re.compile(r"premium\s+net\s+of\s+commission", re.I), "premium", 7),
    (re.compile(r"(?:layer|deposit|estimated|minimum)\s+premium", re.I), "premium", 5),
    # A coverage-specific premium on a blended binder — a component, not the total.
    (re.compile(r"[A-Za-z&/.'-]{3,}(?:\s+[A-Za-z&/.'-]+){0,4}\s+premium", re.I),
     "premium_component", 4),
    (re.compile(r"^premium\b|premium\s*[:\-]", re.I), "premium", 6),
]

SUM_KINDS = {"tax", "fee", "surcharge"}


# Some carriers letter-space their totals ("$ 4 2 , 3 3 6 . 0 0"), which reads
# back as the number 4 unless the gaps are closed first.
_SPACED_DIGITS = re.compile(r"\d(?:\s[\d.,]){3,}")


def unspace_digits(line: str) -> str:
    if not _SPACED_DIGITS.search(line):
        return line
    return re.sub(r"(?<=[\d.,])\s(?=[\d.,])", "", line)


# Column headings that sit above their numbers — "Date Fee", "Description
# Amount". Reading the value below one of these swallows a whole table, so a
# label carrying any of these words is barred from the look-below fallback.
_HEADER_ONLY = {
    "date", "description", "item", "type", "basis", "taxable", "code",
    "number", "no", "state", "rate", "line", "coverage", "transaction",
    "invoiced", "comm",
}


def _looks_like_header(label: str) -> bool:
    words = [w for w in re.split(r"[^A-Za-z]+", label.lower()) if w]
    return any(w in _HEADER_ONLY for w in words)


def _to_float(text: str) -> Optional[float]:
    try:
        return float(text.replace(",", ""))
    except (TypeError, ValueError):
        return None


def _amount_spans(text: str) -> list[tuple[int, int, float]]:
    """(start, end, value) for every dollar figure on a line."""
    out: list[tuple[int, int, float]] = []
    for m in _AMOUNT_RE.finditer(text):
        raw = m.group("v1") or m.group("v2")
        value = _to_float(raw)
        if value is None:
            continue
        # A bare number with no currency mark needs a thousands separator to
        # count — otherwise every policy number and year becomes an amount.
        if m.group("v2") and "," not in raw:
            continue
        if m.group("paren") and m.group("close"):
            value = -value
        out.append((m.start(), m.end(), value))
    return out


def amounts_in(text: str) -> list[float]:
    """Every dollar figure on a line, in order. Parenthesised values are credits."""
    return [value for _s, _e, value in _amount_spans(text)]


def label_value_pairs(line: str) -> list[tuple[str, float]]:
    """Split a line into (label, amount) pairs.

    Layout-mode text merges table columns into one physical line, so
    "Total Premium  $50,000.00  Inspection Fee $750.00" is two charges, not one.
    Each amount is paired with the text between it and the previous amount; a
    pair with no label of its own is a repeated column and is dropped.
    """
    runs: list[tuple[str, list[float]]] = []
    cursor = 0
    for start, end, value in _amount_spans(line):
        label = re.sub(r"\s+", " ", line[cursor:start]).strip(" .:*•·-–—\t")
        cursor = end
        # A rate or a stray separator between two columns is not a new label —
        # "…$30,400.00  3.000%  $912.00" is one tax row, not two.
        if label and re.search(r"[A-Za-z]{2}", label) and not re.fullmatch(
                r"[\d.,%\s]*(?:%|x|xs)?[\d.,%\s]*", label, re.I):
            runs.append((label, [value]))
        elif runs:
            runs[-1][1].append(value)
        elif label:
            runs.append((label, [value]))

    # One label trailed by a run of numbers is a table row — "Nebraska Surplus
    # Lines Tax $30,250.00 $150.00 $30,400.00 3.000% $912.00" is taxable premium,
    # taxable fee, basis, rate and finally the tax itself. Three or more columns
    # and the rightmost is the row's answer; one or two and it is the first.
    return [(label, values[-1] if len(values) >= 3 else values[0])
            for label, values in runs]


def percent_in(text: str) -> Optional[float]:
    m = _PERCENT_RE.search(text)
    return _to_float(m.group(1)) if m else None


def classify(label: str) -> tuple[str, int]:
    """Map a label to (kind, score). ("", 0) when nothing matches."""
    for pattern, kind, score in _LABELS:
        if pattern.search(label):
            return kind, score
    return "", 0


def _label_part(line: str) -> str:
    """The text left of the first amount — the label, as printed."""
    m = _AMOUNT_RE.search(line)
    head = line[: m.start()] if m else line
    return re.sub(r"\s+", " ", head).strip(" .:*•·-–—\t")


def _bare_amount(line: str) -> Optional[float]:
    """An amount on a line that is nothing but an amount (table cell below a label)."""
    stripped = line.strip()
    if not stripped:
        return None
    values = amounts_in(stripped)
    if not values:
        return None
    residue = re.sub(r"[\d,.$()%\s]|USD|US\$|flat|annual", "", stripped, flags=re.I)
    return values[0] if len(residue) <= 2 else None


def _next_amount(lines: list[str], idx: int, lookahead: int = 2) -> Optional[float]:
    """A label can sit above its number in a table — look just below."""
    for offset in range(1, lookahead + 1):
        if idx + offset >= len(lines):
            break
        nxt = lines[idx + offset]
        if not nxt.strip():
            continue
        return _bare_amount(nxt)
    return None


def scan_page(text: str, page_no: int) -> list[MoneyLine]:
    """Every labelled amount on one page."""
    found: list[MoneyLine] = []
    lines = [unspace_digits(ln) for ln in text.split("\n")]

    for idx, line in enumerate(lines):
        stripped = re.sub(r"\s{2,}", "  ", line).strip()
        if not stripped or len(stripped) > 320:
            continue
        if _PROVISION.search(stripped):
            continue

        pairs = label_value_pairs(stripped)

        # No amount on the line — the label may still own the number below it,
        # and a commission rate needs no amount at all.
        if not pairs:
            label = _label_part(stripped)
            if not label or len(label) > 90:
                continue
            kind, score = classify(label)
            if not kind:
                continue
            if kind != "commission" and _NEGATIVE.search(stripped):
                continue
            percent = percent_in(stripped) if kind == "commission" else None
            amount = None if _looks_like_header(label) else _next_amount(lines, idx)
            if amount is None and percent is None:
                continue
            if amount is not None and abs(amount) > _MAX_REASONABLE:
                continue
            found.append(MoneyLine(label=label, kind=kind, amount=amount,
                                   percent=percent, page=page_no, score=score,
                                   raw=stripped[:160]))
            continue

        for label, amount in pairs:
            if len(label) > 90:
                label = label[-90:]
            kind, score = classify(label)
            if not kind:
                # Kept, unscored: state-specific charges carry names no
                # vocabulary anticipates ("KY Domestic, Foreign & Alien"), and
                # reconciliation can still identify one by the gap it fills.
                if (len(label) <= 60 and 0 < amount < _MAX_REASONABLE
                        and not _NEGATIVE.search(label)):
                    found.append(MoneyLine(label=label, kind="unclassified",
                                           amount=amount, page=page_no, score=0,
                                           raw=stripped[:160]))
                continue
            # Commission is quoted as a rate inside prose, so it is the one kind
            # allowed past the negative-context filter.
            if kind != "commission" and _NEGATIVE.search(label):
                continue
            if abs(amount) > _MAX_REASONABLE:
                continue
            found.append(MoneyLine(
                label=label, kind=kind, amount=amount,
                percent=percent_in(stripped) if kind == "commission" else None,
                page=page_no, score=score, raw=stripped[:160],
            ))

        # A rate with no dollar figure beside it still tells us the commission.
        if not any(ln.kind == "commission" for ln in found[-len(pairs):]):
            label = _label_part(stripped)
            kind, score = classify(label)
            if kind == "commission" and percent_in(stripped) is not None:
                found.append(MoneyLine(label=label, kind=kind, amount=None,
                                       percent=percent_in(stripped), page=page_no,
                                       score=score, raw=stripped[:160]))

    return found


# ── Page ranking ─────────────────────────────────────────────────────────────
# The billing block is rarely the declarations page the loss-run engine wants,
# and the pages after it — forms lists, TRIA notices, policy wordings — are full
# of numbers that look like charges. Rank, then read only the top of the pile.
_BILLING_SIGNALS = [
    (re.compile(r"total\s+(?:policy\s+|quoted\s+)?premium|premium\s+due|amount\s+due", re.I), 4),
    (re.compile(r"commission", re.I), 4),
    (re.compile(r"surplus\s+lines?\s+tax|stamping|surcharge|policy\s+fee|"
                r"inspection\s+fee|taxes?\s+and\s+fees", re.I), 3),
    (re.compile(r"\bpremium\b", re.I), 2),
    (re.compile(r"billing|invoice|payment\s+terms|remit", re.I), 2),
]
# Wordings and notices quote money without ever billing it.
_BILLING_NEGATIVE = re.compile(
    r"terrorism\s+risk\s+insurance\s+act|policyholder\s+disclosure|"
    r"forms?\s+(?:and\s+endorsements|schedule)|table\s+of\s+contents", re.I)


def score_page_for_billing(text: str) -> int:
    score = sum(weight for pattern, weight in _BILLING_SIGNALS if pattern.search(text))
    if _BILLING_NEGATIVE.search(text):
        score -= 3
    return score


def rank_pages(pages: list[dict], keep: int = 5) -> list[dict]:
    """Best billing pages first, earlier pages breaking ties."""
    scored = [(score_page_for_billing(p.get("text", "")), p.get("index", 0), p)
              for p in pages]
    scored = [s for s in scored if s[0] > 0]
    scored.sort(key=lambda s: (-s[0], s[1]))
    return [p for _score, _idx, p in scored[:keep]]


def scan_pages(pages: list[dict]) -> list[MoneyLine]:
    """pages = [{"index": 0-based, "text": ...}, ...] — the shape the loss-run
    engine's page selector returns."""
    out: list[MoneyLine] = []
    for page in pages:
        out.extend(scan_page(page.get("text", ""), page.get("index", -1) + 1))
    return out


# ── Assembly ─────────────────────────────────────────────────────────────────

def _best(lines: list[MoneyLine], kind: str) -> Optional[MoneyLine]:
    """Highest-scoring line of a kind; ties go to the earliest page, then to
    the larger amount (a subtotal beats one of its own components)."""
    pool = [ln for ln in lines if ln.kind == kind and ln.amount is not None]
    if not pool:
        return None
    return sorted(pool, key=lambda ln: (-ln.score, ln.page, -(ln.amount or 0)))[0]


def _dedupe(lines: list[MoneyLine]) -> list[MoneyLine]:
    """One line per (kind, amount).

    Binders routinely print the same tax twice — once in the billing summary and
    again in the surplus-lines detail table. Summing both doubles the invoice, so
    the first sighting wins and the rest are dropped.
    """
    seen: set[tuple[str, float]] = set()
    out: list[MoneyLine] = []
    for ln in sorted(lines, key=lambda l: (l.page, -l.score)):
        if ln.amount is None:
            continue
        key = (ln.kind if ln.kind not in SUM_KINDS else "charge", round(ln.amount, 2))
        if key in seen:
            continue
        seen.add(key)
        out.append(ln)
    return out


def _subset_matching(premium: float, charges: list[MoneyLine],
                     target: float) -> Optional[list[MoneyLine]]:
    """Which charges, added to premium, land exactly on the printed total?

    A loss-sensitive program prints cost components that are already inside the
    written premium. Adding them all overstates the invoice, so when exactly one
    subset reconciles to the number the binder printed, that subset is the truth.
    """
    if len(charges) > 12:
        return None
    hits: list[list[MoneyLine]] = []
    for mask in range(1 << len(charges)):
        subset = [charges[i] for i in range(len(charges)) if mask & (1 << i)]
        if abs(premium + sum(ln.amount or 0 for ln in subset) - target) <= 0.5:
            hits.append(subset)
            if len(hits) > 1:
                return None
    return hits[0] if hits else None


def summarize(lines: list[MoneyLine]) -> dict:
    """Fold the raw money lines into one binder's billing picture."""
    premium_line = _best(lines, "premium")
    components   = _dedupe([ln for ln in lines if ln.kind == "premium_component"])
    # A zero-dollar fee is a disclosure, not a charge — it has no place on an
    # invoice request.
    charges      = _dedupe([ln for ln in lines if ln.kind in SUM_KINDS and ln.amount])
    terror_line  = _best(lines, "terrorism")

    # A "TOTAL DUE" inside a commission statement is the commission, and a
    # binder's grand total is never a fraction of its own premium — drop both
    # before they become the reconciliation anchor.
    commission_amounts = {round(abs(ln.amount), 2) for ln in lines
                          if ln.kind == "commission" and ln.amount is not None}
    floor = (premium_line.amount or 0) * 0.5 if premium_line else 0
    total_pool = [ln for ln in lines if ln.kind == "total" and ln.amount is not None
                  and round(abs(ln.amount), 2) not in commission_amounts
                  and ln.amount >= floor]
    printed = (sorted(total_pool, key=lambda ln: (-ln.score, ln.page, -(ln.amount or 0)))[0]
               if total_pool else None)

    notes: list[str] = []

    premium = premium_line.amount if premium_line else None
    premium_basis = premium_line.label if premium_line else ""

    # No headline premium, but the binder itemised coverage lines — add them up.
    if premium is None and components:
        premium = round(sum(ln.amount or 0 for ln in components), 2)
        premium_basis = f"summed from {len(components)} coverage lines"
        notes.append("Premium summed from coverage-line premiums — no total stated.")

    charges_total = round(sum(ln.amount or 0 for ln in charges), 2)

    # Still no premium, but the binder prints what is owed — back into it.
    if premium is None and printed is not None and printed.amount:
        premium = round(printed.amount - charges_total, 2)
        premium_basis = "derived from the printed total"
        notes.append("Premium is not stated — derived from the printed total less "
                     "taxes and fees. Confirm the printed total is not net of "
                     "commission.")

    # Terrorism premium is usually inside the stated premium. Only add it when
    # doing so is what reconciles to the printed total.
    terror = terror_line.amount if terror_line else None
    terror_included = True
    computed = round((premium or 0) + charges_total, 2)

    printed_total = printed.amount if printed else None
    if printed_total is not None and terror:
        if abs(computed - printed_total) > 0.5 and abs(computed + terror - printed_total) <= 0.5:
            computed = round(computed + terror, 2)
            terror_included = False

    # Commission: a printed dollar figure wins; otherwise apply the rate to premium.
    commission_lines = [ln for ln in lines if ln.kind == "commission"]
    pct_line = next((ln for ln in commission_lines if ln.percent is not None), None)
    amt_line = next((ln for ln in commission_lines if ln.amount is not None), None)
    commission_pct = pct_line.percent if pct_line else None
    # Commission is often printed as a deduction — "(4,043.80)" — so the sign
    # says how it was presented, not which way the money moves.
    commission_amt = abs(amt_line.amount) if amt_line else None
    commission_basis = "stated" if amt_line else ""
    if commission_amt is None and commission_pct is not None and premium is not None:
        commission_amt = round(premium * commission_pct / 100.0, 2)
        commission_basis = "computed from rate"
    elif (commission_amt and commission_pct is not None and premium
          and abs(premium * commission_pct / 100.0 - commission_amt) > 1.0):
        implied = commission_amt / premium * 100.0
        notes.append(f"The binder prints a {commission_pct}% rate but a commission of "
                     f"{commission_amt:,.2f} — {implied:.2f}% of premium. Confirm which holds.")

    # Reconciliation against whatever total the binder printed. A binder that
    # prints only a net-of-commission figure still checks out, against
    # premium - commission rather than against the gross.
    net_line = _best(lines, "net")
    net_due = net_line.amount if net_line else None
    net_expected = (round(premium - commission_amt, 2)
                    if premium is not None and commission_amt is not None else None)

    # Some carriers label the net-of-commission figure "Total Amount Due" — what
    # they expect from the broker, not what the client is billed.
    printed_is_net = (printed_total is not None and net_expected is not None
                      and abs(printed_total - net_expected) <= 1.0)

    dropped: list[MoneyLine] = []
    adopted: list[MoneyLine] = []
    if (printed_total is not None and not printed_is_net and premium is not None
            and abs(computed - printed_total) > 0.5):
        subset = _subset_matching(premium, charges, printed_total) if charges else None
        if subset is not None:
            keep_ids = {id(ln) for ln in subset}
            dropped = [ln for ln in charges if id(ln) not in keep_ids]
            charges = subset
            if dropped:
                notes.append("Excluded from the total as already inside the premium: "
                             + ", ".join(ln.label for ln in dropped) + ".")
        else:
            # Nothing on the books closes the gap — see whether an unlabelled
            # charge on the page does. A state tax the vocabulary has never seen
            # is still identifiable by the hole it fills.
            gap = round(printed_total - computed, 2)
            loose = _dedupe([ln for ln in lines if ln.kind == "unclassified"
                             and ln.amount is not None and 0 < ln.amount < printed_total])
            found_gap = _subset_matching(0.0, loose, gap)
            if found_gap:
                adopted = found_gap
                for ln in adopted:
                    ln.kind = "other"
                charges = charges + adopted
                notes.append("Adopted to reconcile with the printed total: "
                             + ", ".join(ln.label for ln in adopted) + ".")
        charges_total = round(sum(ln.amount or 0 for ln in charges), 2)
        computed = round((premium or 0) + charges_total, 2)

    if printed_total is not None and abs(computed - printed_total) <= 0.5:
        reconciliation = "matches printed total"
    elif printed_is_net:
        reconciliation = "matches printed total (net of commission)"
    elif net_due is not None and net_expected is not None and abs(net_due - net_expected) <= 1.0:
        reconciliation = "matches printed net of commission"
    elif printed_total is not None:
        reconciliation = f"off printed total by {computed - printed_total:,.2f}"
        notes.append(f"Extracted lines total {computed:,.2f}; binder prints "
                     f"{printed_total:,.2f} — check for a missed fee or a net-of-"
                     f"commission figure.")
    elif net_due is not None and net_expected is not None:
        reconciliation = f"off printed net by {net_expected - net_due:,.2f}"
        notes.append(f"Premium less commission is {net_expected:,.2f}; binder prints a "
                     f"net due of {net_due:,.2f}.")
    else:
        reconciliation = "no total printed"

    if premium is None:
        notes.append("No premium found — enter it by hand.")
    if not charges:
        notes.append("No taxes, fees or surcharges found on this binder.")

    confidence = 0
    confidence += 45 if premium is not None else 0
    confidence += 20 if (commission_pct is not None or commission_amt is not None) else 0
    confidence += 25 if reconciliation.startswith("matches") else (
        10 if reconciliation == "no total printed" else 0)
    confidence += 10 if charges else 0

    return {
        "premium":          premium,
        "net_due":          net_due,
        "premium_basis":    premium_basis,
        "premium_page":     premium_line.page if premium_line else -1,
        "commission_pct":   commission_pct,
        "commission_amt":   commission_amt,
        "commission_basis": commission_basis,
        "terrorism":        terror,
        "terrorism_included_in_premium": terror_included,
        "charges":          [ln.to_dict() for ln in charges],
        "excluded":         [ln.to_dict() for ln in dropped],
        "components":       [ln.to_dict() for ln in components],
        "charges_total":    charges_total,
        "total":            computed,
        "printed_total":    printed_total,
        "reconciliation":   reconciliation,
        "confidence":       min(confidence, 100),
        "notes":            notes,
    }


def extract(pages: list[dict], rank: bool = True) -> dict:
    """Pages in, one binder's billing picture out."""
    lines = scan_pages(rank_pages(pages) if rank else pages)
    result = summarize(lines)
    result["all_lines"] = [ln.to_dict() for ln in lines]
    return result

