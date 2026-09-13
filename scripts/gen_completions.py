"""Generate data/completions.json for the sample book.

Progress is derived, not random, so the dashboard reads as a coherent snapshot:

  * every stage BEFORE a client's current stage is fully complete
  * the current stage is a contiguous prefix 1..k, where k tracks how far
    through the stage window today actually falls
  * stages AFTER the current one are untouched

The prefix rule is what keeps it sensible: a task is never checked while an
earlier task in the same stage is unchecked, and a stage is never partially
done while a later stage has progress.

Run from the repo root:  .venv/Scripts/python.exe scripts/gen_completions.py
"""
from __future__ import annotations

import json
import pathlib
import sys
from datetime import date, datetime

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "dashboard_v2"))

from data_loader import (  # noqa: E402
    STAGES, get_current_stage, load_clients, load_stage_responses, load_tasks,
)

OUT = ROOT / "data" / "completions.json"
# Post Binding has no following stage date; treat it as a 60-day tail.
TAIL_DAYS = 60


def parse(d: str):
    try:
        return datetime.strptime(str(d).strip(), "%m/%d/%Y").date()
    except Exception:
        return None


def prefix_for(stage: str, stage_dates: dict, n_tasks: int, today: date) -> int:
    """How many tasks of `stage` are done, as a contiguous prefix."""
    start = parse(stage_dates.get(stage, ""))
    if start is None or n_tasks == 0:
        return 0
    if today < start:
        return 0                       # stage not reached yet

    idx = STAGES.index(stage)
    end = None
    if idx + 1 < len(STAGES):
        end = parse(stage_dates.get(STAGES[idx + 1], ""))
    if end is None or end <= start:
        end = start.fromordinal(start.toordinal() + TAIL_DAYS)

    span = (end - start).days or 1
    frac = (today - start).days / span
    frac = min(max(frac, 0.0), 1.0)

    k = round(frac * n_tasks)
    # Always show at least one done and at least one outstanding, so a stage in
    # progress never reads as untouched or as finished-but-not-advanced.
    return min(max(k, 1), n_tasks - 1)


def main() -> None:
    today = date.today()
    clients = load_clients()
    responses = load_stage_responses()
    tasks = load_tasks()
    counts = {s: len(tasks.get(s, {}).get("tasks", [])) for s in STAGES}

    out: dict[str, dict] = {}
    rows = []

    for c in clients:
        name = c["display_name"]
        current = get_current_stage(c, responses)
        stage_dates = c["primary"].get("stage_dates", {})
        cur_idx = STAGES.index(current)

        per_stage: dict[str, dict] = {}

        # Everything before the current stage is finished.
        for s in STAGES[:cur_idx]:
            if counts[s]:
                per_stage[s] = {str(i): True for i in range(1, counts[s] + 1)}

        k = prefix_for(current, stage_dates, counts[current], today)
        if k:
            per_stage[current] = {str(i): True for i in range(1, k + 1)}

        if per_stage:
            out[name] = per_stage
        rows.append((name, current, k, counts[current], cur_idx))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")

    rows.sort(key=lambda r: (r[4], r[0]))
    print(f"{'client':<24} {'stage':<20} {'current':>9}   prior stages")
    for name, stage, k, n, idx in rows:
        pct = round(k / n * 100) if n else 0
        print(f"  {name:<22} {stage:<20} {k}/{n} ({pct:>3}%)   {idx} complete")
    print(f"\nwrote {OUT.relative_to(ROOT)} for {len(out)} clients")


if __name__ == "__main__":
    main()
