"""Renewal Kickoff — Document Generator · Renewal Preparation #5"""
from __future__ import annotations
import json
from dataclasses import dataclass, field
from pathlib import Path

_ROOT = Path(__file__).parent.parent.parent.parent
_CLIENTS = _ROOT / "config" / "clients.json"

@dataclass
class KickoffResult:
    success: bool
    client_name: str = ""
    renewal_date: str = ""
    email_draft: str = ""
    team_members: list = field(default_factory=list)
    error: str = ""

def build_kickoff_email(client_name: str) -> KickoffResult:
    try:
        cfg = json.loads(_CLIENTS.read_text(encoding="utf-8"))
    except Exception as e:
        return KickoffResult(success=False, error=str(e))
    client = next((c for c in cfg.get("clients",[]) if c["display_name"].lower()==client_name.lower()), None)
    if not client:
        return KickoffResult(success=False, error=f"Client not found.")
    team = client.get("team", {})
    engs = client.get("engagements", [])
    primary = next((e for e in engs if e.get("primary")), engs[0] if engs else {})
    renewal_date = primary.get("renewal_date", "[Renewal Date]")
    members = []
    for role, label in [("ae","AE"),("aae","AAE"),("ar","AR")]:
        p = team.get(role,{})
        if p.get("name"): members.append({"role":label,"name":p["name"],"email":p.get("email","")})
    to_str = "; ".join(m["email"] for m in members if m["email"]) or "[team emails]"
    team_lines = "\n".join(f"  • {m['role']}: {m['name']}" for m in members)
    draft = f"To: {to_str}\nSubject: {client_name} — Renewal Kickoff\n\nHi team,\n\nKicking off the {client_name} renewal. Renewal date: {renewal_date}.\n\nTEAM ASSIGNMENTS\n{team_lines}\n\nNEXT STEPS\n  1. Confirm team assignments\n  2. AE/AAE: Schedule the Internal Strategy Meeting (task #4)\n  3. AAE: Send the client exposure update request (task #6)\n  4. AR: Request loss runs from incumbent carriers (task #5)\n\nJordan\n"
    return KickoffResult(success=True, client_name=client_name, renewal_date=renewal_date, email_draft=draft, team_members=members)
