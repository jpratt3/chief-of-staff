"""Team Confirmer — System Updates · Renewal Preparation #8"""
from __future__ import annotations
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

_ROOT = Path(__file__).parent.parent.parent.parent
_CLIENTS = _ROOT / "config" / "clients.json"

@dataclass
class ConfirmResult:
    success: bool
    client_name: str = ""
    emails: List[dict] = field(default_factory=list)
    error: str = ""

def build_team_confirmation(client_name: str) -> ConfirmResult:
    try:
        cfg = json.loads(_CLIENTS.read_text(encoding="utf-8"))
    except Exception as e:
        return ConfirmResult(success=False, error=str(e))
    client = next((c for c in cfg.get("clients",[]) if c["display_name"].lower()==client_name.lower()), None)
    if not client:
        return ConfirmResult(success=False, error="Client not found.")
    team = client.get("team",{})
    engs = client.get("engagements",[])
    primary = next((e for e in engs if e.get("primary")), engs[0] if engs else {})
    renewal_date = primary.get("renewal_date","[Renewal Date]")
    emails = []
    for role in ("placement_gl","placement_prop","placement_cyber","placement_wc","placement_exec"):
        p = team.get(role,{})
        if not p.get("name"): continue
        line = role.replace("placement_","").upper()
        first = p["name"].split()[0]
        draft = f"To: {p.get('email', '[email]')}\nSubject: {client_name} — {line} Renewal Confirmation\n\nHi {first},\n\nConfirming you as {line} placement contact for {client_name} (renewal: {renewal_date}).\n\nPlease reply to confirm or advise on changes.\n\nThanks,\nJordan\n"
        emails.append({"to":p.get("email",""),"name":p["name"],"role":line,"draft":draft})
    csc = team.get("client_service",{})
    if csc.get("name"):
        first = csc["name"].split()[0]
        draft = f"To: {csc.get('email','[email]')}\nSubject: {client_name} — Client Service Renewal Confirmation\n\nHi {first},\n\nConfirming your involvement in the {client_name} renewal (renewal: {renewal_date}).\n\nJordan\n"
        emails.append({"to":csc.get("email",""),"name":csc["name"],"role":"Client Service","draft":draft})
    if not emails:
        return ConfirmResult(success=False, error="No placement or client service contacts found.")
    return ConfirmResult(success=True, client_name=client_name, emails=emails)
