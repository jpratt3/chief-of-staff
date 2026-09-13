"""Exposure Request — Renewal Pipeline · Renewal Preparation #10 + #11"""
from __future__ import annotations
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

_ROOT = Path(__file__).parent.parent.parent.parent
_CLIENTS = _ROOT / "config" / "clients.json"

@dataclass
class ExposureResult:
    success: bool
    client_name: str = ""
    placement_team: List[dict] = field(default_factory=list)
    lines_detected: List[str] = field(default_factory=list)
    checklist_items: List[str] = field(default_factory=list)
    email_draft: str = ""
    error: str = ""

def _load_client(client_name):
    try:
        cfg = json.loads(_CLIENTS.read_text(encoding="utf-8"))
        for c in cfg.get("clients", []):
            if c["display_name"].lower() == client_name.lower():
                return c
    except Exception:
        pass
    return {}

def build_exposure_request(client_name: str, lines: List[str]) -> ExposureResult:
    client = _load_client(client_name)
    if not client:
        return ExposureResult(success=False, error=f"Client not found.")
    team = client.get("team", {})
    placement = []
    for role in ("placement_gl","placement_prop","placement_cyber","placement_wc"):
        p = team.get(role, {})
        if p.get("name"):
            placement.append({"role": role.replace("placement_","").upper(), **p})
    checklist = [f"Updated {l} application / exposure schedule" for l in lines] + [
        "Subjectivities list from prior year",
        "Any changes in operations, acquisitions, or headcount",
        "Prior year loss runs (if not already requested)",
    ]
    to_str = "; ".join(p.get("email","") for p in placement if p.get("email")) or "[placement emails]"
    draft = f"To: {to_str}\nSubject: {client_name} — Renewal Exposure Request\n\nHi team,\n\nAs we kick off the {client_name} renewal, please see the exposure checklist below.\n\nEXPOSURE CHECKLIST\n" + "".join(f"  • {i}\n" for i in checklist) + "\nPlease return updated schedules at your earliest convenience.\n\nThanks,\nJordan\n"
    return ExposureResult(success=True, client_name=client_name, placement_team=placement, lines_detected=lines, checklist_items=checklist, email_draft=draft)

STANDARD_LINES = ["General Liability","Property","Auto","Workers Compensation","Umbrella / Excess","Cyber","D&O / Management Liability","Crime","EPLI"]
