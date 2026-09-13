from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from openpyxl import load_workbook

from src.workbook_manager import get_workbook_path
from src.status_logic import derive_statuses, WorkItemStatus


STANDARD_STAGE_ORDER = [
    "Renewal Preparation",
    "RSM",
    "Submission",
    "Proposal",
    "Bind",
    "Invoice",
    "Post Binding",
]

STAGE_SORT_ORDER = {stage: index + 1 for index, stage in enumerate(STANDARD_STAGE_ORDER)}

STAGE_PATTERNS = [
    (re.compile(r"^renewal preparation$", re.IGNORECASE), "Renewal Preparation"),
    (re.compile(r"^rsm$", re.IGNORECASE), "RSM"),
    (re.compile(r"^submission$", re.IGNORECASE), "Submission"),
    (re.compile(r"^proposal$", re.IGNORECASE), "Proposal"),
    (re.compile(r"^bind$", re.IGNORECASE), "Bind"),
    (re.compile(r"^invoice$", re.IGNORECASE), "Invoice"),
    (re.compile(r"^post binding$", re.IGNORECASE), "Post Binding"),
    (re.compile(r"^deliver policies$", re.IGNORECASE), "Post Binding"),
]



def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_row(worksheet, values: List[Any]) -> None:
    worksheet.append(values)


def _parse_iso_like(value: str | None) -> Optional[datetime]:
    if not value:
        return None

    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    if " " in text and "T" not in text:
        text = text.replace(" ", "T", 1)

    try:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _to_mmddyyyy(value: str | datetime | None) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().strftime("%m/%d/%Y")
    parsed = _parse_iso_like(value)
    if parsed is None:
        text = str(value).strip()
        if re.match(r"^\d{2}/\d{2}/\d{4}$", text):
            return text
        return ""
    return parsed.date().strftime("%m/%d/%Y")


def _normalize_subject(subject: str | None) -> str:
    if not subject:
        return ""

    value = str(subject).strip()
    prefix_pattern = re.compile(r"^(?:(?:re|fw|fwd)\s*:\s*)+", re.IGNORECASE)

    previous = None
    while previous != value:
        previous = value
        value = prefix_pattern.sub("", value).strip()

    value = re.sub(r"\s+", " ", value)
    return value


def _normalize_stage_name(stage_name: str | None) -> str:
    if not stage_name:
        return ""
    value = str(stage_name).strip()
    for pattern, canonical in STAGE_PATTERNS:
        if pattern.match(value):
            return canonical
    return value


def _clear_sheet_but_keep_header(worksheet) -> None:
    if worksheet.max_row > 1:
        worksheet.delete_rows(2, worksheet.max_row - 1)


def _read_sheet_rows(worksheet) -> List[Dict[str, Any]]:
    headers = [cell.value for cell in worksheet[1]]
    rows: List[Dict[str, Any]] = []

    for row in worksheet.iter_rows(min_row=2, values_only=True):
        if not any(value is not None and value != "" for value in row):
            continue
        rows.append({headers[index]: row[index] for index in range(len(headers))})

    return rows


def _ensure_lookup_value(worksheet, list_name: str, value: str, sort_order: int, active: bool) -> None:
    for row in worksheet.iter_rows(min_row=2, values_only=True):
        if not row:
            continue
        if str(row[0] or "").strip() == list_name and str(row[1] or "").strip() == value:
            return
    worksheet.append([list_name, value, sort_order, active])


def seed_lookup_lists(project_root: Path) -> Path:
    workbook_path = get_workbook_path(project_root)
    workbook = load_workbook(workbook_path)
    worksheet = workbook["Lookup_Lists"]

    lookup_values = [
        ("project_type", "Renewal", 1, True),
        ("project_type", "Other", 2, True),
        ("overall_status", "On Track", 1, True),
        ("overall_status", "At Risk", 2, True),
        ("overall_status", "Blocked", 3, True),
        ("overall_status", "Complete", 4, True),
        ("work_item_status", "Upcoming", 1, True),
        ("work_item_status", "Not Started", 2, True),
        ("work_item_status", "In Progress", 3, True),
        ("work_item_status", "Waiting", 4, True),
        ("work_item_status", "Complete", 5, True),
        ("work_item_status", "Overdue", 6, True),
        ("priority", "High", 1, True),
        ("priority", "Normal", 2, True),
        ("priority", "Low", 3, True),
        ("role_owner_type", "AE", 1, True),
        ("role_owner_type", "AAE", 2, True),
        ("role_owner_type", "AR", 3, True),
        ("role_owner_type", "ALL", 4, True),
        ("work_item_type", "Standard", 1, True),
        ("work_item_type", "Other", 2, True),
        ("evidence_type", "Email", 1, True),
        ("evidence_type", "Calendar", 2, True),
        ("evidence_type", "Manual", 3, True),
        ("evidence_type", "Rule", 4, True),
        ("completion_source", "Manual", 1, True),
        ("completion_source", "Email", 2, True),
        ("completion_source", "Calendar", 3, True),
        ("completion_source", "Rule", 4, True),
        ("confidence", "High", 1, True),
        ("confidence", "Medium", 2, True),
        ("confidence", "Low", 3, True),
    ]

    for list_name, value, sort_order, active in lookup_values:
        _ensure_lookup_value(worksheet, list_name, value, sort_order, active)

    workbook.save(workbook_path)
    workbook.close()
    return workbook_path


def seed_evidence_rules(project_root: Path) -> Path:
    workbook_path = get_workbook_path(project_root)
    workbook = load_workbook(workbook_path)
    worksheet = workbook["Evidence_Rules"]

    existing_rules = {
        str(row.get("rule_name") or "").strip()
        for row in _read_sheet_rows(worksheet)
        if str(row.get("rule_name") or "").strip()
    }

    rules = [
        [
            "RSM calendar event occurred",
            True,
            "Renewal",
            "RSM",
            "Calendar",
            "event_subject",
            "RSM",
            "Complete",
            True,
            "High",
            False,
            "If an RSM calendar event occurs, treat the RSM stage as complete.",
        ],
        [
            "Submission email signal",
            True,
            "Renewal",
            "Submission",
            "Email",
            "subject",
            "submission",
            "In Progress",
            False,
            "Medium",
            True,
            "Submission-related email suggests submission stage is active.",
        ],
        [
            "Proposal email signal",
            True,
            "Renewal",
            "Proposal",
            "Email",
            "subject",
            "proposal",
            "In Progress",
            False,
            "Medium",
            True,
            "Proposal-related email suggests proposal stage is active.",
        ],
        [
            "Bind email signal",
            True,
            "Renewal",
            "Bind",
            "Email",
            "subject",
            "binder",
            "In Progress",
            False,
            "Medium",
            True,
            "Binder-related email suggests bind activity but should still allow review.",
        ],
        [
            "Invoice email signal",
            True,
            "Renewal",
            "Invoice",
            "Email",
            "subject",
            "invoice",
            "In Progress",
            False,
            "Medium",
            True,
            "Invoice-related email suggests invoice stage is active.",
        ],
        [
            "Deliver policies email signal",
            True,
            "Renewal",
            "Post Binding",
            "Email",
            "subject",
            "policy",
            "In Progress",
            False,
            "Medium",
            True,
            "Policy delivery-related email suggests delivery stage is active.",
        ],
    ]

    for rule in rules:
        if rule[0] not in existing_rules:
            worksheet.append(rule)

    workbook.save(workbook_path)
    workbook.close()
    return workbook_path


def seed_workflow_template(project_root: Path) -> Path:
    workbook_path = get_workbook_path(project_root)
    workbook = load_workbook(workbook_path)
    worksheet = workbook["Workflow_Template"]

    _clear_sheet_but_keep_header(worksheet)

    # Generic commercial P&C renewal workflow. Column order matches
    # SHEET_COLUMNS["Workflow_Template"] in workbook_manager.py:
    #   stage_name, stage_window, task_name, role_owner_type,
    #   ae_flag, aae_flag, ar_flag, timing_detail,
    #   stage_sort_order, task_sort_order, active, template_source
    rows = [
        ["Renewal Preparation", "180-120 Days", "Open the renewal file in the agency management system and confirm expiring terms, carriers, and premiums.", "AR", False, False, True, "", 1, 1, True, "Projects.xlsx"],
        ["Renewal Preparation", "180-120 Days", "Send the internal renewal kickoff note with team assignments, renewal date, and target dates.", "AE", True, True, False, "", 1, 2, True, "Projects.xlsx"],
        ["Renewal Preparation", "180-120 Days", "Confirm the service team for the renewal and record who owns each line of coverage.", "AE", True, True, False, "", 1, 3, True, "Projects.xlsx"],
        ["Renewal Preparation", "180-120 Days", "Review the Exempt Commercial Purchaser (ECP) form and confirm it is current for any surplus lines placement.", "AR", False, False, True, "", 1, 4, True, "Projects.xlsx"],
        ["Renewal Preparation", "180-120 Days", "Confirm the surplus lines home state assignment for the coming term and notify the placement contact.", "AR", False, False, True, "", 1, 5, True, "Projects.xlsx"],
        ["Renewal Preparation", "180-120 Days", "Schedule the Internal Strategy Meeting (ISM) and circulate an agenda covering client goals and known changes.", "AAE", False, True, True, "", 1, 6, True, "Projects.xlsx"],
        ["Renewal Preparation", "180-120 Days", "Build the ISM deck from the prior-year version, refreshed for current exposures and market conditions.", "AAE", False, True, True, "", 1, 7, True, "Projects.xlsx"],
        ["Renewal Preparation", "180-120 Days", "Request loss runs from each incumbent carrier and build a loss summary by line.", "AAE", False, True, True, "", 1, 8, True, "Projects.xlsx"],
        ["Renewal Preparation", "180-120 Days", "Send the client an exposure update request with prior-year schedules prefilled.", "AAE", False, True, False, "", 1, 9, True, "Projects.xlsx"],

        ["RSM", "180-90 Days", "Pull benchmarking and market condition data for the client's industry and program size.", "AAE", False, True, True, "180-120 Days", 2, 1, True, "Projects.xlsx"],
        ["RSM", "180-90 Days", "Draft the renewal strategy document: loss summary, exposure changes, and program options.", "AR", False, False, True, "180-120 Days", 2, 2, True, "Projects.xlsx"],
        ["RSM", "180-90 Days", "Review and approve the strategy document before it goes to the client.", "AE", True, True, False, "180-90 Days", 2, 3, True, "Projects.xlsx"],
        ["RSM", "180-90 Days", "Hold the renewal strategy meeting with the client and agree on the market approach.", "ALL", True, True, True, "180-90 Days", 2, 4, True, "Projects.xlsx"],
        ["RSM", "180-90 Days", "Confirm target terms, pricing expectations, and the list of markets to approach.", "AE", True, True, False, "180-90 Days", 2, 5, True, "Projects.xlsx"],
        ["RSM", "180-90 Days", "Send a written recap covering agreed strategy, assignments, and target dates.", "AR", False, True, True, "180-90 Days", 2, 6, True, "Projects.xlsx"],

        ["Submission", "90-60 Days", "Review returned exposure data for completeness and follow up on gaps.", "AAE", False, True, False, "", 3, 1, True, "Projects.xlsx"],
        ["Submission", "90-60 Days", "Complete ACORD applications and supplemental forms for each line being marketed.", "AAE", False, True, True, "", 3, 2, True, "Projects.xlsx"],
        ["Submission", "90-60 Days", "Refresh loss runs if the valuation date will be stale by the time quotes are due.", "AR", False, False, True, "", 3, 3, True, "Projects.xlsx"],
        ["Submission", "90-60 Days", "Assemble the submission package and route it to the AE for review.", "AAE", False, True, False, "", 3, 4, True, "Projects.xlsx"],
        ["Submission", "90-60 Days", "Approve the submission and share it with the client before release.", "AE", True, True, False, "", 3, 5, True, "Projects.xlsx"],
        ["Submission", "90-60 Days", "Release the submission to the market with a stated quote due date and track receipt.", "ALL", True, True, True, "", 3, 6, True, "Projects.xlsx"],

        ["Proposal", "60-15 Days", "Log quotes as they arrive and chase markets that have not responded by the due date.", "AAE", False, True, True, "", 4, 1, True, "Projects.xlsx"],
        ["Proposal", "60-15 Days", "Route underwriter questions to the client team and return the answers to the market.", "AAE", False, True, False, "", 4, 2, True, "Projects.xlsx"],
        ["Proposal", "60-15 Days", "Build the Cost & Coverage Comparison (CCC) against expiring terms and flag gaps, new exclusions, and changed sublimits.", "AR", False, False, True, "", 4, 3, True, "Projects.xlsx"],
        ["Proposal", "60-15 Days", "Negotiate pricing, terms, and conditions with the preferred markets.", "AE", True, True, False, "", 4, 4, True, "Projects.xlsx"],
        ["Proposal", "60-15 Days", "Finalize the proposal, including compensation disclosure, and present it to the client.", "AE", True, True, True, "", 4, 5, True, "Projects.xlsx"],
        ["Proposal", "60-15 Days", "Offer premium finance options where the client wants to spread payment.", "AE", True, False, True, "", 4, 6, True, "Projects.xlsx"],

        ["Bind", "15-0 Days", "Obtain written bind instructions from the client before the expiration date.", "AE", True, True, False, "", 5, 1, True, "Projects.xlsx"],
        ["Bind", "15-0 Days", "Issue bind orders to the selected carriers and confirm receipt.", "AE", True, True, True, "", 5, 2, True, "Projects.xlsx"],
        ["Bind", "15-0 Days", "Check binders against the quoted terms and request corrections in writing.", "AR", False, True, True, "", 5, 3, True, "Projects.xlsx"],
        ["Bind", "15-0 Days", "Send the client the binders, a binder transmittal letter, and the list of open subjectivities.", "AR", False, False, True, "", 5, 4, True, "Projects.xlsx"],
        ["Bind", "15-0 Days", "Update the agency management system and the certificate system with the bound policy terms.", "AR", False, False, True, "", 5, 5, True, "Projects.xlsx"],
        ["Bind", "15-0 Days", "Collect signed applications, surplus lines forms, and any other carrier-required signatures.", "AR", False, False, True, "", 5, 6, True, "Projects.xlsx"],

        ["Invoice", "0-5 Days", "Confirm bound premium, taxes, fees, and surplus lines charges against the binder.", "AR", False, False, True, "", 6, 1, True, "Projects.xlsx"],
        ["Invoice", "0-5 Days", "Agree the premium allocation across entities or locations with the client.", "AAE", False, True, True, "", 6, 2, True, "Projects.xlsx"],
        ["Invoice", "0-5 Days", "Issue the invoice and record the payment terms and due date.", "AR", False, False, True, "", 6, 3, True, "Projects.xlsx"],
        ["Invoice", "0-5 Days", "Reconcile carrier invoices to the binder and resolve differences before they age.", "AR", False, False, True, "", 6, 4, True, "Projects.xlsx"],
        ["Invoice", "0-5 Days", "Track payment and follow up ahead of any cancellation or audit deadline.", "AR", False, False, True, "", 6, 5, True, "Projects.xlsx"],

        ["Post Binding", "0-60 Days", "Meet internally to list open subjectivities, service commitments, and their owners.", "ALL", True, True, True, "0-21 Days", 7, 1, True, "Projects.xlsx"],
        ["Post Binding", "0-60 Days", "Close carrier subjectivities and keep written evidence in the document management system.", "AAE", False, True, True, "0-21 Days", 7, 2, True, "Projects.xlsx"],
        ["Post Binding", "0-60 Days", "Issue updated certificates and auto ID cards from the certificate system.", "AR", False, False, True, "0-21 Days", 7, 3, True, "Projects.xlsx"],
        ["Post Binding", "0-60 Days", "Produce the program summary and schedule of insurance for the bound term.", "AR", False, False, True, "0-21 Days", 7, 4, True, "Projects.xlsx"],
        ["Post Binding", "0-60 Days", "Track policy receipt and follow up with carriers that are late.", "AR", False, False, True, "30-60 Days post-bind", 7, 5, True, "Renewal_Timeline.xlsx"],
        ["Post Binding", "0-60 Days", "Check issued policies against the binder and request correcting endorsements.", "AR", False, False, True, "30-60 Days post-bind", 7, 6, True, "Renewal_Timeline.xlsx"],
        ["Post Binding", "0-60 Days", "Deliver the policies with a policy transmittal letter (PTL) and file the final set in the document management system.", "AR", False, False, True, "30-60 Days post-bind", 7, 7, True, "Renewal_Timeline.xlsx"],
    ]

    for row in rows:
        worksheet.append(row)

    workbook.save(workbook_path)
    workbook.close()
    return workbook_path


def build_activity_log(project_root: Path, activities: list) -> Path:
    """
    Write ResolvedActivity objects from the inference engine to the Activity_Log tab.

    Parameters
    ----------
    project_root : Project root path
    activities   : List of ResolvedActivity dataclasses from inference_engine.run_inference()
    """
    workbook_path = get_workbook_path(project_root)
    workbook = load_workbook(workbook_path)
    worksheet = workbook["Activity_Log"]
    _clear_sheet_but_keep_header(worksheet)

    sorted_activities = sorted(
        activities,
        key=lambda a: _parse_iso_like(a.timestamp) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    for act in sorted_activities:
        activity_date = _to_mmddyyyy(act.timestamp)
        engagement_display = (
            f"{act.project_name} [{act.engagement_label}]"
            if act.engagement_label
            else act.project_name
        )
        worksheet.append([
            act.timestamp,
            activity_date,
            act.client_name,
            engagement_display,
            act.stage_hint,
            act.activity_type,
            act.source_type,
            act.reference,
            act.sender,
            act.reference,
            True,
            "",
        ])

    workbook.save(workbook_path)
    workbook.close()
    return workbook_path


def _safe_days_to_due(value: str | None, anchor: datetime) -> Any:
    due_dt = _parse_iso_like(value) if value else None
    if due_dt is None and value:
        text = str(value).strip()
        try:
            due_dt = datetime.strptime(text, "%m/%d/%Y").replace(tzinfo=timezone.utc)
        except ValueError:
            return ""
    if due_dt is None:
        return ""
    return (due_dt.date() - anchor.date()).days


def _read_manual_notes_map(rows: List[Dict[str, Any]], key_fields: List[str], note_field: str = "notes") -> Dict[Tuple[str, ...], str]:
    notes_map: Dict[Tuple[str, ...], str] = {}
    for row in rows:
        key = tuple(str(row.get(field) or "") for field in key_fields)
        notes = str(row.get(note_field) or "")
        if notes:
            notes_map[key] = notes
    return notes_map



def _client_record_for(client_name: str, client_config: Any) -> Dict[str, Any]:
    """Return the clients.json record for client_name, or a minimal fallback dict."""
    if client_config is None:
        return {"display_name": client_name, "jordan_role": "AR", "team": {}, "engagements": []}
    raw = getattr(client_config, "raw", None) or {}
    for rec in raw.get("clients", []):
        if str(rec.get("display_name") or "").strip() == client_name:
            return rec
    return {"display_name": client_name, "jordan_role": "AR", "team": {}, "engagements": []}


def _schedule_for(
    client_name: str,
    project_name: str,
    renewal_schedules: List[Any],
) -> Any:
    """Return the RenewalSchedule for this client/project, or None."""
    if not renewal_schedules:
        return None
    # project_name is "MM/DD/YYYY Renewal" — extract date part as label
    from src.renewal_calendar import get_schedule_for_client
    # Try empty label first (single-line clients), then try matching by renewal_date_str
    for schedule in renewal_schedules:
        if schedule.client_name.lower() == client_name.lower():
            date_str = schedule.renewal_date.strftime("%m/%d/%Y")
            if project_name.startswith(date_str):
                return schedule
    # Fallback: first schedule for this client
    for schedule in renewal_schedules:
        if schedule.client_name.lower() == client_name.lower():
            return schedule
    return None


def build_client_project_work_items(project_root: Path, activities: list = None, client_config: Any = None, renewal_schedules: List[Any] = None):
    workbook_path = get_workbook_path(project_root)
    workbook = load_workbook(workbook_path)

    # Chunk 7: resolve domain data for status derivation
    resolved_activities = activities or []
    resolved_client_config = client_config
    resolved_renewal_schedules = renewal_schedules or []
    all_statuses: List[WorkItemStatus] = []

    activity_sheet = workbook["Activity_Log"]
    template_sheet = workbook["Workflow_Template"]
    target_sheet = workbook["Client_Project_Work_Items"]

    existing_rows = _read_sheet_rows(target_sheet)
    manual_notes_map = _read_manual_notes_map(existing_rows, ["client_name", "project_name", "work_item_name"])
    manual_override_map = {
        tuple(str(row.get(field) or "") for field in ["client_name", "project_name", "work_item_name"]): bool(row.get("manual_override"))
        for row in existing_rows
    }

    _clear_sheet_but_keep_header(target_sheet)

    activity_rows = _read_sheet_rows(activity_sheet)
    template_rows = _read_sheet_rows(template_sheet)

    today = datetime.now(timezone.utc)

    written_keys: Set[Tuple[str, str, str]] = set()

    # Chunk 7: drive rows from client_config engagements, not the activity log.
    # This ensures every active client gets a row every run regardless of
    # whether mail arrived in the current lookback window.
    if resolved_client_config is not None:
        active_clients = resolved_client_config.active_clients
    else:
        active_clients = []

    for client_rec in active_clients:
        client_name = client_rec.display_name
        client_record = _client_record_for(client_name, resolved_client_config)

        # Separate primary from secondary engagements.
        # An engagement is primary if primary == True, or if no engagement has
        # primary set (legacy config — treat all as primary to avoid data loss).
        all_engagements = client_rec.engagements
        primary_engagements = [e for e in all_engagements if getattr(e, "primary", None) is True]
        secondary_engagements = [e for e in all_engagements if getattr(e, "primary", None) is False]

        # Fall back to all engagements if none are explicitly flagged primary
        if not primary_engagements:
            primary_engagements = all_engagements
            secondary_engagements = []

        # Build a note string listing secondary engagements so they surface on the primary row
        secondary_note = ""
        if secondary_engagements:
            labels = [
                f"{getattr(e, 'label', '') or getattr(e, 'project_name', '')} ({getattr(e, 'renewal_date', '')})"
                for e in secondary_engagements
            ]
            secondary_note = "Also renews: " + "; ".join(labels)

        for engagement in primary_engagements:
            project_name = engagement.project_name
            renewal_schedule = _schedule_for(client_name, project_name, resolved_renewal_schedules)

            work_item_statuses: list[WorkItemStatus] = derive_statuses(
                activities=resolved_activities,
                schedule=renewal_schedule,
                client_record=client_record,
                manual_notes_map=manual_notes_map,
                manual_override_map=manual_override_map,
                project_name=project_name,
            )
            all_statuses.extend(work_item_statuses)

            first_item = True
            for ws in work_item_statuses:
                key = (ws.client_name, ws.project_name, ws.stage_name)
                # Attach secondary engagement note to the first work item row only
                if first_item and secondary_note:
                    combined_notes = "; ".join(filter(None, [ws.notes, secondary_note]))
                else:
                    combined_notes = ws.notes
                first_item = False
                target_sheet.append([
                    ws.client_name,
                    ws.project_name,
                    ws.stage_name,
                    "Standard",
                    ws.status,
                    ws.owner,
                    ws.due_date,
                    ws.completed_flag,
                    ws.completed_at,
                    ws.completion_source,
                    ws.manual_override,
                    combined_notes,
                    ws.evidence_type,
                    ws.evidence_reference,
                    ws.last_activity_date,
                    False,
                    ws.stage_name,
                    "Normal",
                    "Renewal",
                    ws.sort_order,
                ])
                written_keys.add(key)

    # Non-renewal activity: any activity log rows not already covered above
    grouped_activity: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for row in activity_rows:
        client_name = str(row.get("client_name") or "").strip()
        project_name = str(row.get("project_name") or "").strip()
        if client_name and project_name:
            grouped_activity[(client_name, project_name)].append(row)

    for (client_name, project_name), rows in grouped_activity.items():
        renewal_rows = [row for row in rows if str(row.get("project_name") or "").endswith("Renewal")]
        if renewal_rows:
            continue  # already handled above

        for row in rows:
            work_item_name = str(row.get("work_item_name") or "").strip() or "Other Work"
            key = (client_name, project_name, work_item_name)
            if key in written_keys:
                continue
            notes = manual_notes_map.get(key, "")
            manual_override = manual_override_map.get(key, False)

            target_sheet.append([
                client_name,
                project_name,
                work_item_name,
                "Other",
                "In Progress",
                "",
                "",
                False,
                "",
                "",
                manual_override,
                notes,
                row.get("source_type", ""),
                row.get("source_reference", ""),
                row.get("activity_date", ""),
                False,
                "",
                "Normal",
                "Other",
                999,
            ])
            written_keys.add(key)

    workbook.save(workbook_path)
    workbook.close()
    return workbook_path, all_statuses


def _project_name_from_renewal_date(date_str: str) -> str:
    return f"{date_str} Renewal"


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def build_client_project_summary(project_root: Path) -> Path:
    workbook_path = get_workbook_path(project_root)
    workbook = load_workbook(workbook_path)

    work_items_sheet = workbook["Client_Project_Work_Items"]
    target_sheet = workbook["Client_Project_Summary"]

    existing_rows = _read_sheet_rows(target_sheet)
    manual_notes_map = _read_manual_notes_map(existing_rows, ["client_name", "project_name"])

    _clear_sheet_but_keep_header(target_sheet)

    rows = _read_sheet_rows(work_items_sheet)
    grouped: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        client_name = str(row.get("client_name") or "").strip()
        project_name = str(row.get("project_name") or "").strip()
        if client_name and project_name:
            grouped[(client_name, project_name)].append(row)

    today = datetime.now(timezone.utc)

    for (client_name, project_name), project_rows in sorted(grouped.items()):
        project_type = str(project_rows[0].get("project_type") or "Other")
        renewal_date = ""

        if project_type == "Renewal":
            mmddyyyy_match = re.search(r"\b\d{2}/\d{2}/\d{4}\b", project_name)
            if mmddyyyy_match:
                renewal_date = mmddyyyy_match.group(0)

        open_rows = [r for r in project_rows if str(r.get("status") or "") != "Complete"]
        overdue_rows = [r for r in project_rows if str(r.get("status") or "") == "Overdue"]
        blocked_rows = [r for r in project_rows if _coerce_bool(r.get("blocked_flag"))]

        sorted_rows = sorted(
            project_rows,
            key=lambda r: (
                1 if str(r.get("status") or "") == "Complete" else 0,
                int(r.get("sort_order") or 999),
            ),
        )

        current_stage = ""
        next_milestone = ""
        next_milestone_date = ""
        priority = "Normal"
        blocked_flag = bool(blocked_rows)
        owner = ""
        last_activity_date = ""

        if open_rows:
            open_sorted = sorted(
                open_rows,
                key=lambda r: (
                    int(r.get("sort_order") or 999),
                    str(r.get("due_date") or "12/31/9999"),
                ),
            )
            current_stage = str(open_sorted[0].get("stage_name") or open_sorted[0].get("work_item_name") or "")
            next_milestone = str(open_sorted[0].get("work_item_name") or "")
            next_milestone_date = str(open_sorted[0].get("due_date") or "")
            owner = str(open_sorted[0].get("owner") or "")
            if any(str(r.get("priority") or "") == "High" for r in open_rows):
                priority = "High"
            elif any(str(r.get("priority") or "") == "Low" for r in open_rows):
                priority = "Low"
        else:
            current_stage = "Complete"
            next_milestone = ""
            next_milestone_date = ""

        activity_dates = [
            _parse_iso_like(str(r.get("last_activity_date") or ""))
            for r in project_rows
            if str(r.get("last_activity_date") or "")
        ]
        activity_dates = [dt for dt in activity_dates if dt is not None]
        if activity_dates:
            latest_dt = max(activity_dates)
            last_activity_date = latest_dt.strftime("%m/%d/%Y")

        overall_status = "On Track"
        if all(str(r.get("status") or "") == "Complete" for r in project_rows):
            overall_status = "Complete"
        elif blocked_flag:
            overall_status = "Blocked"
        elif overdue_rows:
            overall_status = "At Risk"

        days_to_due = ""
        if next_milestone_date:
            try:
                due_dt = datetime.strptime(next_milestone_date, "%m/%d/%Y").replace(tzinfo=timezone.utc)
                days_to_due = (due_dt.date() - today.date()).days
            except ValueError:
                days_to_due = ""

        notes = manual_notes_map.get((client_name, project_name), "")

        target_sheet.append([
            client_name,
            project_name,
            project_type,
            renewal_date,
            current_stage,
            next_milestone,
            next_milestone_date,
            overall_status,
            priority,
            days_to_due,
            last_activity_date,
            len(open_rows),
            len(overdue_rows),
            blocked_flag,
            owner,
            notes,
        ])

    workbook.save(workbook_path)
    workbook.close()
    return workbook_path


def refresh_overview(project_root: Path) -> Path:
    workbook_path = get_workbook_path(project_root)
    workbook = load_workbook(workbook_path)

    overview_sheet = workbook["Overview"]
    summary_sheet = workbook["Client_Project_Summary"]
    work_items_sheet = workbook["Client_Project_Work_Items"]
    activity_sheet = workbook["Activity_Log"]

    _clear_sheet_but_keep_header(overview_sheet)

    updated_at = _utc_now_iso()
    summary_rows = _read_sheet_rows(summary_sheet)
    work_item_rows = _read_sheet_rows(work_items_sheet)
    activity_rows = _read_sheet_rows(activity_sheet)

    def write(section: str, metric: str, value: Any, window: str = "") -> None:
        overview_sheet.append([section, metric, value, window, updated_at])

    needs_attention_rows = [
        row for row in work_item_rows
        if str(row.get("status") or "") in {"Overdue", "In Progress", "Waiting"}
    ]
    needs_attention_rows = sorted(
        needs_attention_rows,
        key=lambda r: (
            {"High": 1, "Normal": 2, "Low": 3}.get(str(r.get("priority") or "Normal"), 9),
            0 if str(r.get("status") or "") == "Overdue" else 1,
            str(r.get("due_date") or "12/31/9999"),
            str(r.get("last_activity_date") or ""),
        ),
    )[:15]

    for row in needs_attention_rows:
        write(
            "Needs Attention Now",
            f"{row.get('client_name')} | {row.get('project_name')} | {row.get('work_item_name')}",
            f"{row.get('priority')} | {row.get('status')} | due {row.get('due_date')}",
            "7d",
        )

    today = datetime.now(timezone.utc)

    def in_window(date_str: str, days: int) -> bool:
        try:
            dt = datetime.strptime(str(date_str), "%m/%d/%Y").replace(tzinfo=timezone.utc)
            return today.date() <= dt.date() <= (today + timedelta(days=days)).date()
        except ValueError:
            return False

    upcoming_rows = [row for row in summary_rows if in_window(str(row.get("next_milestone_date") or ""), 30)]
    upcoming_rows = sorted(
        upcoming_rows,
        key=lambda r: (
            str(r.get("next_milestone_date") or "12/31/9999"),
            {"High": 1, "Normal": 2, "Low": 3}.get(str(r.get("priority") or "Normal"), 9),
            str(r.get("client_name") or ""),
        ),
    )[:15]

    for row in upcoming_rows:
        window = "7d" if in_window(str(row.get("next_milestone_date") or ""), 7) else "30d"
        write(
            "Upcoming Milestones",
            f"{row.get('client_name')} | {row.get('project_name')}",
            f"{row.get('next_milestone')} | {row.get('next_milestone_date')} | {row.get('overall_status')}",
            window,
        )

    risk_rows = [
        row for row in summary_rows
        if str(row.get("overall_status") or "") in {"Blocked", "At Risk"}
    ]
    risk_rows = sorted(
        risk_rows,
        key=lambda r: (
            0 if str(r.get("overall_status") or "") == "Blocked" else 1,
            str(r.get("next_milestone_date") or "12/31/9999"),
            {"High": 1, "Normal": 2, "Low": 3}.get(str(r.get("priority") or "Normal"), 9),
        ),
    )[:15]

    for row in risk_rows:
        write(
            "Blocked / At Risk",
            f"{row.get('client_name')} | {row.get('project_name')}",
            f"{row.get('overall_status')} | {row.get('next_milestone')} | {row.get('next_milestone_date')}",
            "",
        )

    recent_rows = []
    for row in activity_rows:
        timestamp = _parse_iso_like(str(row.get("activity_timestamp") or ""))
        if timestamp is None:
            continue
        if timestamp < today - timedelta(days=7):
            continue
        recent_rows.append(row)

    recent_rows = sorted(
        recent_rows,
        key=lambda r: _parse_iso_like(str(r.get("activity_timestamp") or "")) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )[:20]

    for row in recent_rows:
        write(
            "Recent Activity",
            f"{row.get('client_name')} | {row.get('project_name')} | {row.get('work_item_name')}",
            f"{row.get('last_activity_date') if row.get('last_activity_date') else row.get('activity_date')} | {row.get('source_type')} | {row.get('source_reference')}",
            "7d",
        )

    workbook.save(workbook_path)
    workbook.close()
    return workbook_path


def write_unmatched_tab(project_root: Path, unmatched: list) -> Path:
    """
    Append unmatched items to the Unmatched_Events tab in the tracker workbook.

    Called by run_daily.py after run_inference() returns.  The tab is NOT
    cleared on each run so that the discard history accumulates for review.
    Each row records: source_type, timestamp, subject, folder_name, discard_reason.

    Parameters
    ----------
    project_root : Project root path
    unmatched    : List of UnmatchedItem dataclasses from inference_engine
    """
    if not unmatched:
        return get_workbook_path(project_root)

    workbook_path = get_workbook_path(project_root)
    workbook = load_workbook(workbook_path)
    worksheet = workbook["Unmatched_Events"]

    for item in unmatched:
        worksheet.append([
            item.source_type,
            item.timestamp,
            item.subject,
            item.folder_name,
            item.discard_reason,
        ])

    workbook.save(workbook_path)
    workbook.close()
    return workbook_path
