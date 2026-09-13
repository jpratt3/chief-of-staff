"""
status_logic.py — Chunk 7
Derives work item status and owner for each renewal stage.

Single responsibility: given a list of ResolvedActivity objects + a
RenewalSchedule + a client config entry, return a list of WorkItemStatus
objects — one per canonical stage — with status, due date, owner, and
evidence fields fully resolved before the workbook layer ever sees them.

Public API
----------
derive_statuses(activities, schedule, client_record, today=None)
    -> list[WorkItemStatus]

Status progression (per locked design decisions):
    Upcoming   — due date > 14 days away, no activity
    Not Started — due date <= 14 days away, no activity
    In Progress — at least one qualifying signal detected
    Complete    — all required close signals detected
    Overdue     — due date passed without reaching Complete

Stage close signal map (locked in pre-build):
    Renewal Preparation — any of: ISM (Internal Strategy Meeting) scheduled/held,
                          exposure request sent,
                          loss runs requested  → In Progress (any 1 of 3)
                          all 3 detected       → Complete
    RSM                 — calendar event with RSM/Renewal Strategy keyword
    Submission          — outbound email with submission/marketplace keywords
    Proposal            — inbound external email with bind-approval language
    Bind                — outbound email with BTL keywords OR binder/policy
                          attachment filename match; body-text fallback
    Invoice             — inbound from the premium-invoice mailbox (identity.invoice_sender) → In Progress
                          (hard close is manual — no Complete from signal alone)
    Post Binding        — outbound email with PTL/program summary keywords OR
                          matching attachment filename; body-text fallback
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from src.models import ResolvedActivity
from src.renewal_calendar import RenewalSchedule, CANONICAL_STAGES

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

UPCOMING_WINDOW_DAYS = 14  # locked: stages within 14 days flip to Not Started

# Stage → role owner (maps each stage to the role key in the team block)
# Derived from Workflow_Template role flags:
#   Renewal Preparation — AR leads checklist tasks
#   RSM                 — AE conducts, AR drafts
#   Submission          — AAE prepares, AE approves → primary owner AE
#   Proposal            — AE leads client delivery
#   Bind                — AE obtains bind order; AR executes
#   Invoice             — AR
#   Post Binding        — AR
STAGE_OWNER_ROLE: Dict[str, str] = {
    "Renewal Preparation": "ar",
    "RSM":                 "ae",
    "Submission":          "ae",
    "Proposal":            "ae",
    "Bind":                "ae",
    "Invoice":             "ar",
    "Post Binding":        "ar",
}

# ---------------------------------------------------------------------------
# Attachment / body-text patterns for signal detection
# ---------------------------------------------------------------------------

# Filename patterns (case-insensitive substring match)
_ATTACHMENT_PATTERNS_BIND: List[str] = ["binder", "btl"]
_ATTACHMENT_PATTERNS_POST_BINDING: List[str] = ["policy", "ptl"]

# Body / subject keyword patterns compiled once
_RE_ISM            = re.compile(r"\bism\b", re.IGNORECASE)
_RE_EXPOSURE        = re.compile(r"\bexposure\b", re.IGNORECASE)
_RE_LOSS_RUNS       = re.compile(r"\bloss\s*(runs?|data|summary)\b", re.IGNORECASE)
_RE_RSM             = re.compile(r"\b(rsm|renewal\s+strategy\s+meeting)\b", re.IGNORECASE)
_RE_SUBMISSION      = re.compile(r"\b(submission|submitted|marketplace|subm)\b", re.IGNORECASE)
_RE_BIND_APPROVAL   = re.compile(
    r"\b(approved|confirmed|permission\s+to\s+bind|bind\s+order|binding\s+order|authorize\s+to\s+bind)\b",
    re.IGNORECASE,
)
_RE_BTL             = re.compile(r"\b(btl|binder\s+transmittal|transmittal\s+letter)\b", re.IGNORECASE)
_RE_PTL             = re.compile(r"\b(ptl|policy\s+transmittal|program\s+summary|program\s+graphic|summary\s+of\s+insurance)\b", re.IGNORECASE)

def _identity(key: str, default: str) -> str:
    """
    Read an identity value out of config/settings.json.

    Firm-specific identifiers (the premium-invoice mailbox, the firm's legal
    name) live in config, not in source, so the repo carries no internal
    mailboxes. settings.json is gitignored; settings.example.json shows the
    shape. Falls back to the placeholder so a missing config degrades to
    "matches nothing" rather than raising.
    """
    try:
        import json as _json
        from pathlib import Path as _Path
        _p = _Path(__file__).resolve().parent.parent / "config" / "settings.json"
        return (_json.loads(_p.read_text(encoding="utf-8"))
                .get("identity", {})
                .get(key) or default)
    except Exception:
        return default


INVOICE_SENDER = _identity("invoice_sender", "premium.invoice@yourbrokerage.com").lower()

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class WorkItemStatus:
    """Fully resolved status for one stage of one client engagement."""

    client_name:        str
    project_name:       str
    stage_name:         str
    status:             str          # Upcoming | Not Started | In Progress | Complete | Overdue
    due_date:           str          # MM/DD/YYYY or ""
    owner:              str          # display name from team block, or role label fallback
    owner_email:        str          # email from team block, or ""
    completed_flag:     bool
    completed_at:       str          # MM/DD/YYYY or ""
    completion_source:  str          # "Email" | "Calendar" | "Manual" | "Rule" | ""
    evidence_type:      str          # source of most recent evidence
    evidence_reference: str          # subject or event title of evidence
    last_activity_date: str          # MM/DD/YYYY of most recent activity
    manual_override:    bool         # preserved from prior workbook row
    notes:              str          # preserved from prior workbook row
    sort_order:         int          # follows CANONICAL_STAGES index (1-based)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _fmt_date(dt: Optional[datetime]) -> str:
    if dt is None:
        return ""
    return dt.strftime("%m/%d/%Y")


def _parse_mmddyyyy(value: str | None) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.strptime(str(value).strip(), "%m/%d/%Y").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _extract_team_emails(team: Dict[str, Any]) -> List[str]:
    """Flatten all email addresses from the team block into a list."""
    emails: List[str] = []
    for key, entry in team.items():
        if isinstance(entry, list):
            for member in entry:
                email = str(member.get("email") or "").strip().lower()
                if email:
                    emails.append(email)
        elif isinstance(entry, dict):
            email = str(entry.get("email") or "").strip().lower()
            if email:
                emails.append(email)
    return emails


def _resolve_owner(stage_name: str, team: Dict[str, Any], jordan_role: str) -> tuple[str, str]:
    """
    Return (owner_name, owner_email) for a stage.

    Lookup order:
    1. STAGE_OWNER_ROLE maps stage → role key
    2. team block for that role key
    3. Fallback to jordan if role key absent or empty
    """
    role_key = STAGE_OWNER_ROLE.get(stage_name, "ar")
    entry = team.get(role_key)
    if isinstance(entry, dict):
        name  = str(entry.get("name") or "").strip()
        email = str(entry.get("email") or "").strip()
        if name:
            return name, email
    # Fallback: return role label as display
    return role_key.upper(), ""


def _search_text(activity: ResolvedActivity) -> str:
    """Combined searchable text from an activity's reference field."""
    return activity.reference.lower()


def _has_attachment_match(activity: ResolvedActivity, patterns: List[str]) -> bool:
    """
    Check attachment_filenames if present on the source message,
    falling back to body-text / reference scan.

    ResolvedActivity does not carry attachment_filenames directly — those
    live on ClassifiedMailMessage.  The reference field (normalized subject)
    and stage_hint are what reach this layer.  Body-text fallback uses
    the reference field only; attachment filename matching requires that
    the inference engine passes a signal forward.  For now we match on
    reference text as the available signal; attachment filename enrichment
    from mail_reader feeds stage_hint via inference_engine keywords.
    """
    ref_lower = activity.reference.lower()
    for pattern in patterns:
        if pattern.lower() in ref_lower:
            return True
    return False


def _latest_activity_for_stage(
    activities: List[ResolvedActivity],
    stage_name: str,
    client_name: str,
    project_name: str,
) -> Optional[ResolvedActivity]:
    """Return the most recent activity whose stage_hint matches stage_name."""
    matching = [
        a for a in activities
        if a.client_name == client_name
        and a.project_name == project_name
        and a.stage_hint == stage_name
    ]
    if not matching:
        return None
    def _ts(a: ResolvedActivity) -> datetime:
        try:
            t = a.timestamp.strip()
            if t.endswith("Z"):
                t = t[:-1] + "+00:00"
            dt = datetime.fromisoformat(t)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return datetime.min.replace(tzinfo=timezone.utc)
    return max(matching, key=_ts)


def _all_activities_for_stage(
    activities: List[ResolvedActivity],
    stage_name: str,
    client_name: str,
    project_name: str,
) -> List[ResolvedActivity]:
    return [
        a for a in activities
        if a.client_name == client_name
        and a.project_name == project_name
        and a.stage_hint == stage_name
    ]

# ---------------------------------------------------------------------------
# Stage-specific close signal evaluators
# ---------------------------------------------------------------------------

def _eval_renewal_preparation(
    acts: List[ResolvedActivity],
    schedule: Optional[RenewalSchedule],
) -> tuple[str, str]:
    """
    Close logic (locked):
      - Any 1 of 3 signals → In Progress
      - All 3 signals      → Complete
    Signals: ISM scheduled/held | exposure request sent | loss runs requested
    ISM can come from calendar (activity_type Complete/Upcoming) or mail keyword.
    """
    ism_seen      = False
    exposure_seen  = False
    loss_runs_seen = False

    for a in acts:
        text = a.reference.lower()
        if _RE_ISM.search(text):
            ism_seen = True
        if _RE_EXPOSURE.search(text):
            exposure_seen = True
        if _RE_LOSS_RUNS.search(text):
            loss_runs_seen = True
        # Calendar events with activity_type Complete/Upcoming that match ISM
        if a.source_type == "calendar" and _RE_ISM.search(text):
            ism_seen = True

    # Also treat a schedule ISM date that is in the past as ism_seen
    if schedule and schedule.ism_date:
        now = datetime.now(timezone.utc)
        if schedule.ism_date.replace(tzinfo=timezone.utc) <= now:
            ism_seen = True

    count = sum([ism_seen, exposure_seen, loss_runs_seen])
    if count == 3:
        return "Complete", "Email"
    if count >= 1:
        return "In Progress", "Email"
    return "", ""


def _eval_rsm(acts: List[ResolvedActivity]) -> tuple[str, str]:
    """
    Close: calendar event with RSM or Renewal Strategy keyword (activity_type Complete).
    """
    for a in acts:
        if a.source_type == "calendar" and a.activity_type == "Complete":
            if _RE_RSM.search(a.reference):
                return "Complete", "Calendar"
        if _RE_RSM.search(a.reference):
            return "In Progress", "Email"
    return "", ""


def _eval_submission(acts: List[ResolvedActivity]) -> tuple[str, str]:
    """
    Close: outbound email (Action Taken / is_sent_by_jordan) with submission keywords.
    In Progress: any submission-keyword activity.
    """
    for a in acts:
        if a.source_type == "mail" and a.activity_type == "Action Taken":
            if _RE_SUBMISSION.search(a.reference):
                return "Complete", "Email"
    for a in acts:
        if _RE_SUBMISSION.search(a.reference):
            return "In Progress", "Email"
    return "", ""


def _eval_proposal(acts: List[ResolvedActivity]) -> tuple[str, str]:
    """
    Close: inbound external email with bind-approval language.
    In Progress: any proposal-stage activity.
    """
    for a in acts:
        if a.source_type == "mail" and a.activity_type != "Action Taken":
            if _RE_BIND_APPROVAL.search(a.reference):
                return "Complete", "Email"
    if acts:
        return "In Progress", "Email"
    return "", ""


def _eval_bind(acts: List[ResolvedActivity]) -> tuple[str, str]:
    """
    Close: outbound email with BTL keywords OR binder/policy attachment filename match.
    Falls back to body-text pattern scan.
    In Progress: any bind-stage activity.
    """
    for a in acts:
        if a.source_type == "mail" and a.activity_type == "Action Taken":
            if _RE_BTL.search(a.reference) or _has_attachment_match(a, _ATTACHMENT_PATTERNS_BIND):
                return "Complete", "Email"
    if acts:
        return "In Progress", "Email"
    return "", ""


def _eval_invoice(acts: List[ResolvedActivity]) -> tuple[str, str]:
    """
    Soft signal only (locked Decision 2):
      inbound from the premium-invoice mailbox (identity.invoice_sender) → In Progress
    Hard close is manual — this layer never sets Invoice to Complete from signals.
    """
    for a in acts:
        if a.source_type == "mail" and INVOICE_SENDER in a.sender.lower():
            return "In Progress", "Email"
    if acts:
        return "In Progress", "Email"
    return "", ""


def _eval_post_binding(acts: List[ResolvedActivity]) -> tuple[str, str]:
    """
    Close: outbound email with PTL/program summary keywords OR matching
    attachment filename. Body-text fallback applies.
    In Progress: any post-binding-stage activity.
    """
    for a in acts:
        if a.source_type == "mail" and a.activity_type == "Action Taken":
            if _RE_PTL.search(a.reference) or _has_attachment_match(a, _ATTACHMENT_PATTERNS_POST_BINDING):
                return "Complete", "Email"
    if acts:
        return "In Progress", "Email"
    return "", ""


# Dispatch table: stage_name → evaluator function
_STAGE_EVALUATORS = {
    "Renewal Preparation": lambda acts, sched: _eval_renewal_preparation(acts, sched),
    "RSM":                 lambda acts, sched: _eval_rsm(acts),
    "Submission":          lambda acts, sched: _eval_submission(acts),
    "Proposal":            lambda acts, sched: _eval_proposal(acts),
    "Bind":                lambda acts, sched: _eval_bind(acts),
    "Invoice":             lambda acts, sched: _eval_invoice(acts),
    "Post Binding":        lambda acts, sched: _eval_post_binding(acts),
}

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def derive_statuses(
    activities:     List[ResolvedActivity],
    schedule:       Optional[RenewalSchedule],
    client_record:  Dict[str, Any],
    today:          Optional[datetime] = None,
    manual_notes_map:    Optional[Dict[tuple, str]]  = None,
    manual_override_map: Optional[Dict[tuple, bool]] = None,
    project_name:   Optional[str] = None,
) -> List[WorkItemStatus]:
    """
    Derive one WorkItemStatus per canonical stage for a single client engagement.

    Parameters
    ----------
    activities          : All ResolvedActivity objects for this run (may include
                          activities from other clients — filtered internally).
    schedule            : RenewalSchedule for this engagement, or None if no xlsx match.
    client_record       : Single client dict from clients.json (has 'display_name',
                          'jordan_role', 'team', 'engagements').
    today               : Override for current date (defaults to UTC now).
    manual_notes_map    : {(client, project, stage): notes_string} — preserved from
                          prior workbook run; pass {} if not available.
    manual_override_map : {(client, project, stage): bool} — preserved from prior
                          workbook run; pass {} if not available.

    Returns
    -------
    list[WorkItemStatus] — one entry per canonical stage, in stage order.
    """
    if today is None:
        today = datetime.now(timezone.utc)

    notes_map    = manual_notes_map    or {}
    override_map = manual_override_map or {}

    client_name  = client_record.get("display_name", "")
    jordan_role  = (client_record.get("jordan_role") or "AR").lower()
    team         = client_record.get("team") or {}

    # Determine project_name: caller-supplied takes precedence,
    # then schedule renewal date, then first engagement in client_record
    if not project_name:
        if schedule:
            project_name = schedule.renewal_date.strftime("%m/%d/%Y") + " Renewal"
        else:
            engagements = client_record.get("engagements") or []
            if engagements:
                project_name = engagements[0].get("project_name", "")
    project_name = project_name or ""

    results: List[WorkItemStatus] = []

    for sort_idx, stage_name in enumerate(CANONICAL_STAGES, start=1):
        # --- Due date from schedule ---
        due_dt: Optional[datetime] = None
        if schedule:
            due_dt = schedule.stage_dates.get(stage_name)
            if due_dt and due_dt.tzinfo is None:
                due_dt = due_dt.replace(tzinfo=timezone.utc)

        due_date_str = _fmt_date(due_dt)

        # --- Collect activities for this stage ---
        stage_acts = _all_activities_for_stage(activities, stage_name, client_name, project_name)
        latest     = _latest_activity_for_stage(activities, stage_name, client_name, project_name)

        # --- Run stage evaluator ---
        evaluator = _STAGE_EVALUATORS.get(stage_name)
        signal_status, signal_source = evaluator(stage_acts, schedule) if evaluator else ("", "")

        # --- Derive final status ---
        completed_flag    = False
        completed_at      = ""
        completion_source = ""
        evidence_type     = signal_source if signal_source else (latest.source_type if latest else "")
        evidence_reference = latest.reference if latest else ""
        last_activity_date = ""

        if latest:
            try:
                ts = latest.timestamp.strip()
                if ts.endswith("Z"):
                    ts = ts[:-1] + "+00:00"
                ldt = datetime.fromisoformat(ts)
                if ldt.tzinfo is None:
                    ldt = ldt.replace(tzinfo=timezone.utc)
                last_activity_date = ldt.strftime("%m/%d/%Y")
            except ValueError:
                pass

        if signal_status == "Complete":
            status            = "Complete"
            completed_flag    = True
            completed_at      = last_activity_date
            completion_source = signal_source or "Rule"

        elif signal_status == "In Progress":
            # Could still be Overdue if due date passed
            if due_dt and due_dt.date() < today.date():
                status = "Overdue"
            else:
                status = "In Progress"

        else:
            # No signal — apply date-based logic
            if due_dt is None:
                # No due date and no signal — treat as Upcoming (no date to anchor)
                status = "Upcoming"
            elif due_dt.date() < today.date():
                status = "Overdue"
            elif (due_dt.date() - today.date()).days <= UPCOMING_WINDOW_DAYS:
                status = "Not Started"
            else:
                status = "Upcoming"

        # --- Owner ---
        owner_name, owner_email = _resolve_owner(stage_name, team, jordan_role)

        # --- Preserved manual fields ---
        key    = (client_name, project_name, stage_name)
        notes  = notes_map.get(key, "")
        manual = override_map.get(key, False)

        results.append(WorkItemStatus(
            client_name=client_name,
            project_name=project_name,
            stage_name=stage_name,
            status=status,
            due_date=due_date_str,
            owner=owner_name,
            owner_email=owner_email,
            completed_flag=completed_flag,
            completed_at=completed_at,
            completion_source=completion_source,
            evidence_type=evidence_type,
            evidence_reference=evidence_reference,
            last_activity_date=last_activity_date,
            manual_override=manual,
            notes=notes,
            sort_order=sort_idx,
        ))

    return results


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

def print_status_summary(all_statuses: List[WorkItemStatus]) -> None:
    """Print a verification summary of derived statuses to stdout."""
    from collections import Counter
    counts: Counter = Counter(ws.status for ws in all_statuses)

    print(f"\n{'='*60}")
    print("Status Logic Summary")
    print(f"{'='*60}")
    print(f"Total work items : {len(all_statuses)}")
    for status_label in ["Complete", "In Progress", "Overdue", "Not Started", "Upcoming"]:
        print(f"  {status_label:<15} {counts.get(status_label, 0)}")

    overdue = [ws for ws in all_statuses if ws.status == "Overdue"]
    if overdue:
        print(f"\n--- Overdue stages ---")
        for ws in overdue[:10]:
            print(f"  {ws.client_name} | {ws.project_name} | {ws.stage_name} | due {ws.due_date}")

    in_prog = [ws for ws in all_statuses if ws.status == "In Progress"]
    if in_prog:
        print(f"\n--- In Progress stages (sample, up to 5) ---")
        for ws in in_prog[:5]:
            print(f"  {ws.client_name} | {ws.stage_name} | {ws.evidence_reference[:60]}")

    print()
