"""
dashboard_v2/skills/rsm/service.py

Thin adapter onto the shared RSM engine.

Deck assembly lives in `engine/rsm.py` — cross-presentation slide copy at the
ZIP level, because python-pptx has no correct one. This module re-exports only
what the Blueprint calls.
"""
from __future__ import annotations

from engine import rsm as _engine

build_rsm = _engine.build_rsm
load_market_db = _engine.load_market_db


def market_db_by_category() -> dict[str, list]:
    """Market-update slides grouped by category, for the picker."""
    categories: dict[str, list] = {}
    for entry in load_market_db():
        categories.setdefault(entry.get("category", "Other"), []).append(entry)
    return categories


def safe_filename(client: str, year: str) -> str:
    """RSM_<client>_<year>.pptx, with anything path-hostile flattened."""
    safe = "".join(c if c.isalnum() or c in "._- " else "_" for c in client)
    return f"RSM_{safe.strip().replace(' ', '_')}_{year.replace('/', '-')}.pptx"
