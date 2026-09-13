"""Mock Applied Epic: expiration report, account records, attachments, activities."""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

import config
import seed

STATE_PATH = config.ROOT / "epic_state.json"

app = FastAPI(title="Mock Applied Epic")


def _load() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {"attachments": {}, "activities": {}}


def _save(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2))


def _policies_for(account_id: str) -> list[dict]:
    if account_id != "meridian":
        return []
    out = []
    for p in seed.MERIDIAN_POLICIES:
        out.append({
            "policy_number": p.policy_number,
            "carrier": p.carrier,
            "line": p.line,
            "channel": config.CARRIERS[p.carrier]["channel"],
            "effective": seed.PERIOD_START.isoformat(),
            "expiration": "2026-03-01",
        })
    return out


@app.get("/expirations")
def expirations(days: int = 90):
    """Accounts renewing within `days`, with policy detail."""
    rows = []
    for a in seed.ACCOUNTS:
        rows.append({
            "id": a["id"],
            "account": a["name"],
            "expires": a["expires"].isoformat(),
            "servicing_team": "Commercial Lines - Team 2",
            "policies": _policies_for(a["id"]),
            "policy_count": len(_policies_for(a["id"])),
        })
    return {"as_of": date.today().isoformat(), "days": days, "accounts": rows}


@app.get("/accounts/{account_id}")
def get_account(account_id: str):
    acct = next((a for a in seed.ACCOUNTS if a["id"] == account_id), None)
    if not acct:
        raise HTTPException(404, "no such account")
    state = _load()
    return {
        "id": acct["id"],
        "account": acct["name"],
        "expires": acct["expires"].isoformat(),
        "policies": _policies_for(account_id),
        "attachments": state["attachments"].get(account_id, []),
        "activities": state["activities"].get(account_id, []),
    }


class Attachment(BaseModel):
    name: str
    added_by: str = "LossRunAgent"
    size_kb: int = 0
    note: str = ""


@app.post("/accounts/{account_id}/attachments")
def add_attachment(account_id: str, att: Attachment):
    state = _load()
    rec = att.model_dump()
    rec["added_at"] = datetime.now().isoformat(timespec="seconds")
    state["attachments"].setdefault(account_id, []).append(rec)
    _save(state)
    return rec


class Activity(BaseModel):
    text: str
    added_by: str = "LossRunAgent"


@app.post("/accounts/{account_id}/activities")
def add_activity(account_id: str, act: Activity):
    state = _load()
    rec = act.model_dump()
    rec["added_at"] = datetime.now().isoformat(timespec="seconds")
    state["activities"].setdefault(account_id, []).append(rec)
    _save(state)
    return rec


@app.post("/reset")
def reset():
    _save({"attachments": {}, "activities": {}})
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    # Pre-seed a human-added attachment so the account has prior history.
    st = _load()
    if not st["attachments"].get("meridian"):
        st["attachments"]["meridian"] = [{
            "name": "Meridian_Application_Signed.pdf",
            "added_by": "J. Reyes",
            "size_kb": 412,
            "note": "",
            "added_at": "2026-02-12T09:14:00",
        }]
        _save(st)
    uvicorn.run(app, host="127.0.0.1", port=config.EPIC_PORT, log_level="warning")
