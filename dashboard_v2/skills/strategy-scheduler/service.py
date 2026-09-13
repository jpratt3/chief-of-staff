"""Internal Strategy Meeting (ISM) Scheduler — Meeting Scheduler · Renewal Preparation #6"""
from __future__ import annotations
import json
from dataclasses import dataclass, field
from datetime import date, timedelta, datetime
from pathlib import Path
from typing import List

_ROOT = Path(__file__).parent.parent.parent.parent
_CLIENTS = _ROOT / "config" / "clients.json"

@dataclass
class SchedulerResult:
    success: bool
    client_name: str = ""
    suggested_slots: List[str] = field(default_factory=list)
    invite_draft: str = ""
    attendees: List[dict] = field(default_factory=list)
    error: str = ""

def build_strategy_invite(client_name: str, preferred_dates: List[str] = None) -> SchedulerResult:
    try:
        cfg = json.loads(_CLIENTS.read_text(encoding="utf-8"))
    except Exception as e:
        return SchedulerResult(success=False, error=str(e))
    client = next((c for c in cfg.get("clients",[]) if c["display_name"].lower()==client_name.lower()), None)
    if not client:
        return SchedulerResult(success=False, error="Client not found.")
    team = client.get("team",{})
    engs = client.get("engagements",[])
    primary = next((e for e in engs if e.get("primary")), engs[0] if engs else {})
    renewal_date = primary.get("renewal_date","")
    attendees = []
    for role in ("ae","aae","ar"):
        p = team.get(role,{})
        if p.get("name"): attendees.append({"role":role.upper(),"name":p["name"],"email":p.get("email","")})
    slots = preferred_dates or []
    if not slots and renewal_date:
        try:
            rd = datetime.strptime(renewal_date.strip(), "%m/%d/%Y").date()
            base = rd - timedelta(days=90)
            for i in range(3):
                d = base + timedelta(weeks=i)
                while d.weekday() >= 5: d += timedelta(days=1)
                slots.append(d.strftime("%A, %B %d %Y — 10:00 AM"))
        except Exception:
            pass
    if not slots: slots = ["[Propose 3 available timeslots]"]
    to_str = "; ".join(a["email"] for a in attendees if a["email"]) or "[team emails]"
    slots_str = "\n".join(f"  Option {i+1}: {s}" for i,s in enumerate(slots))
    draft = f"To: {to_str}\nSubject: {client_name} ISM — Scheduling\n\nHi team,\n\nTime to schedule the Internal Strategy Meeting (ISM) for {client_name}. Please confirm availability:\n\n{slots_str}\n\nAgenda: renewal strategy, prior year loss summary, submission assignments, target dates.\n\nJordan\n"
    return SchedulerResult(success=True, client_name=client_name, suggested_slots=slots, invite_draft=draft, attendees=attendees)
