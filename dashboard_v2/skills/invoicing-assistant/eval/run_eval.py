"""
Accuracy harness for the Invoicing Assistant's money extraction.

Grades four numbers per binder — premium, commission rate, taxes/fees/surcharges
and the total billed — against hand-checked ground truth in truth/*.json.

Usage (from the repo root):
    .venv/Scripts/python.exe dashboard_v2/skills/invoicing-assistant/eval/run_eval.py
    .venv/Scripts/python.exe dashboard_v2/skills/invoicing-assistant/eval/run_eval.py set1

Ground truth format — null means "the binder does not state one", which the
engine is expected to reproduce rather than invent:

    "<filename>": {
      "premium":        84000,
      "commission_pct": 0,
      "charges_total":  0,
      "total":          84000
    }

Add a binder here whenever a format breaks, so the fix stays fixed.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SKILL = _HERE.parent
_ROOT = _SKILL.parents[2]

sys.path.insert(0, str(_SKILL))
spec = importlib.util.spec_from_file_location("invoicing_money", _SKILL / "money.py")
money = importlib.util.module_from_spec(spec)
# Registered before execution: @dataclass resolves annotations through
# sys.modules, and a module that is not there yet blows up on import.
sys.modules["invoicing_money"] = money
spec.loader.exec_module(money)

BINDERS = _ROOT / "binders"
FIELDS = ["premium", "commission_pct", "charges_total", "total"]
TOLERANCE = 0.5


def pages_for(path: Path) -> list[dict]:
    """The same pages the skill reads: billing-scored, layout-preserving text."""
    import pdfplumber
    with pdfplumber.open(str(path)) as pdf:
        cheap = []
        for page in pdf.pages[:20]:
            try:
                cheap.append(page.extract_text(layout=True) or "")
            except Exception:
                cheap.append(page.extract_text() or "")
    pages = [{"index": i, "text": t} for i, t in enumerate(cheap) if t.strip()]
    return money.rank_pages(pages)


def close(got, want) -> bool:
    if want is None:
        return got is None
    if got is None:
        return False
    return abs(float(got) - float(want)) <= TOLERANCE


def run(truth_path: Path) -> tuple[int, int]:
    raw = json.loads(truth_path.read_text(encoding="utf-8"))
    # Keys beginning with "_" are prose, not binders — see truth/example.json.
    truth = {k: v for k, v in raw.items() if not k.startswith("_")}
    print(f"\n===== {truth_path.name} ({len(truth)} files) =====")

    started = time.time()
    per_field = {f: [0, 0] for f in FIELDS}
    misses = []

    for name, expected in truth.items():
        path = BINDERS / name
        if not path.exists():
            print(f"  MISSING FILE: {name}")
            continue
        result = money.extract(pages_for(path), rank=False)
        bad = {}
        for field in FIELDS:
            per_field[field][1] += 1
            if close(result.get(field), expected.get(field)):
                per_field[field][0] += 1
            else:
                bad[field] = (result.get(field), expected.get(field))
        if bad:
            misses.append((name, bad, result.get("reconciliation", "")))

    for name, fields, reconciliation in misses:
        print(f"\n{name}   [{reconciliation}]")
        for field, (got, want) in fields.items():
            print(f"   {field:15s} got={got!r:>14}  want={want!r}")

    print("\n--- per field ---")
    for field in FIELDS:
        correct, total = per_field[field]
        print(f"  {field:15s} {correct}/{total}  {100 * correct / max(total, 1):.1f}%")

    correct = sum(v[0] for v in per_field.values())
    total = sum(v[1] for v in per_field.values())
    print(f"\n{truth_path.stem}: {correct}/{total} = {100 * correct / max(total, 1):.1f}%"
          f"   ({time.time() - started:.1f}s)")
    return correct, total


def main(argv: list[str]) -> int:
    wanted = set(argv)
    paths = sorted((_HERE / "truth").glob("*.json"))
    # example.json documents the format and names binders that deliberately do
    # not exist. Asking for it by name still works.
    paths = [p for p in paths if p.stem != "example" or "example" in wanted]
    paths = [p for p in paths if not wanted or p.stem in wanted]
    if not paths:
        print(f"No truth sets matched {argv}")
        return 1

    correct = total = 0
    for path in paths:
        c, t = run(path)
        correct += c
        total += t

    if len(paths) > 1:
        print(f"\nOVERALL {correct}/{total} = {100 * correct / max(total, 1):.1f}%")
    return 0 if correct == total else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
