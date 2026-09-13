"""
dashboard_v2/skills/loss-run/service.py

Thin adapter onto the shared extraction engine.

The extraction logic lives in `engine/loss_run.py` — pure Python, no Flask
imports, carrying its own accuracy harness in `engine/eval/`. This module only
reshapes the engine's dataclasses into the dicts the v2 templates expect.

Nothing here should grow extraction logic. A rule added here is a rule the eval
does not measure.
"""
from __future__ import annotations

from engine import loss_run as _engine

process_batch = _engine.process_batch
build_draft_batch = _engine.build_draft_batch

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".xls", ".csv"}


def extract_rows(files: list[tuple[bytes, str]], full_scan: bool = False) -> list[dict]:
    """Extract one row per uploaded file. Never raises — partial rows carry
    an extract_note the UI surfaces instead."""
    return [row.to_dict() for row in process_batch(files, full_scan=full_scan)]


def build_drafts(rows: list[dict], requestor_name: str,
                 requestor_email: str, years: int):
    """Group confirmed rows by recipient and return one draft per recipient."""
    return build_draft_batch(
        confirmed_rows=rows,
        requestor_name=requestor_name,
        requestor_email=requestor_email,
        years=years,
    )
