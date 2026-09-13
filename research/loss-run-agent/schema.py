"""Canonical claims schema. Every extracted field carries a source citation."""
from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

Line = Literal["WC", "GL", "AUTO", "PROP"]
ClaimStatus = Literal["open", "closed"]


class SourceRef(BaseModel):
    """Where a value came from. Load-bearing: the review UI links to this page."""

    artifact: str  # filename, e.g. HBS_LossRun_2021-2025.pdf
    page: int
    line: Optional[int] = None

    def cite(self) -> str:
        return f"{self.artifact} · p.{self.page}"


class Claim(BaseModel):
    claim_number: str
    date_of_loss: date
    line: Line
    status: ClaimStatus
    description: str = ""
    paid: float = 0.0
    expense: float = 0.0
    reserved: float = 0.0
    total_incurred: float = 0.0
    litigation_flag: bool = False
    source_ref: SourceRef

    @field_validator("paid", "expense", "reserved", "total_incurred", mode="before")
    @classmethod
    def _money(cls, v):
        if isinstance(v, str):
            v = v.replace("$", "").replace(",", "").strip()
            if v in ("", "-"):
                return 0.0
        return float(v)


class LossRunDocument(BaseModel):
    carrier: str
    policy_number: str
    policy_period_start: date
    policy_period_end: date
    valuation_date: date
    source: Literal["portal", "email"]
    artifact: str
    doc_kind: Literal["loss_run", "large_loss_detail"] = "loss_run"
    claims: list[Claim] = Field(default_factory=list)

    @property
    def total_paid(self) -> float:
        return sum(c.paid for c in self.claims)

    @property
    def total_incurred(self) -> float:
        return sum(c.total_incurred for c in self.claims)


class Discrepancy(BaseModel):
    """A conflict between documents, WITH a proposed resolution.

    The agent commits to a value (`agent_pick`); the human confirms rather than
    adjudicating from scratch.
    """

    id: str
    kind: Literal["amount_conflict", "date_conflict", "missing_claim"]
    headline: str
    claim_number: str
    carrier: str
    date_of_loss: Optional[date] = None
    field: str = "total_incurred"

    pick_label: str  # "Carrier loss run"
    pick_value: str  # "$164,000"
    pick_ref: SourceRef

    other_label: str  # "Large loss detail"
    other_value: str
    other_ref: SourceRef

    rationale: str
    resolved: bool = False
    approved_value: Optional[str] = None


class DevelopmentNote(BaseModel):
    """Same claim, different valuations, incurred moved. Normal. NOT a discrepancy."""

    claim_number: str
    carrier: str
    from_value: float
    to_value: float
    from_valuation: date
    to_valuation: date

    @property
    def note(self) -> str:
        return (
            f"{self.claim_number}: incurred developed "
            f"${self.from_value:,.0f} -> ${self.to_value:,.0f} between "
            f"{self.from_valuation:%m/%Y} and {self.to_valuation:%m/%Y} (reserve development)"
        )
