"""
renewal_calendar.py — Chunk 3
Parses Renewal_Timeline.xlsx into structured renewal schedules per client engagement.

Source of truth: docs/Renewal_Timeline.xlsx
  - "Renewal Timeline" sheet: per-client stage dates
  - Stage names match clients.json stage_dates keys (7 canonical stages)

Key structures:
  - RenewalSchedule: one per engagement row in the spreadsheet
  - load_renewal_schedule(xlsx_path): returns list[RenewalSchedule]
  - get_schedule_for_client(client_record, schedules): matches by display_name + label
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import openpyxl

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Canonical stage order (7 stages — Deliver Policies is a subset of Post Binding)
# ---------------------------------------------------------------------------
CANONICAL_STAGES = [
    "Renewal Preparation",
    "RSM",
    "Submission",
    "Proposal",
    "Bind",
    "Invoice",
    "Post Binding",
]

# Column index mapping (0-based) from the Renewal Timeline sheet
# ISM = Internal Strategy Meeting (a sub-milestone inside Renewal Preparation).
# Row 3 header: Client | Line | Renewal Date | Renewal Preparation | ISM | RSM | Addl RSM | Submission | Proposal | Bind | Invoice | Post Binding
_COL_CLIENT          = 0
_COL_LINE            = 1
_COL_RENEWAL_DATE    = 2
_COL_RENEWAL_PREP    = 3
_COL_ISM            = 4   # sub-milestone within Renewal Preparation
_COL_RSM             = 5
_COL_ADDL_RSM        = 6   # client note on RSM (two accounts only)
_COL_SUBMISSION      = 7
_COL_PROPOSAL        = 8
_COL_BIND            = 9
_COL_INVOICE         = 10
_COL_POST_BINDING    = 11

_HEADER_ROW = 3  # 1-based row index of the header in the sheet
_DATA_START  = 4  # 1-based row index of first data row


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class RenewalSchedule:
    """Structured renewal schedule for one client engagement."""

    # Identity
    client_name: str          # As it appears in the spreadsheet (e.g. "Northwind Foods")
    line: str                 # Engagement line label (e.g. "P&C", "—" for single-line clients)
    renewal_date: datetime

    # 7 canonical stage dates (may be None if not present in spreadsheet)
    stage_dates: dict[str, Optional[datetime]] = field(default_factory=dict)

    # Sub-milestones (not stages — tracked within their parent stage)
    ism_date: Optional[datetime] = None       # Within Renewal Preparation
    addl_rsm_date: Optional[datetime] = None   # Client note on RSM (two accounts)

    @property
    def label(self) -> str:
        """Normalised label: empty string for single-line clients (where line == '—')."""
        return "" if self.line == "—" else self.line

    @property
    def renewal_date_str(self) -> str:
        return self.renewal_date.strftime("%m/%d/%Y")

    def stage_date_str(self, stage: str) -> Optional[str]:
        dt = self.stage_dates.get(stage)
        return dt.strftime("%m/%d/%Y") if dt else None


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def load_renewal_schedule(xlsx_path: Path) -> list[RenewalSchedule]:
    """
    Parse the 'Renewal Timeline' sheet from Renewal_Timeline.xlsx.

    Returns a list of RenewalSchedule — one per engagement row.
    Rows with missing client name or renewal date are skipped with a warning.
    """
    xlsx_path = Path(xlsx_path)
    if not xlsx_path.exists():
        raise FileNotFoundError(f"Renewal_Timeline.xlsx not found at: {xlsx_path}")

    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    if "Renewal Timeline" not in wb.sheetnames:
        raise ValueError(f"'Renewal Timeline' sheet not found in {xlsx_path.name}")

    ws = wb["Renewal Timeline"]
    rows = list(ws.iter_rows(values_only=True))
    schedules: list[RenewalSchedule] = []

    for row_idx, row in enumerate(rows, start=1):
        if row_idx < _DATA_START:
            continue  # skip title, blank, and header rows

        client = row[_COL_CLIENT]
        line   = row[_COL_LINE]

        if not client:
            continue  # blank trailing row

        renewal_date = row[_COL_RENEWAL_DATE]
        if not isinstance(renewal_date, datetime):
            logger.warning("Row %d: skipping '%s' — missing or invalid renewal date", row_idx, client)
            continue

        def _dt(col: int) -> Optional[datetime]:
            val = row[col]
            return val if isinstance(val, datetime) else None

        stage_dates: dict[str, Optional[datetime]] = {
            "Renewal Preparation": _dt(_COL_RENEWAL_PREP),
            "RSM":                 _dt(_COL_RSM),
            "Submission":          _dt(_COL_SUBMISSION),
            "Proposal":            _dt(_COL_PROPOSAL),
            "Bind":                _dt(_COL_BIND),
            "Invoice":             _dt(_COL_INVOICE),
            "Post Binding":        _dt(_COL_POST_BINDING),
        }

        schedule = RenewalSchedule(
            client_name=str(client).strip(),
            line=str(line).strip() if line else "—",
            renewal_date=renewal_date,
            stage_dates=stage_dates,
            ism_date=_dt(_COL_ISM),
            addl_rsm_date=_dt(_COL_ADDL_RSM),
        )
        schedules.append(schedule)

    wb.close()
    logger.info("Loaded %d renewal schedules from %s", len(schedules), xlsx_path.name)
    return schedules


# ---------------------------------------------------------------------------
# Lookup helper
# ---------------------------------------------------------------------------

def get_schedule_for_client(
    display_name: str,
    label: str,
    schedules: list[RenewalSchedule],
) -> Optional[RenewalSchedule]:
    """
    Return the RenewalSchedule matching a given display_name + engagement label.

    Matching rules:
    - display_name is compared case-insensitively against schedule.client_name
    - label "" matches schedules where schedule.label == "" (single-line clients)
    - label "P&C" etc. matched case-insensitively against schedule.label

    Returns None if no match found.
    """
    dn_lower = display_name.strip().lower()
    label_lower = label.strip().lower()

    for s in schedules:
        if s.client_name.lower() == dn_lower and s.label.lower() == label_lower:
            return s

    return None


# ---------------------------------------------------------------------------
# Cross-validation helper
# ---------------------------------------------------------------------------

def cross_validate(
    clients_cfg: dict,
    schedules: list[RenewalSchedule],
    tolerance_days: int = 3,
) -> list[dict]:
    """
    Compare stage_dates in clients.json against Renewal_Timeline.xlsx.

    Returns a list of discrepancy dicts:
      {client, label, stage, clients_json_date, xlsx_date, delta_days}

    Only flags divergences greater than tolerance_days.
    """
    discrepancies = []

    for client in clients_cfg.get("clients", []):
        if not client.get("active", True):
            continue

        display_name = client.get("display_name", "")

        for eng in client.get("engagements", []):
            # Skip secondary engagements — they are noted on the primary row but
            # not tracked as independent projects, so cross-validation is irrelevant.
            if eng.get("primary") is False:
                continue
            label = eng.get("label", "")
            schedule = get_schedule_for_client(display_name, label, schedules)

            if schedule is None:
                logger.warning("cross_validate: no xlsx match for '%s' / '%s'", display_name, label)
                continue

            cfg_dates = eng.get("stage_dates", {})
            for stage in CANONICAL_STAGES:
                cfg_val = cfg_dates.get(stage)
                xlsx_dt = schedule.stage_dates.get(stage)

                if not cfg_val or xlsx_dt is None:
                    continue

                try:
                    cfg_dt = datetime.strptime(cfg_val, "%m/%d/%Y")
                except ValueError:
                    continue

                delta = abs((cfg_dt - xlsx_dt).days)
                if delta > tolerance_days:
                    discrepancies.append({
                        "client":           display_name,
                        "label":            label,
                        "stage":            stage,
                        "clients_json_date": cfg_val,
                        "xlsx_date":        xlsx_dt.strftime("%m/%d/%Y"),
                        "delta_days":       delta,
                    })

    return discrepancies


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

def print_schedule_summary(schedules: list[RenewalSchedule]) -> None:
    """Print a summary of loaded schedules for verification."""
    print(f"\n{'='*60}")
    print(f"Renewal Schedule Summary — {len(schedules)} engagements")
    print(f"{'='*60}")

    stage_coverage = {s: 0 for s in CANONICAL_STAGES}
    ism_count = 0
    addl_rsm_count = 0

    for s in schedules:
        label_str = f" ({s.label})" if s.label else ""
        print(f"\n  {s.client_name}{label_str} — Renewal: {s.renewal_date_str}")
        for stage in CANONICAL_STAGES:
            dt = s.stage_dates.get(stage)
            date_str = dt.strftime("%m/%d/%Y") if dt else "—"
            if dt:
                stage_coverage[stage] += 1
            print(f"    {stage:<22} {date_str}")
        if s.ism_date:
            print(f"    {'ISM (sub-milestone)':<22} {s.ism_date.strftime('%m/%d/%Y')}")
            ism_count += 1
        if s.addl_rsm_date:
            print(f"    {'Addl RSM (note)':<22} {s.addl_rsm_date.strftime('%m/%d/%Y')}")
            addl_rsm_count += 1

    print(f"\n  Stage coverage across {len(schedules)} engagements:")
    for stage, count in stage_coverage.items():
        print(f"    {stage:<22} {count}/{len(schedules)}")
    print(f"  ISM sub-milestone present: {ism_count}/{len(schedules)}")
    print(f"  Addl RSM note present:      {addl_rsm_count}/{len(schedules)}")
    print()
