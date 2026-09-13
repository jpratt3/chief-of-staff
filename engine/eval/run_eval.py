"""
Accuracy harness for the loss-run extraction engine.

Grades five cells per binder — policy number, carrier, effective date,
coverage, insured — against hand-checked ground truth in truth/*.json, and
prints per-field accuracy plus every miss.

Usage (from the repo root):
    .venv/Scripts/python.exe engine/eval/run_eval.py
    .venv/Scripts/python.exe engine/eval/run_eval.py set3

Ground truth format — each value is a list of acceptable answers, because some
binders genuinely carry more than one defensible one (a package policy naming
four policy numbers, a blended D&O/EPL/Fiduciary form):

    "<filename>": {
      "insured": ["Northwind Foods"],       # matched against insured_resolved
      "policy":  ["83 FA 0235529-25"],    # compared ignoring case/punctuation
      "carrier": ["Hartford"],            # matched against carrier_group
      "eff":     ["08/31/2025"],          # normalised to MM/DD/YYYY first
      "cov":     ["Crime"]                # exact canonical category
    }

Add a file here whenever a binder format breaks, so the fix stays fixed.

The real truth sets (set1/set2/set3.json — 80 binders, 400 graded cells) and the
binders they grade are gitignored: the filenames and insured names are client
data. `truth/example.json` carries the same schema with fictional binders. To
run this against your own documents, drop them in `binders/` and write a
`set*.json` beside that example.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

_HERE = Path(__file__).parent
_ROOT = _HERE.parent.parent
sys.path.insert(0, str(_ROOT))

from engine import loss_run as service   # noqa: E402

BINDERS = _ROOT / "binders"
FIELDS = ["policy", "carrier", "eff", "cov", "insured"]

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"])}


def norm_date(value: str) -> str:
    value = (value or "").strip()
    m = re.match(r"^(\d{1,2})[/\-](\d{1,2})[/\-](\d{2,4})$", value)
    if m:
        month, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return f"{month:02d}/{day:02d}/{year + 2000 if year < 100 else year}"
    m = re.match(r"^([A-Za-z]+)\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})$", value)
    if m and m.group(1)[:3].lower() in _MONTHS:
        return f"{_MONTHS[m.group(1)[:3].lower()]:02d}/{int(m.group(2)):02d}/{m.group(3)}"
    m = re.match(r"^(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+),?\s+(\d{4})$", value)
    if m and m.group(2)[:3].lower() in _MONTHS:
        return f"{_MONTHS[m.group(2)[:3].lower()]:02d}/{int(m.group(1)):02d}/{m.group(3)}"
    return value


def norm_id(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


def norm_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def grade(row, expected: dict) -> dict:
    carrier = row.carrier_group or row.carrier_raw
    insured = norm_text(row.insured_resolved or row.insured_raw)
    return {
        "policy":  norm_id(row.policy_number) in {norm_id(v) for v in expected["policy"]},
        "carrier": any(norm_text(v) and norm_text(v) in norm_text(carrier)
                       for v in expected["carrier"]),
        "eff":     norm_date(row.effective_date) in {norm_date(v) for v in expected["eff"]},
        "cov":     (row.coverage_category in expected["cov"]
                    if expected["cov"] != [""] else not row.coverage_category),
        "insured": any(norm_text(v) and norm_text(v) in insured for v in expected["insured"]),
    }


def _load_truth(truth_path: Path) -> dict:
    """
    Read a truth set, dropping documentation keys.

    Keys beginning with "_" are prose, not binders. truth/example.json uses one
    to carry the format spec, and a real set is free to do the same — a note
    about why a given binder is here outlives the memory of adding it.
    """
    raw = json.loads(truth_path.read_text(encoding="utf-8"))
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def run(truth_path: Path) -> float:
    truth = _load_truth(truth_path)
    print(f"\n===== {truth_path.name} ({len(truth)} files) =====")

    started = time.time()
    per_field = {f: [0, 0] for f in FIELDS}
    misses = []

    for name, expected in truth.items():
        path = BINDERS / name
        if not path.exists():
            print(f"  MISSING FILE: {name}")
            continue
        row = service.process_batch([(path.read_bytes(), name)])[0]
        result = grade(row, expected)
        got = {"policy": row.policy_number,
               "carrier": row.carrier_group or row.carrier_raw,
               "eff": row.effective_date,
               "cov": row.coverage_category,
               "insured": row.insured_resolved or row.insured_raw}
        for field in FIELDS:
            per_field[field][1] += 1
            per_field[field][0] += bool(result[field])
        bad = [f for f in FIELDS if not result[f]]
        if bad:
            misses.append((name, {f: (got[f], expected[f]) for f in bad}))

    for name, fields in misses:
        print(f"\n{name}")
        for field, (actual, wanted) in fields.items():
            print(f"   {field:8s} got={actual!r:40s} want={wanted}")

    print("\n--- per field ---")
    for field in FIELDS:
        correct, total = per_field[field]
        print(f"  {field:8s} {correct}/{total}  {100 * correct / max(total, 1):.1f}%")

    correct = sum(v[0] for v in per_field.values())
    total = sum(v[1] for v in per_field.values())
    print(f"\nTOTAL {correct}/{total} = {100 * correct / max(total, 1):.1f}%"
          f"   ({time.time() - started:.1f}s)")
    return correct / max(total, 1)


def main(argv: list[str]) -> int:
    wanted = set(argv)
    paths = sorted((_HERE / "truth").glob("*.json"))
    # example.json documents the format. It names binders that deliberately do
    # not exist, so grading it would drag the denominator up with guaranteed
    # misses. Asking for it by name still works.
    paths = [p for p in paths if p.stem != "example" or "example" in wanted]
    paths = [p for p in paths if not wanted or p.stem in wanted]
    if not paths:
        print(f"No truth sets matched {argv}")
        return 1

    overall_correct = overall_total = 0
    for path in paths:
        truth = _load_truth(path)
        overall_total += len(truth) * len(FIELDS)
        overall_correct += round(run(path) * len(truth) * len(FIELDS))

    if len(paths) > 1:
        print(f"\n{'=' * 40}")
        print(f"OVERALL {overall_correct}/{overall_total} = "
              f"{100 * overall_correct / overall_total:.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
