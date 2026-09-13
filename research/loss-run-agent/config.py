"""Central configuration: ports, paths, carrier registry."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent

RUNS_DIR = ROOT / "runs"
MAIL_DIR = ROOT / "mock_mail"
SHAREPOINT_DIR = ROOT / "mock_sharepoint"
SEED_PDF_DIR = ROOT / "seed_pdfs"
VAULT_PATH = ROOT / "vault.json"

EPIC_PORT = 8100
WEB_PORT = 8000

# channel: "portal" -> has a mock portal; "email" -> chased by email
CARRIERS = {
    "Northbridge": {
        "channel": "portal",
        "port": 8201,
        "slug": "northbridge",
        "host": "portal.northbridge.com",
        "quirk": "totp",
        "file_prefix": "NBM",
    },
    "Harborstone": {
        "channel": "portal",
        "port": 8202,
        "slug": "harborstone",
        "host": "portal.harborstone.com",
        "quirk": "two_documents",
        "file_prefix": "HBS",
    },
    "Bluehaven": {
        "channel": "portal",
        "port": 8203,
        "slug": "bluehaven",
        "host": "portal.bluehaven.com",
        "quirk": "security_question",  # vault has no answer -> Input required
        "file_prefix": "BLH",
    },
    "Redstone": {
        "channel": "portal",
        "port": 8204,
        "slug": "redstone",
        "host": "portal.redstone.com",
        "quirk": "dash_rejection",
        "file_prefix": "RDS",
    },
    "Summit": {
        "channel": "email",
        "email": "lossruns@summit.example.com",
        "replies_after_followups": 1,
        "file_prefix": "SMT",
    },
    "Ironwood": {
        "channel": "email",
        "email": "lossruns@ironwood.com",
        "replies_after_followups": 2,
        "file_prefix": "IRN",
    },
}

BROKER_NAME = "Meridian Risk Services"
BROKER_EMAIL = "lossruns@meridianrisk.example.com"

# Chase cadence in virtual days: "FOLLOW-UP 2 ... Day 6"
FOLLOWUP_1_DAY = 3
FOLLOWUP_2_DAY = 6
ESCALATE_DAY = 14

LARGE_LOSS_THRESHOLD = 25_000

# Work activates this many days before expiration
LEAD_DAYS = 90


def base_url(carrier: str) -> str:
    return f"http://127.0.0.1:{CARRIERS[carrier]['port']}"


def epic_url() -> str:
    return f"http://127.0.0.1:{EPIC_PORT}"


def use_claude() -> bool:
    """Real Claude planner only when a key is present and not explicitly disabled."""
    if os.environ.get("LOSSRUN_PLANNER") == "scripted":
        return False
    if os.environ.get("LOSSRUN_PLANNER") == "claude":
        return True
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def ensure_dirs() -> None:
    for d in (RUNS_DIR, MAIL_DIR / "inbox", MAIL_DIR / "sent", SHAREPOINT_DIR, SEED_PDF_DIR):
        d.mkdir(parents=True, exist_ok=True)
