"""Internal Strategy Meeting (ISM) Deck Builder — Deck Builder · Renewal Preparation #7"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import tempfile, shutil, zipfile, re

@dataclass
class DeckResult:
    success: bool
    filename: str = ""
    slide_count: int = 0
    output_path: str = ""
    error: str = ""

def build_strategy_deck(source_path: str, client_name: str, new_year: str, old_year: str = "") -> DeckResult:
    src = Path(source_path)
    if not src.exists():
        return DeckResult(success=False, error="Source file not found.")
    if src.suffix.lower() != ".pptx":
        return DeckResult(success=False, error="Must be a .pptx file.")
    tmp = tempfile.mktemp(suffix=".pptx")
    shutil.copy2(str(src), tmp)
    year_old = old_year or (str(int(new_year)-1) if new_year.isdigit() else "")
    out_path = tmp.replace(".pptx", f"_ISM_{client_name.replace(' ','_')}_{new_year}.pptx")
    with zipfile.ZipFile(tmp, "r") as zin:
        names = zin.namelist()
        slide_count = sum(1 for n in names if re.match(r"ppt/slides/slide[0-9]+\.xml$", n))
        content_map = {}
        for name in names:
            data = zin.read(name)
            if (name.endswith(".xml") or name.endswith(".rels")) and year_old and new_year:
                try:
                    text = data.decode("utf-8").replace(year_old, new_year)
                    content_map[name] = text.encode("utf-8")
                except Exception:
                    content_map[name] = data
            else:
                content_map[name] = data
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, data in content_map.items():
            zout.writestr(name, data)
    Path(tmp).unlink(missing_ok=True)
    return DeckResult(success=True, filename=Path(out_path).name, slide_count=slide_count, output_path=out_path)
