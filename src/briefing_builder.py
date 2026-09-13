"""
briefing_builder.py — Chunk 8
Assembles the daily plain-text briefing from in-memory work item statuses
and the on-disk latest_changes.json produced by state_store.compare_work_items().

Single responsibility: given a list of WorkItemStatus objects and a project
root path, return a formatted plain-text briefing string.  No Outlook COM
calls, no workbook writes — pure reading and formatting.

Public API
----------
build_briefing(all_statuses, project_root) -> str

Sections (in order):
    1. Header          — date and run timestamp
    2. Changes         — items whose status/activity changed since last run
    3. Action Required — all Overdue items + items that moved to In Progress
    4. Upcoming        — items due within 14 days, not yet Complete
    5. In Progress     — all In Progress items grouped by client
    6. Quiet Clients   — active clients with no In Progress or Complete items
                         and no activity in the last 14 days
    7. Footer          — workbook path
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.status_logic import WorkItemStatus

logger = logging.getLogger(__name__)

_CHANGES_FILENAME = "latest_changes.json"
_UPCOMING_WINDOW_DAYS = 14
_QUIET_CLIENT_WINDOW_DAYS = 14

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _today_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_mmddyyyy(value: str | None) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.strptime(str(value).strip(), "%m/%d/%Y").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _load_changes(project_root: Path) -> Dict[str, Any]:
    """Load latest_changes.json; return empty structure on missing/corrupt file."""
    changes_path = project_root / "data" / _CHANGES_FILENAME
    empty: Dict[str, Any] = {
        "added": [], "removed": [], "changed": [],
        "summary": {"added_count": 0, "removed_count": 0, "changed_count": 0},
    }
    if not changes_path.exists():
        logger.warning("briefing_builder: %s not found — changes section will be empty.", changes_path)
        return empty
    try:
        return json.loads(changes_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("briefing_builder: could not read %s: %s", changes_path, exc)
        return empty


def _divider(char: str = "-", width: int = 60) -> str:
    return char * width


def _section_header(title: str) -> str:
    return f"\n{_divider('=')}\n{title.upper()}\n{_divider('=')}"


def _fmt_item(ws: WorkItemStatus, *, show_owner: bool = True, show_evidence: bool = True) -> str:
    """Format a single WorkItemStatus as a compact text line."""
    parts = [f"  {ws.client_name}  |  {ws.stage_name}  |  {ws.status}"]
    if ws.due_date:
        parts.append(f"due {ws.due_date}")
    if show_owner and ws.owner:
        parts.append(f"owner: {ws.owner}")
    if show_evidence and ws.evidence_reference:
        ref = ws.evidence_reference[:80].strip()
        if ref:
            parts.append(f'"{ref}"')
    return "  ".join(parts)


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------

def _section_header_text(project_root: Path) -> str:
    """Section 1 — date and run timestamp."""
    now = _today_utc()
    date_str = f"{now.month}/{now.day}/{now.year}"
    time_str = now.strftime("%I:%M %p").lstrip("0")
    lines = [
        _divider("="),
        "CHIEF OF STAFF — DAILY BRIEFING",
        f"{date_str}  {time_str} UTC",
        _divider("="),
    ]
    return "\n".join(lines)


def _section_changes(changes: Dict[str, Any]) -> str:
    """
    Section 2 — Changes Since Last Run.

    Shows: status changes (from → to), new items added, items removed.
    If nothing changed: single 'No changes' line.
    """
    lines = [_section_header("Changes Since Last Run")]

    changed = changes.get("changed", [])
    added   = changes.get("added", [])
    removed = changes.get("removed", [])

    if not changed and not added and not removed:
        lines.append("  No status changes since last run.")
        return "\n".join(lines)

    if changed:
        lines.append(f"\n  Status changes ({len(changed)}):")
        for item in changed:
            client = item.get("client_name", "")
            stage  = item.get("work_item_name", "")
            for field, diff in item.get("changes", {}).items():
                from_val = diff.get("from", "")
                to_val   = diff.get("to", "")
                if field == "status":
                    lines.append(f"    {client}  |  {stage}  |  {from_val} → {to_val}")
                elif field == "last_activity_date" and from_val != to_val:
                    lines.append(f"    {client}  |  {stage}  |  last activity: {from_val} → {to_val}")

    if added:
        lines.append(f"\n  New items ({len(added)}):")
        for item in added:
            lines.append(f"    {item.get('client_name', '')}  |  {item.get('work_item_name', '')}  |  {item.get('status', '')}")

    if removed:
        lines.append(f"\n  Removed items ({len(removed)}):")
        for item in removed:
            lines.append(f"    {item.get('client_name', '')}  |  {item.get('work_item_name', '')}")

    return "\n".join(lines)


def _section_action_required(
    all_statuses: List[WorkItemStatus],
    changes: Dict[str, Any],
) -> str:
    """
    Section 3 — Action Required.

    Surfaces:
    - All Overdue items
    - Items that moved to In Progress since last run (status change: * → In Progress)

    No role filter — all items surface regardless of owner.
    """
    lines = [_section_header("Action Required")]

    overdue = [ws for ws in all_statuses if ws.status == "Overdue"]
    overdue.sort(key=lambda ws: (ws.due_date or "12/31/9999", ws.client_name))

    # Items that flipped to In Progress this run
    newly_in_progress_keys = set()
    for item in changes.get("changed", []):
        for field, diff in item.get("changes", {}).items():
            if field == "status" and diff.get("to") == "In Progress":
                key = (
                    item.get("client_name", ""),
                    item.get("project_name", ""),
                    item.get("work_item_name", ""),
                )
                newly_in_progress_keys.add(key)

    newly_in_progress = [
        ws for ws in all_statuses
        if ws.status == "In Progress"
        and (ws.client_name, ws.project_name, ws.stage_name) in newly_in_progress_keys
    ]
    newly_in_progress.sort(key=lambda ws: ws.client_name)

    if not overdue and not newly_in_progress:
        lines.append("  Nothing requires immediate action.")
        return "\n".join(lines)

    if overdue:
        lines.append(f"\n  Overdue ({len(overdue)}):")
        for ws in overdue:
            lines.append(_fmt_item(ws, show_owner=True, show_evidence=False))

    if newly_in_progress:
        lines.append(f"\n  Moved to In Progress this run ({len(newly_in_progress)}):")
        for ws in newly_in_progress:
            lines.append(_fmt_item(ws, show_owner=True, show_evidence=True))

    return "\n".join(lines)


def _section_upcoming(all_statuses: List[WorkItemStatus]) -> str:
    """
    Section 4 — Upcoming Deadlines.

    Items due within the next 14 days that are not yet Complete,
    grouped by client, sorted by due date within each client.
    """
    lines = [_section_header("Upcoming Deadlines (Next 14 Days)")]

    today = _today_utc()
    cutoff = today + timedelta(days=_UPCOMING_WINDOW_DAYS)

    upcoming = []
    for ws in all_statuses:
        if ws.status == "Complete":
            continue
        due_dt = _parse_mmddyyyy(ws.due_date)
        if due_dt is None:
            continue
        if today.date() <= due_dt.date() <= cutoff.date():
            upcoming.append(ws)

    if not upcoming:
        lines.append("  No items due within the next 14 days.")
        return "\n".join(lines)

    # Group by client
    by_client: Dict[str, List[WorkItemStatus]] = {}
    for ws in upcoming:
        by_client.setdefault(ws.client_name, []).append(ws)

    for client_name in sorted(by_client):
        lines.append(f"\n  {client_name}")
        items = sorted(by_client[client_name], key=lambda ws: ws.due_date or "12/31/9999")
        for ws in items:
            lines.append(f"    {ws.stage_name}  |  {ws.status}  |  due {ws.due_date}  |  owner: {ws.owner}")

    return "\n".join(lines)


def _section_in_progress(all_statuses: List[WorkItemStatus]) -> str:
    """
    Section 5 — In Progress by Client.

    All In Progress items grouped by client.
    Shows stage name, owner, last activity date, and evidence snippet.
    """
    lines = [_section_header("In Progress by Client")]

    in_prog = [ws for ws in all_statuses if ws.status == "In Progress"]

    if not in_prog:
        lines.append("  No items currently In Progress.")
        return "\n".join(lines)

    by_client: Dict[str, List[WorkItemStatus]] = {}
    for ws in in_prog:
        by_client.setdefault(ws.client_name, []).append(ws)

    for client_name in sorted(by_client):
        lines.append(f"\n  {client_name}")
        items = sorted(by_client[client_name], key=lambda ws: ws.sort_order)
        for ws in items:
            row = f"    {ws.stage_name}  |  owner: {ws.owner}"
            if ws.last_activity_date:
                row += f"  |  last activity: {ws.last_activity_date}"
            if ws.evidence_reference:
                ref = ws.evidence_reference[:80].strip()
                if ref:
                    row += f'  |  "{ref}"'
            lines.append(row)

    return "\n".join(lines)


def _section_quiet_clients(all_statuses: List[WorkItemStatus]) -> str:
    """
    Section 6 — Quiet Clients.

    Active clients with:
    - No In Progress or Complete items, AND
    - No activity in the last 14 days (last_activity_date is empty or older than cutoff)

    Flags by client name only — no diagnosis.
    """
    lines = [_section_header("Quiet Clients")]

    today = _today_utc()
    cutoff = today - timedelta(days=_QUIET_CLIENT_WINDOW_DAYS)

    # Group all statuses by client
    by_client: Dict[str, List[WorkItemStatus]] = {}
    for ws in all_statuses:
        by_client.setdefault(ws.client_name, []).append(ws)

    quiet: List[str] = []

    for client_name, items in sorted(by_client.items()):
        has_active = any(ws.status in {"In Progress", "Complete"} for ws in items)
        if has_active:
            continue

        # Check for recent activity
        recent_activity = False
        for ws in items:
            if not ws.last_activity_date:
                continue
            dt = _parse_mmddyyyy(ws.last_activity_date)
            if dt and dt.date() >= cutoff.date():
                recent_activity = True
                break

        if not recent_activity:
            quiet.append(client_name)

    if not quiet:
        lines.append("  All active clients have recent activity or In Progress items.")
        return "\n".join(lines)

    lines.append(f"\n  {len(quiet)} client(s) with no recent signal:")
    for name in quiet:
        lines.append(f"    {name}")

    return "\n".join(lines)


def _section_footer(project_root: Path) -> str:
    """Section 7 — Footer with workbook path."""
    workbook_path = project_root / "workbook" / "chief_of_staff_tracker.xlsx"
    lines = [
        "",
        _divider("-"),
        f"Full detail: {workbook_path}",
        _divider("-"),
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_briefing(all_statuses: List[WorkItemStatus], project_root: Path) -> str:
    """
    Assemble and return the daily plain-text briefing string.

    Parameters
    ----------
    all_statuses : List of WorkItemStatus from workbook_writes.build_client_project_work_items()
    project_root : Project root path (used to locate latest_changes.json and workbook)

    Returns
    -------
    str — complete briefing, ready to print or send.
    """
    root = Path(project_root)
    changes = _load_changes(root)

    sections = [
        _section_header_text(root),
        _section_changes(changes),
        _section_action_required(all_statuses, changes),
        _section_upcoming(all_statuses),
        _section_in_progress(all_statuses),
        _section_quiet_clients(all_statuses),
        _section_footer(root),
    ]

    return "\n".join(sections)
