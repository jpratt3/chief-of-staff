"""Reconcile claims across documents.

Three rules:
  * same claim, different valuation dates, incurred moved  -> normal DEVELOPMENT, annotated, NOT flagged
  * same claim, comparable valuation, conflicting field    -> DISCREPANCY
  * claim on a supplemental report but absent from the
    carrier's official run at the same valuation           -> DISCREPANCY

Every discrepancy carries a proposed resolution. The agent commits to a value
(`Agent's pick`) and marks the loser `Superseded`; the reviewer confirms rather
than adjudicating from scratch.

Authority: official carrier loss run > supplemental detail; newer valuation > older.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from schema import Claim, DevelopmentNote, Discrepancy, LossRunDocument

AUTHORITY = {"loss_run": 2, "large_loss_detail": 1}


@dataclass
class Reconciliation:
    canonical: list[Claim] = field(default_factory=list)
    discrepancies: list[Discrepancy] = field(default_factory=list)
    development: list[DevelopmentNote] = field(default_factory=list)

    @property
    def totals(self) -> dict:
        return {
            "claims": len(self.canonical),
            "paid": round(sum(c.paid for c in self.canonical), 2),
            "incurred": round(sum(c.total_incurred for c in self.canonical), 2),
            "open": sum(1 for c in self.canonical if c.status == "open"),
        }


def _money(v: float) -> str:
    return f"${v:,.0f}"


def reconcile(docs: list[LossRunDocument]) -> Reconciliation:
    rec = Reconciliation()
    if not docs:
        return rec

    latest_valuation = max(d.valuation_date for d in docs)

    # index: (carrier, claim_number) -> [(doc, claim)]
    index: dict[tuple[str, str], list[tuple[LossRunDocument, Claim]]] = defaultdict(list)
    for d in docs:
        for c in d.claims:
            index[(d.carrier, c.claim_number)].append((d, c))

    seq = 0
    for (carrier, claim_no), entries in sorted(index.items()):
        current = [(d, c) for d, c in entries if d.valuation_date == latest_valuation]
        older = [(d, c) for d, c in entries if d.valuation_date < latest_valuation]

        # --- development across valuations: annotate, never flag --------------
        if current and older:
            newest = max(current, key=lambda e: AUTHORITY.get(e[0].doc_kind, 0))
            oldest = min(older, key=lambda e: e[0].valuation_date)
            if abs(newest[1].total_incurred - oldest[1].total_incurred) > 0.5:
                rec.development.append(DevelopmentNote(
                    claim_number=claim_no, carrier=carrier,
                    from_value=oldest[1].total_incurred,
                    to_value=newest[1].total_incurred,
                    from_valuation=oldest[0].valuation_date,
                    to_valuation=newest[0].valuation_date,
                ))

        if not current:
            # only present at an older valuation -- superseded, not a conflict
            continue

        official = [(d, c) for d, c in current if d.doc_kind == "loss_run"]
        supplemental = [(d, c) for d, c in current if d.doc_kind != "loss_run"]

        # --- claim on a supplemental report but not the official run ---------
        if supplemental and not official:
            seq += 1
            sd, sc = supplemental[0]
            run = next((d for d in docs
                        if d.carrier == carrier and d.doc_kind == "loss_run"
                        and d.valuation_date == latest_valuation), None)
            rec.discrepancies.append(Discrepancy(
                id=f"D{seq}", kind="missing_claim",
                headline="Claim missing from carrier loss run",
                claim_number=claim_no, carrier=carrier,
                date_of_loss=sc.date_of_loss, field="presence",
                pick_label="Carrier loss run", pick_value="Not listed",
                pick_ref=(run.claims[0].source_ref.model_copy(update={"line": None})
                          if run and run.claims else sc.source_ref),
                other_label="Large loss detail", other_value=_money(sc.total_incurred),
                other_ref=sc.source_ref,
                rationale=("The official carrier run is authoritative and does not list this "
                           "claim. Most likely a stale supplemental report - excluded from the "
                           "summary pending carrier confirmation."),
            ))
            continue

        if not official:
            continue

        primary_doc, primary = max(official, key=lambda e: AUTHORITY.get(e[0].doc_kind, 0))
        rec.canonical.append(primary)

        # --- conflicts between same-valuation documents ----------------------
        for od, oc in supplemental:
            if abs(oc.total_incurred - primary.total_incurred) > 0.5:
                seq += 1
                rec.discrepancies.append(Discrepancy(
                    id=f"D{seq}", kind="amount_conflict",
                    headline="Amounts disagree across carriers",
                    claim_number=claim_no, carrier=carrier,
                    date_of_loss=primary.date_of_loss, field="total_incurred",
                    pick_label="Carrier loss run",
                    pick_value=_money(primary.total_incurred),
                    pick_ref=primary.source_ref,
                    other_label="Large loss detail",
                    other_value=_money(oc.total_incurred), other_ref=oc.source_ref,
                    rationale=("The official carrier loss run supersedes the supplemental "
                               "large loss detail at the same valuation date."),
                ))
            if oc.date_of_loss != primary.date_of_loss:
                seq += 1
                rec.discrepancies.append(Discrepancy(
                    id=f"D{seq}", kind="date_conflict",
                    headline="Date of loss disagrees across documents",
                    claim_number=claim_no, carrier=carrier,
                    date_of_loss=primary.date_of_loss, field="date_of_loss",
                    pick_label="Carrier loss run",
                    pick_value=primary.date_of_loss.strftime("%m/%d/%Y"),
                    pick_ref=primary.source_ref,
                    other_label="Large loss detail",
                    other_value=oc.date_of_loss.strftime("%m/%d/%Y"),
                    other_ref=oc.source_ref,
                    rationale=("The official carrier loss run supersedes the supplemental "
                               "large loss detail at the same valuation date."),
                ))

    rec.canonical.sort(key=lambda c: (c.date_of_loss, c.claim_number))
    rec.discrepancies.sort(key=lambda d: (d.kind != "amount_conflict", d.claim_number))
    for i, d in enumerate(rec.discrepancies, 1):
        d.id = f"D{i}"
    return rec
