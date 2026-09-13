"""Extract heterogeneous carrier loss run PDFs into the canonical schema.

Two extractors, one interface:

  ClaudeExtractor - the real one. Hands the PDF to the model and takes strict
                    JSON back, with one self-repair round-trip on validation
                    failure. No per-carrier parsing code.
  LayoutExtractor - header-alias parser used when no API key is present, so the
                    pipeline is testable offline. It genuinely parses the PDF
                    text; it does not read the seed data.
"""
from __future__ import annotations

import json
import os
import re
from datetime import date, datetime
from pathlib import Path

from pydantic import ValidationError

import config
from schema import Claim, LossRunDocument, SourceRef

# ---- header aliases: longest match first ---------------------------------
ALIASES: list[tuple[str, list[str]]] = [
    ("claim_number", ["claim number", "claim ref", "claim no", "claim"]),
    ("date_of_loss", ["occurrence date", "date of loss", "loss date", "dol"]),
    ("line", ["coverage", "peril", "type", "line", "cov"]),
    ("status", ["open/closed", "status"]),
    ("paid", ["indemnity paid", "paid loss", "payments", "paid"]),
    ("expense", ["paid exp", "expense"]),
    ("reserved", ["outstanding", "reserves", "reserved", "reserve"]),
    ("total_incurred", ["total incurred", "total inc.", "incurred", "total"]),
]

LINE_MAP = {"WC": "WC", "GL": "GL", "AU": "AUTO", "AUTO": "AUTO", "PR": "PROP", "PROP": "PROP"}


def _header_field(cell: str) -> str | None:
    c = cell.strip().lower().rstrip(":")
    for field, names in ALIASES:
        for n in names:
            if c == n:
                return field
    return None


def _parse_date(s: str) -> date | None:
    s = s.strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d-%b-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _parse_money(s: str) -> float:
    s = s.replace("$", "").replace(",", "").strip()
    if s in ("", "-"):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


CLAIM_RE = re.compile(r"^[A-Z]{2}-\d{6,7}$")


class LayoutExtractor:
    """Generic header-alias table parser. Handles all six carrier layouts."""

    def extract(self, path: Path, source: str = "portal") -> LossRunDocument:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        pages = [(i + 1, (p.extract_text() or "")) for i, p in enumerate(reader.pages)]
        all_text = "\n".join(t for _, t in pages)

        carrier = self._meta(all_text, r"^(.+?) Insurance Company")
        policy = self._meta(all_text, r"Policy Number:\s*(.+)")
        period = self._meta(all_text, r"Policy Period:\s*(.+)")
        valued = self._meta(all_text, r"Valued As Of:\s*(.+)")
        kind = ("large_loss_detail" if "Large Loss Detail" in all_text else "loss_run")

        p_start = p_end = None
        if period:
            parts = re.split(r"\s+to\s+", period)
            if len(parts) == 2:
                p_start, p_end = _parse_date(parts[0]), _parse_date(parts[1])

        claims: list[Claim] = []
        for pageno, text in pages:
            claims.extend(self._rows(text, pageno, path.name))

        return LossRunDocument(
            carrier=(carrier or "Unknown").strip(),
            policy_number=(policy or "").split(",")[0].strip(),
            policy_period_start=p_start or date(2021, 1, 1),
            policy_period_end=p_end or date(2025, 12, 31),
            valuation_date=_parse_date(valued or "") or date(2026, 1, 12),
            source=source,
            artifact=path.name,
            doc_kind=kind,
            claims=claims,
        )

    @staticmethod
    def _meta(text: str, pattern: str) -> str | None:
        m = re.search(pattern, text, re.MULTILINE)
        return m.group(1).strip() if m else None

    def _rows(self, text: str, pageno: int, artifact: str) -> list[Claim]:
        lines = [l.strip() for l in text.split("\n") if l.strip()]

        # locate the header run
        start = None
        for i, l in enumerate(lines):
            if _header_field(l) == "claim_number":
                start = i
                break
        if start is None:
            return []

        headers: list[str] = []
        i = start
        while i < len(lines):
            f = _header_field(lines[i])
            if f is None:
                break
            headers.append(f)
            i += 1
        if len(headers) < 4:
            return []

        n = len(headers)
        body = lines[i:]
        out: list[Claim] = []
        for j in range(0, len(body) - n + 1, n):
            chunk = body[j:j + n]
            if not chunk or chunk[0].upper().startswith("TOTAL"):
                break
            if not CLAIM_RE.match(chunk[0]):
                break
            rec = dict(zip(headers, chunk))
            dol = _parse_date(rec.get("date_of_loss", ""))
            if dol is None:
                continue
            paid = _parse_money(rec.get("paid", "0"))
            expense = _parse_money(rec.get("expense", "0"))
            reserved = _parse_money(rec.get("reserved", "0"))
            incurred = _parse_money(rec.get("total_incurred", "0")) or (paid + expense + reserved)
            try:
                out.append(Claim(
                    claim_number=rec["claim_number"],
                    date_of_loss=dol,
                    line=LINE_MAP.get(rec.get("line", "").upper(), "GL"),
                    status="open" if rec.get("status", "").upper().startswith("O") else "closed",
                    paid=paid, expense=expense, reserved=reserved, total_incurred=incurred,
                    source_ref=SourceRef(artifact=artifact, page=pageno, line=j // n + 1),
                ))
            except ValidationError:
                continue
        return out


EXTRACT_SCHEMA_HINT = """Return ONLY JSON of this shape:
{"carrier":str,"policy_number":str,"policy_period_start":"YYYY-MM-DD",
 "policy_period_end":"YYYY-MM-DD","valuation_date":"YYYY-MM-DD",
 "doc_kind":"loss_run"|"large_loss_detail",
 "claims":[{"claim_number":str,"date_of_loss":"YYYY-MM-DD",
            "line":"WC"|"GL"|"AUTO"|"PROP","status":"open"|"closed",
            "paid":num,"expense":num,"reserved":num,"total_incurred":num,
            "page":int}]}
Every claim row in the document must appear. Do not invent claims."""


class ClaudeExtractor:
    MODEL = "claude-opus-5"

    def __init__(self):
        import anthropic
        self.client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    def extract(self, path: Path, source: str = "portal") -> LossRunDocument:
        import base64
        pdf_b64 = base64.standard_b64encode(path.read_bytes()).decode()

        def ask(extra: str = "") -> str:
            msg = self.client.messages.create(
                model=self.MODEL, max_tokens=8000,
                messages=[{"role": "user", "content": [
                    {"type": "document",
                     "source": {"type": "base64", "media_type": "application/pdf",
                                "data": pdf_b64}},
                    {"type": "text",
                     "text": ("Extract every claim from this carrier loss run.\n"
                              + EXTRACT_SCHEMA_HINT + extra)},
                ]}],
            )
            return "".join(b.text for b in msg.content if b.type == "text")

        raw = ask()
        for attempt in range(2):
            try:
                data = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
                claims = [
                    Claim(**{k: v for k, v in c.items() if k != "page"},
                          source_ref=SourceRef(artifact=path.name, page=c.get("page", 1)))
                    for c in data["claims"]
                ]
                return LossRunDocument(
                    carrier=data["carrier"], policy_number=data["policy_number"],
                    policy_period_start=data["policy_period_start"],
                    policy_period_end=data["policy_period_end"],
                    valuation_date=data["valuation_date"],
                    source=source, artifact=path.name,
                    doc_kind=data.get("doc_kind", "loss_run"), claims=claims,
                )
            except (ValidationError, ValueError, KeyError, AttributeError) as e:
                if attempt == 1:
                    raise
                raw = ask(f"\n\nYour previous output failed validation: {e}. "
                          f"Return corrected JSON only.")
        raise RuntimeError("unreachable")


def get_extractor():
    return ClaudeExtractor() if config.use_claude() else LayoutExtractor()


def extract_all(paths: list[tuple[Path, str]]) -> list[LossRunDocument]:
    ex = get_extractor()
    docs = []
    for p, source in paths:
        docs.append(ex.extract(p, source))
    return docs


if __name__ == "__main__":
    ex = LayoutExtractor()
    for p in sorted(config.SEED_PDF_DIR.glob("*.pdf")):
        d = ex.extract(p)
        print(f"{p.name:<34} {d.carrier:<12} claims={len(d.claims):<3} "
              f"paid=${d.total_paid:>12,.2f} inc=${d.total_incurred:>12,.2f}  [{d.doc_kind}]")
