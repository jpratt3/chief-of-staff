from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from openpyxl import load_workbook


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SNAPSHOT_FILENAME = "prev_work_items.json"
_CHANGES_FILENAME = "latest_changes.json"

# Fields that form the identity key for a work item row.
# A row is "the same" across runs when all three match.
_KEY_FIELDS = ("client_name", "project_name", "work_item_name")

# Fields compared to detect changes within a matched row.
_TRACKED_FIELDS = (
    "status",
    "last_activity_date",
    "evidence_reference",
    "evidence_type",
    "completed_flag",
)

# Client_Project_Work_Items column order (must match workbook_manager.py).
_WORK_ITEMS_COLUMNS = [
    "client_name",
    "project_name",
    "work_item_name",
    "work_item_type",
    "status",
    "owner",
    "due_date",
    "completed_flag",
    "completed_at",
    "completion_source",
    "manual_override",
    "notes",
    "evidence_type",
    "evidence_reference",
    "last_activity_date",
    "blocked_flag",
    "stage_name",
    "priority",
    "project_type",
    "sort_order",
]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Run state (existing, unchanged)
# ---------------------------------------------------------------------------

DEFAULT_STATE = {
    "last_successful_run": None,
    "last_inbox_pull_start": None,
    "last_sent_pull_start": None,
    "last_calendar_pull_start": None,
    "last_briefing_sent_date": None,
    "recent_message_ids": [],
    "recent_event_ids": [],
}


def load_state(state_path: str) -> dict:
    path = Path(state_path)

    if not path.exists():
        return DEFAULT_STATE.copy()

    raw_text = path.read_text(encoding="utf-8").strip()
    if not raw_text:
        return DEFAULT_STATE.copy()

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        return DEFAULT_STATE.copy()

    merged = DEFAULT_STATE.copy()
    merged.update(data)
    return merged


def save_state(state_path: str, state: dict) -> None:
    path = Path(state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _read_work_items_from_workbook(workbook_path: Path) -> List[Dict[str, Any]]:
    """
    Read the Client_Project_Work_Items tab and return a list of row dicts.
    Raises PermissionError if the file is locked (caller handles this).
    """
    workbook = load_workbook(workbook_path, data_only=True, read_only=True)
    sheet = workbook["Client_Project_Work_Items"]

    rows: List[Dict[str, Any]] = []
    for row in sheet.iter_rows(min_row=2, values_only=True):
        if not any(v is not None and v != "" for v in row):
            continue
        row_dict = {
            _WORK_ITEMS_COLUMNS[i]: row[i]
            for i in range(min(len(_WORK_ITEMS_COLUMNS), len(row)))
        }
        rows.append(row_dict)

    workbook.close()
    return rows


def _row_key(row: Dict[str, Any]) -> Tuple[str, ...]:
    return tuple(str(row.get(f) or "") for f in _KEY_FIELDS)


def _serialize_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Normalize all values to strings for clean JSON serialization."""
    return [
        {k: str(v) if v is not None else "" for k, v in row.items()}
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------

def snapshot_work_items(workbook_path: Path, data_dir: Path) -> bool:
    """
    Read the current Client_Project_Work_Items tab and save it to
    data/prev_work_items.json BEFORE the run clears and rewrites the tab.

    Returns True on success, False if the workbook is locked or missing.
    run_daily.py should abort the run if this returns False.
    """
    if not workbook_path.exists():
        logger.warning(
            "snapshot_work_items: workbook not found at %s — skipping snapshot.",
            workbook_path,
        )
        return False

    try:
        rows = _read_work_items_from_workbook(workbook_path)
    except PermissionError:
        logger.error(
            "snapshot_work_items: workbook is locked by another process at %s. "
            "Close the workbook and re-run. Aborting snapshot.",
            workbook_path,
        )
        return False
    except Exception as exc:
        logger.error("snapshot_work_items: unexpected error reading workbook: %s", exc)
        return False

    snapshot_path = data_dir / _SNAPSHOT_FILENAME
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(
        json.dumps(_serialize_rows(rows), indent=2),
        encoding="utf-8",
    )
    logger.info(
        "snapshot_work_items: wrote %d rows to %s", len(rows), snapshot_path
    )
    return True


# ---------------------------------------------------------------------------
# Compare
# ---------------------------------------------------------------------------

def compare_work_items(workbook_path: Path, data_dir: Path) -> Optional[Dict[str, Any]]:
    """
    Compare the current Client_Project_Work_Items tab against the snapshot
    saved by snapshot_work_items() and write the diff to data/latest_changes.json.

    Returns the changes dict on success, or None if the workbook is locked/missing.
    On first run (no snapshot exists), writes an empty changes file and returns
    an empty changes dict — this is not an error.
    """
    snapshot_path = data_dir / _SNAPSHOT_FILENAME
    changes_path = data_dir / _CHANGES_FILENAME

    # Load previous snapshot (empty on first run — not an error)
    prev_rows: List[Dict[str, Any]] = []
    if snapshot_path.exists():
        try:
            prev_rows = json.loads(snapshot_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(
                "compare_work_items: could not read snapshot: %s — treating as empty.", exc
            )

    # Load current rows from the freshly written workbook
    if not workbook_path.exists():
        logger.warning("compare_work_items: workbook not found — skipping comparison.")
        return None

    try:
        curr_rows = _serialize_rows(_read_work_items_from_workbook(workbook_path))
    except PermissionError:
        logger.error(
            "compare_work_items: workbook is locked at %s — skipping comparison.",
            workbook_path,
        )
        return None
    except Exception as exc:
        logger.error("compare_work_items: unexpected error reading workbook: %s", exc)
        return None

    # Build lookup dicts keyed by (client_name, project_name, work_item_name)
    prev_by_key = {_row_key(r): r for r in prev_rows}
    curr_by_key = {_row_key(r): r for r in curr_rows}

    added: List[Dict[str, Any]] = []
    removed: List[Dict[str, Any]] = []
    changed: List[Dict[str, Any]] = []

    # Rows present now but not before
    for key, row in curr_by_key.items():
        if key not in prev_by_key:
            added.append({f: row.get(f, "") for f in list(_KEY_FIELDS) + list(_TRACKED_FIELDS)})

    # Rows present before but not now
    for key, row in prev_by_key.items():
        if key not in curr_by_key:
            removed.append({f: row.get(f, "") for f in list(_KEY_FIELDS) + list(_TRACKED_FIELDS)})

    # Rows in both — check tracked fields for changes
    for key in prev_by_key.keys() & curr_by_key.keys():
        prev = prev_by_key[key]
        curr = curr_by_key[key]
        field_changes: Dict[str, Any] = {}
        for field in _TRACKED_FIELDS:
            prev_val = str(prev.get(field) or "")
            curr_val = str(curr.get(field) or "")
            if prev_val != curr_val:
                field_changes[field] = {"from": prev_val, "to": curr_val}
        if field_changes:
            identity = {f: curr.get(f, "") for f in _KEY_FIELDS}
            changed.append({**identity, "changes": field_changes})

    result: Dict[str, Any] = {
        "added": added,
        "removed": removed,
        "changed": changed,
        "summary": {
            "added_count": len(added),
            "removed_count": len(removed),
            "changed_count": len(changed),
        },
    }

    changes_path.parent.mkdir(parents=True, exist_ok=True)
    changes_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    logger.info(
        "compare_work_items: added=%d removed=%d changed=%d — wrote to %s",
        len(added), len(removed), len(changed), changes_path,
    )
    return result
