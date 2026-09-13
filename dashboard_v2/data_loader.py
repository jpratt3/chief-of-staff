"""
dashboard_v2/data_loader.py
Read-only data layer for Dashboard v2.
Reads the same source files as v1 but never writes to workbook, pipeline, or v1 files.
"""
from __future__ import annotations

import json
import random
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import openpyxl

_HERE = Path(__file__).parent
_ROOT = _HERE.parent

_WORKBOOK   = _ROOT / "workbook" / "chief_of_staff_tracker.xlsx"
_TASKS_XLSX = _ROOT / "docs" / "Renewal_Timeline.xlsx"
_CLIENTS    = _ROOT / "config" / "clients.json"
_SKILL_MAP  = _ROOT / "config" / "skill_map.json"
_COMPLETIONS = _ROOT / "data" / "completions.json"
_STAGE_RESP  = _ROOT / "data" / "stage_responses.json"

STAGES = [
    "Renewal Preparation",
    "RSM",
    "Submission",
    "Proposal",
    "Bind",
    "Invoice",
    "Post Binding",
]

STAGE_WINDOWS = {
    "Renewal Preparation": "180–120 Days",
    "RSM":                 "180–90 Days",
    "Submission":          "90–60 Days",
    "Proposal":            "60–15 Days",
    "Bind":                "15–0 Days",
    "Invoice":             "0–5 Days",
    "Post Binding":        "0–60 Days",
}

PORTALS = [
    "Renewal Pipeline",
    "Deck Builder",
    "Document Review",
    "Document Generator",
    "System Updates",
    "Meeting Scheduler",
    "Invoicing Assistant",
]

PORTAL_META = {
    "Renewal Pipeline":     {"icon": "", "color": "#2563eb", "stages": ["Renewal Preparation","RSM","Submission","Proposal"]},
    "Deck Builder":         {"icon": "", "color": "#7c3aed", "stages": ["Renewal Preparation","RSM","Proposal"]},
    "Document Review":      {"icon": "", "color": "#0891b2", "stages": ["Renewal Preparation","Submission","Proposal","Bind","Post Binding"]},
    "Document Generator":   {"icon": "",  "color": "#059669", "stages": ["Renewal Preparation","RSM","Submission","Proposal","Bind","Post Binding"]},
    "System Updates":       {"icon": "",  "color": "#d97706", "stages": ["Renewal Preparation","Proposal","Bind","Post Binding"]},
    "Meeting Scheduler":    {"icon": "", "color": "#dc2626", "stages": ["Renewal Preparation","Post Binding"]},
    "Invoicing Assistant":  {"icon": "", "color": "#065f46", "stages": ["Proposal","Invoice","Post Binding"]},
}

# Skills by portal × stage
PORTAL_SKILLS = {
    "Renewal Pipeline": {
        "Renewal Preparation": [{"name": "Exposure Request", "url": None, "status": "planned"}],
        "RSM": [
            {"name": "Certificate List", "url": None, "status": "planned"},
            {"name": "Flood Zone Determinations", "url": None, "status": "planned"},
        ],
        "Submission": [{"name": "Submission Reviewer + Sender", "url": None, "status": "planned"}],
        "Proposal":   [{"name": "Submission Q&A", "url": None, "status": "planned"}],
    },
    "Deck Builder": {
        "Renewal Preparation": [{"name": "ISM Deck Builder", "url": None, "status": "planned"}],
        "RSM":      [{"name": "RSM Deck Builder", "url": "/skills/rsm", "status": "live"}],
        "Proposal": [{"name": "Proposal Builder", "url": None, "status": "planned"}],
    },
    "Document Review": {
        "Renewal Preparation": [
            {"name": "ECP Reviewer", "url": None, "status": "planned"},
            {"name": "Home State Assigner", "url": None, "status": "planned"},
        ],
        "Submission":    [{"name": "Loss Run Request", "url": "/skills/loss-run", "status": "live"}],
        "Proposal":      [{"name": "Quote Reviewer", "url": None, "status": "planned"}],
        "Bind":          [{"name": "Binder Reviewer", "url": None, "status": "planned"}],
        "Post Binding":  [{"name": "Policy Checker", "url": None, "status": "planned"}],
    },
    "Document Generator": {
        "Renewal Preparation": [{"name": "Renewal Kickoff", "url": None, "status": "planned"}],
        "RSM":          [{"name": "PSL Builder", "url": None, "status": "planned"}],
        "Submission":   [{"name": "Auto ID Updates", "url": None, "status": "planned"}],
        "Proposal":     [{"name": "T&D Generator", "url": None, "status": "planned"}],
        "Bind":         [{"name": "Bind Order", "url": None, "status": "planned"}],
        "Post Binding": [{"name": "PTL + PG + SOI Generator", "url": None, "status": "planned"}],
    },
    "System Updates": {
        "Renewal Preparation": [
            {"name": "Header Updates", "url": None, "status": "planned"},
            {"name": "Team Confirmer", "url": None, "status": "planned"},
        ],
        "Proposal":     [{"name": "Header Updates", "url": None, "status": "planned"}],
        "Bind":         [{"name": "Status + Certificate System Updater", "url": None, "status": "planned"}],
        "Post Binding": [{"name": "Document Checker", "url": None, "status": "planned"}],
    },
    "Meeting Scheduler": {
        "Renewal Preparation": [
            {"name": "Renewal Kickoff Email", "url": None, "status": "planned"},
            {"name": "ISM Scheduler", "url": None, "status": "planned"},
        ],
        "Post Binding": [{"name": "Post-Bind Scheduler", "url": None, "status": "planned"}],
    },
    "Invoicing Assistant": {
        "Proposal":     [{"name": "Premium Financing", "url": None, "status": "planned"}],
        "Invoice":      [{"name": "Invoicing Assistant", "url": "/skills/invoicing-assistant", "status": "live"}],
        "Post Binding": [{"name": "Policy Tracker", "url": None, "status": "planned"}],
    },
}

# ── JSON helpers ──────────────────────────────────────────────────────────────

def _load_json(path: Path, default=None):
    if default is None:
        default = {}
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
    except Exception:
        return default


def load_completions() -> Dict:
    return _load_json(_COMPLETIONS, {})


def save_completions(data: Dict) -> None:
    _COMPLETIONS.parent.mkdir(parents=True, exist_ok=True)
    _COMPLETIONS.write_text(json.dumps(data, indent=2), encoding="utf-8")


def toggle_completion(client_name: str, stage: str, task_num: str, checked: bool) -> None:
    data = load_completions()
    data.setdefault(client_name, {}).setdefault(stage, {})[task_num] = checked
    save_completions(data)


def load_stage_responses() -> Dict:
    return _load_json(_STAGE_RESP, {})


def save_stage_responses(data: Dict) -> None:
    _STAGE_RESP.parent.mkdir(parents=True, exist_ok=True)
    _STAGE_RESP.write_text(json.dumps(data, indent=2), encoding="utf-8")


def record_stage_response(client_name: str, selected_stage: str) -> None:
    data = load_stage_responses()
    data[client_name] = {"selected_stage": selected_stage}
    save_stage_responses(data)


def load_skill_map() -> Dict:
    return _load_json(_SKILL_MAP, {})

# ── Client config ─────────────────────────────────────────────────────────────

def load_clients() -> List[Dict]:
    cfg = _load_json(_CLIENTS, {"clients": []})
    out = []
    for c in cfg.get("clients", []):
        if not c.get("active", True):
            continue
        engs = c.get("engagements", [])
        primary = next((e for e in engs if e.get("primary") is True), None)
        if primary is None and engs:
            primary = engs[0]
        secondaries = [e for e in engs if e.get("primary") is False]
        secondary_note = ""
        if secondaries:
            parts = [e.get("label") or e.get("project_name", "") for e in secondaries]
            secondary_note = "Also: " + ", ".join(p for p in parts if p)
        out.append({
            "display_name":   c["display_name"],
            "primary":        primary or {},
            "secondary_note": secondary_note,
            "jordan_role":    c.get("jordan_role", "AR"),
            "team":           c.get("team", {}),
            "folder_name":    c.get("folder_name", ""),
        })
    return out

# ── Tasks ─────────────────────────────────────────────────────────────────────

def load_tasks() -> Dict[str, Dict]:
    result: Dict[str, Dict] = {}
    # Try primary workbook first
    if _WORKBOOK.exists():
        try:
            wb = openpyxl.load_workbook(_WORKBOOK, data_only=True)
            if "Workflow_Template" in wb.sheetnames:
                ws = wb["Workflow_Template"]
                counters: Dict[str, int] = {}
                for row in ws.iter_rows(min_row=2, values_only=True):
                    if not row[0]:
                        continue
                    stage = str(row[0]).strip()
                    if stage not in STAGES:
                        continue
                    if row[10] is not None and str(row[10]).strip().lower() == "false":
                        continue
                    window   = str(row[1]).strip() if row[1] else STAGE_WINDOWS.get(stage, "")
                    task_txt = str(row[2]).strip() if row[2] else ""
                    if not task_txt:
                        continue
                    if stage not in result:
                        result[stage] = {"window": window, "tasks": []}
                        counters[stage] = 0
                    counters[stage] += 1
                    result[stage]["tasks"].append({
                        "num":  counters[stage],
                        "text": task_txt,
                        "ae":   bool(row[4]) if row[4] is not None else False,
                        "aae":  bool(row[5]) if row[5] is not None else False,
                        "ar":   bool(row[6]) if row[6] is not None else False,
                    })
                if result:
                    return result
        except Exception:
            pass
    # Fallback: Renewal_Timeline.xlsx Tasks sheet
    if not _TASKS_XLSX.exists():
        return result
    try:
        wb2 = openpyxl.load_workbook(_TASKS_XLSX, data_only=True)
    except Exception:
        return result
    if "Tasks" not in wb2.sheetnames:
        return result
    ws2 = wb2["Tasks"]
    current_stage = None
    current_window = ""
    task_num = 0
    for row in ws2.iter_rows(values_only=True):
        if not any(row):
            continue
        cell0 = str(row[0]).strip() if row[0] else ""
        cell1 = str(row[1]).strip() if row[1] else ""
        if cell0 in STAGES:
            current_stage = cell0
            current_window = cell1
            task_num = 0
            result[current_stage] = {"window": current_window, "tasks": []}
            continue
        if cell0 == "Task":
            continue
        if current_stage and cell0:
            task_num += 1
            result[current_stage]["tasks"].append({
                "num":  task_num,
                "text": cell0.strip(),
                "ae":   bool(row[1] and str(row[1]).strip() == "X"),
                "aae":  bool(row[2] and str(row[2]).strip() == "X"),
                "ar":   bool(row[3] and str(row[3]).strip() == "X"),
            })
    return result

# ── Stage derivation ──────────────────────────────────────────────────────────

def _stage_from_dates(stage_dates: Dict) -> str:
    today = date.today()
    passed = []
    for s in STAGES:
        d_str = stage_dates.get(s, "")
        if not d_str:
            continue
        try:
            d = datetime.strptime(str(d_str).strip(), "%m/%d/%Y").date()
            if today >= d:
                passed.append(s)
        except Exception:
            continue
    if passed:
        for s in reversed(STAGES):
            if s in passed:
                return s
    return STAGES[0]


def get_current_stage(client: Dict, stage_responses: Optional[Dict] = None) -> str:
    name = client["display_name"]
    responses = stage_responses or load_stage_responses()
    selected = str(responses.get(name, {}).get("selected_stage", "")).strip()
    if selected in STAGES:
        return selected
    stage_dates = client["primary"].get("stage_dates", {})
    if stage_dates:
        return _stage_from_dates(stage_dates)
    return STAGES[0]

# ── Overview ──────────────────────────────────────────────────────────────────

_rng = random.Random(42)

_DEMO_STATUSES = ["On Track", "On Track", "On Track", "Needs Attention", "At Risk"]
_DEMO_MILESTONES = {
    "Renewal Preparation": ["Update exposure spreadsheets", "Request Loss Data", "Confirm placement specialists"],
    "RSM":     ["Draft RSM document", "Review and finalize RSM", "Conduct RSM"],
    "Submission": ["Prepare draft submission", "Finalize submission", "Send to marketplace"],
    "Proposal":   ["Review Quotes", "Prepare Cost & Coverage Comparison", "Generate T&D"],
    "Bind":       ["Obtain Bind Order", "Review Binders", "Send to Client"],
    "Invoice":    ["Finalize Premium Allocation", "Send Invoice Request"],
    "Post Binding": ["Follow-up for policies", "Review policy accuracy", "Prepare PTL"],
}


def get_overview_data() -> List[Dict]:
    clients = load_clients()
    stage_responses = load_stage_responses()
    completions = load_completions()
    tasks_all = load_tasks()
    rows = []
    for c in clients:
        name = c["display_name"]
        primary = c["primary"]
        renewal_date = primary.get("renewal_date", "")
        stage = get_current_stage(c, stage_responses)
        stage_tasks = tasks_all.get(stage, {}).get("tasks", [])
        num_tasks = len(stage_tasks)
        client_comps = completions.get(name, {}).get(stage, {})
        completed_nums = [int(k) for k, v in client_comps.items() if v]
        pct = round(len(completed_nums) / num_tasks * 100) if num_tasks else 0
        # Days to renewal
        days_to_renewal = ""
        if renewal_date:
            try:
                rd = datetime.strptime(str(renewal_date).strip(), "%m/%d/%Y").date()
                days_to_renewal = (rd - date.today()).days
            except Exception:
                pass
        # Status (demo enriched)
        status = _rng.choice(_DEMO_STATUSES)
        # Next milestone
        stage_milestones = _DEMO_MILESTONES.get(stage, ["In progress"])
        next_milestone = _rng.choice(stage_milestones)
        # Team lead
        team = c.get("team", {})
        ae = team.get("ae", {}).get("name", "") or team.get("ao_ce", {}).get("name", "")
        rows.append({
            "client_name":    name,
            "secondary_note": c["secondary_note"],
            "renewal_date":   renewal_date,
            "days_to_renewal": days_to_renewal,
            "current_stage":  stage,
            "pct_complete":   pct,
            "status":         status,
            "next_milestone": next_milestone,
            "owner":          c["jordan_role"],
            "ae":             ae,
            "manual_stage":   str(stage_responses.get(name, {}).get("selected_stage", "")).strip(),
        })

    def _sort_key(r):
        d = str(r.get("renewal_date", "")).strip()
        try:
            return datetime.strptime(d, "%m/%d/%Y").date()
        except Exception:
            return date.max

    rows.sort(key=_sort_key)
    return rows


def get_stage_detail(stage: str) -> Dict:
    clients = load_clients()
    tasks_all = load_tasks()
    completions = load_completions()
    stage_responses = load_stage_responses()
    skill_map = load_skill_map()

    stage_tasks = tasks_all.get(stage, {"window": STAGE_WINDOWS.get(stage, ""), "tasks": []})
    num_tasks = len(stage_tasks["tasks"])
    stage_skill_map = skill_map.get(stage, {})

    accounts = []
    for c in clients:
        name = c["display_name"]
        current = get_current_stage(c, stage_responses)
        if current != stage:
            continue
        primary = c["primary"]
        due = primary.get("stage_dates", {}).get(stage, "")
        days_to_due = ""
        if due:
            try:
                due_dt = datetime.strptime(str(due).strip(), "%m/%d/%Y").date()
                days_to_due = (due_dt - date.today()).days
            except Exception:
                pass
        client_comps = completions.get(name, {}).get(stage, {})
        completed_nums = [int(k) for k, v in client_comps.items() if v]
        outstanding = [t["num"] for t in stage_tasks["tasks"] if t["num"] not in completed_nums]
        pct = round(len(completed_nums) / num_tasks * 100) if num_tasks else 0
        accounts.append({
            "client_name":    name,
            "secondary_note": c["secondary_note"],
            "due_date":       due,
            "days_to_due":    days_to_due,
            "pct_complete":   pct,
            "completed_nums": sorted(completed_nums),
            "outstanding":    outstanding,
            "num_tasks":      num_tasks,
            "task_checks":    {str(t["num"]): (t["num"] in completed_nums) for t in stage_tasks["tasks"]},
        })

    accounts.sort(key=lambda a: (
        "9999/99/99" if not a["due_date"]
        else "/".join(reversed(a["due_date"].split("/")))
        if len(str(a["due_date"]).split("/")) == 3
        else str(a["due_date"])
    ))

    # Attach portal skills to each task
    tasks_with_skills = []
    for t in stage_tasks["tasks"]:
        task_url = stage_skill_map.get(str(t["num"]))
        tasks_with_skills.append({**t, "skill_url": task_url})

    return {
        "stage":    stage,
        "window":   stage_tasks["window"],
        "tasks":    tasks_with_skills,
        "accounts": accounts,
    }


def get_portal_data(portal: str) -> Dict:
    """Return all clients × stages relevant to a given portal."""
    meta = PORTAL_META.get(portal, {})
    active_stages = meta.get("stages", [])
    portal_skill_grid = PORTAL_SKILLS.get(portal, {})
    clients = load_clients()
    stage_responses = load_stage_responses()

    stage_rows = []
    for stage in active_stages:
        skills_here = portal_skill_grid.get(stage, [])
        # Clients currently in this stage
        clients_in_stage = [
            c["display_name"] for c in clients
            if get_current_stage(c, stage_responses) == stage
        ]
        stage_rows.append({
            "stage":      stage,
            "window":     STAGE_WINDOWS.get(stage, ""),
            "skills":     skills_here,
            "clients":    clients_in_stage,
        })

    return {
        "portal":       portal,
        "meta":         meta,
        "stage_rows":   stage_rows,
        "all_stages":   active_stages,
    }
