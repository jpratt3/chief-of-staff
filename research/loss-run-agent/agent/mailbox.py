"""Maildir-style mock inbox/sent, plus the simulated carrier side."""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

import config
import seed


def _next_id(folder: Path) -> int:
    folder.mkdir(parents=True, exist_ok=True)
    return len(list(folder.glob("*.json"))) + 1


def send(to: str, subject: str, body: str, policy_number: str,
         kind: str = "request", day: int = 0) -> Path:
    folder = config.MAIL_DIR / "sent"
    n = _next_id(folder)
    rec = {
        "id": n, "to": to, "from": config.BROKER_EMAIL, "subject": subject,
        "body": body, "policy_number": policy_number, "kind": kind,
        "virtual_day": day, "sent_at": datetime.now().isoformat(timespec="seconds"),
    }
    p = folder / f"{n:04d}-{kind}.json"
    p.write_text(json.dumps(rec, indent=2), encoding="utf-8")
    return p


def sent_for(policy_number: str) -> list[dict]:
    folder = config.MAIL_DIR / "sent"
    if not folder.exists():
        return []
    out = []
    for f in sorted(folder.glob("*.json")):
        rec = json.loads(f.read_text())
        if rec["policy_number"] == policy_number:
            out.append(rec)
    return out


def deliver_reply(policy_number: str, carrier: str, attachments: list[str],
                  day: int, wrong_policy: bool = False) -> Path:
    """Simulated carrier reply landing in the inbox."""
    folder = config.MAIL_DIR / "inbox"
    n = _next_id(folder)
    att_dir = folder / f"{n:04d}-attachments"
    att_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    for a in attachments:
        src = config.SEED_PDF_DIR / a
        if src.exists():
            shutil.copy(src, att_dir / a)
            saved.append(a)
    rec = {
        "id": n,
        "from": config.CARRIERS[carrier].get("email", "noreply@example.com"),
        "to": config.BROKER_EMAIL,
        "subject": f"RE: Loss run request · Meridian Logistics · {policy_number}",
        "body": "Attached, as requested.",
        "policy_number": "XX-000-0000" if wrong_policy else policy_number,
        "stated_policy": "XX-000-0000" if wrong_policy else policy_number,
        "carrier": carrier,
        "attachments": saved,
        "attachment_dir": str(att_dir),
        "virtual_day": day,
        "received_at": datetime.now().isoformat(timespec="seconds"),
    }
    p = folder / f"{n:04d}-reply.json"
    p.write_text(json.dumps(rec, indent=2), encoding="utf-8")
    return p


def inbox_for(policy_number: str) -> list[dict]:
    folder = config.MAIL_DIR / "inbox"
    if not folder.exists():
        return []
    return [json.loads(f.read_text()) for f in sorted(folder.glob("*.json"))
            if json.loads(f.read_text()).get("policy_number") == policy_number]


def reset() -> None:
    for sub in ("inbox", "sent"):
        d = config.MAIL_DIR / sub
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True, exist_ok=True)
