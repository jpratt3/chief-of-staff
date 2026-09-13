"""
engine/rsm.py
RSM Deck Builder — business logic. No Flask imports.

APPROACH: ZIP-level slide assembly
===================================
python-pptx has no correct cross-presentation slide copy API. Every
approach that uses the Part object model causes either:
  - Duplicate slide part URIs (from _sldIdLst clear without drop_rel)
  - Cross-presentation Part aliasing (from relate_to() dragging in source's
    transitive rels including other slides)
  - Missing customXml (package-root parts that live at ../../customXml/
    relative to the slide, outside ppt/, unreachable via Part.relate_to)

THE ONLY CORRECT APPROACH: work at the ZIP (byte) level.

Algorithm:
  1. Open source and destination as ZipFile objects.
  2. For each slide to copy:
     a. Read slide XML and its .rels file.
     b. Rename all referenced parts to avoid collisions (e.g. slide3.xml
        -> slideN.xml, notesSlide3.xml -> notesSlideN.xml, etc.)
     c. Copy ALL directly-referenced parts (images, charts, tags, notes,
        customXml, embeddings) into the destination ZIP under new names.
     d. Rewrite rId targets in the rels file to point to the new names.
     e. Add the slide to [Content_Types].xml and presentation.xml.
  3. Save the assembled ZIP.

This handles all part types correctly, preserves all content, and produces
a ZIP that PowerPoint can open without repair.
"""
from __future__ import annotations

import copy
import io
import json
import re
import zipfile
from pathlib import Path
from typing import Optional
from lxml import etree

from .base import SkillResult

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_HERE = Path(__file__).parent
_ROOT = _HERE.parent
_MARKET_DB  = _ROOT / "data" / "rsm_market_db.json"
_MARKET_DIR = _ROOT / "data" / "rsm_market_slides"
_TEMPLATE   = _ROOT / "uploads" / "New RSM Template (001) (2).pptx"

# Namespaces used in pptx XML
NS = {
    "p":  "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r":  "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "a":  "http://schemas.openxmlformats.org/drawingml/2006/main",
    "ct": "http://schemas.openxmlformats.org/package/2006/content-types",
}
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
R_NS   = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

# Rel types to SKIP when copying (slideLayout resolves from shared master)
_SKIP_REL_TYPES = {"slideLayout", "slideMaster", "notesMaster"}

# ---------------------------------------------------------------------------
# Slide classification
# ---------------------------------------------------------------------------

_MARKET_TITLE_KWORDS = [
    "market condition",
    "market executive summary",
    "market snapshot",
    "market conditions,",
    "rate trends",
    "rate monitor",
    "qsg",
    "1st quarter", "2nd quarter", "3rd quarter", "4th quarter",
    "q1 20", "q2 20", "q3 20", "q4 20",
    "all industries market conditions",
    "portfolio rate monitor",
]

_CHROME_LAYOUT_NAMES = {"back cover", "back page blue + disclaimer"}


def _slide_title_from_xml(slide_xml: bytes) -> str:
    try:
        root = etree.fromstring(slide_xml)
        for el in root.iter():
            if el.tag.endswith("}t") and el.text and el.text.strip():
                return el.text.strip()[:80]
    except Exception:
        pass
    return ""


def _slide_layout_name_from_zip(z: zipfile.ZipFile, slide_part: str) -> str:
    """Get the layout name for a slide by following its slideLayout rel."""
    rels_path = _rels_path_for(slide_part)
    if rels_path not in z.namelist():
        return ""
    rels_xml = z.read(rels_path)
    rels_root = etree.fromstring(rels_xml)
    for rel in rels_root:
        rel_type = rel.get("Type", "")
        if "slideLayout" in rel_type:
            target = rel.get("Target", "")
            layout_part = _resolve_target(slide_part, target)
            if layout_part in z.namelist():
                layout_xml = z.read(layout_part)
                layout_root = etree.fromstring(layout_xml)
                cSld = layout_root.find(".//{http://schemas.openxmlformats.org/presentationml/2006/main}cSld")
                if cSld is None:
                    cSld = layout_root.find(".//{http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing}cSld")
                if cSld is None:
                    for el in layout_root.iter():
                        if el.tag.endswith("}cSld"):
                            cSld = el
                            break
                if cSld is not None:
                    return cSld.get("name", "")
    return ""


def _is_market_slide(title: str, layout_name: str) -> bool:
    if layout_name.lower().strip() == "divider slide":
        return False
    t = title.lower()
    return any(kw in t for kw in _MARKET_TITLE_KWORDS)


def _is_chrome_slide(layout_name: str) -> bool:
    return layout_name.lower().strip() in _CHROME_LAYOUT_NAMES


# ---------------------------------------------------------------------------
# ZIP path helpers
# ---------------------------------------------------------------------------

def _rels_path_for(part_path: str) -> str:
    """Return the .rels path for a given part."""
    parts = part_path.split("/")
    return "/".join(parts[:-1]) + "/_rels/" + parts[-1] + ".rels"


def _resolve_target(base_part: str, target: str) -> str:
    """Resolve a relative target from base_part's directory."""
    if target.startswith("/"):
        return target.lstrip("/")
    base_dir = "/".join(base_part.split("/")[:-1])
    combined = base_dir + "/" + target
    parts = combined.split("/")
    resolved = []
    for p in parts:
        if p == "..":
            if resolved:
                resolved.pop()
        elif p and p != ".":
            resolved.append(p)
    return "/".join(resolved)


def _content_type_for(part_path: str, ct_map: dict) -> str:
    return ct_map.get(part_path, "")


# ---------------------------------------------------------------------------
# Content-type map from [Content_Types].xml
# ---------------------------------------------------------------------------

def _parse_content_types(ct_xml: bytes) -> dict:
    """Return dict of partName -> contentType from [Content_Types].xml."""
    root = etree.fromstring(ct_xml)
    result = {}
    ns = "http://schemas.openxmlformats.org/package/2006/content-types"
    for el in root:
        if el.tag == f"{{{ns}}}Override":
            part = el.get("PartName", "").lstrip("/")
            ct = el.get("ContentType", "")
            result[part] = ct
        elif el.tag == f"{{{ns}}}Default":
            ext = el.get("Extension", "")
            ct = el.get("ContentType", "")
            result[f"__ext_{ext}"] = ct
    return result


def _ct_for_part(part_path: str, ct_map: dict) -> str:
    ct = ct_map.get(part_path, "")
    if ct:
        return ct
    ext = part_path.rsplit(".", 1)[-1].lower() if "." in part_path else ""
    return ct_map.get(f"__ext_{ext}", "application/octet-stream")


# ---------------------------------------------------------------------------
# Core: copy one slide from src_zip into dst_zip
# ---------------------------------------------------------------------------

def _copy_slide_zip(
    src_zip: zipfile.ZipFile,
    dst_zip: zipfile.ZipFile,
    src_slide_part: str,     # e.g. "ppt/slides/slide3.xml"
    dst_slide_num: int,      # target slide number in dst
    src_ct_map: dict,
    dst_names: set,          # tracks parts already in dst_zip
    dst_ct_overrides: list,  # accumulates (part_name, ct) for Content_Types
    dst_presentation_rels: list,  # accumulates (rId, target) for presentation.xml.rels
) -> str:
    """
    Copy src_slide_part from src_zip into dst_zip as slideN.xml.
    Copies all directly-referenced parts (notes, images, charts, tags,
    customXml) with collision-safe renaming.
    Returns the new slide part name.
    """
    src_names = set(src_zip.namelist())

    # Counters for part renaming (shared via dst_names inspection)
    def _next_name(pattern: str, dst_names: set) -> str:
        """Find next available name matching pattern with %d placeholder."""
        i = 1
        while True:
            candidate = pattern % i
            if candidate not in dst_names:
                return candidate
            i += 1

    dst_slide_part = f"ppt/slides/slide{dst_slide_num}.xml"

    # Read source slide XML
    slide_xml = src_zip.read(src_slide_part)

    # Read source rels
    src_rels_path = _rels_path_for(src_slide_part)
    if src_rels_path in src_names:
        rels_xml = src_zip.read(src_rels_path)
        rels_root = etree.fromstring(rels_xml)
    else:
        rels_root = etree.fromstring(b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>')

    # Build new rels, copying each referenced part
    new_rels_root = etree.Element(
        f"{{{REL_NS}}}Relationships"
    )

    for rel in rels_root:
        rel_type = rel.get("Type", "")
        rel_type_short = rel_type.split("/")[-1]
        target = rel.get("Target", "")
        rId = rel.get("Id", "")
        mode = rel.get("TargetMode", "Internal")

        # Always skip slideLayout (resolves from shared master in dst)
        if rel_type_short in _SKIP_REL_TYPES:
            # Add the slideLayout rel pointing to dst's copy
            # Find which layout in dst matches the source layout
            src_layout_part = _resolve_target(src_slide_part, target)
            layout_name = ""
            if src_layout_part in src_names:
                try:
                    layout_xml = src_zip.read(src_layout_part)
                    root = etree.fromstring(layout_xml)
                    for el in root.iter():
                        if el.tag.endswith("}cSld"):
                            layout_name = el.get("name", "")
                            break
                except Exception:
                    pass
            # Find matching layout in dst
            dst_layout_target = _find_dst_layout(dst_zip, layout_name, dst_names)
            if dst_layout_target:
                new_rel = etree.SubElement(new_rels_root, f"{{{REL_NS}}}Relationship")
                new_rel.set("Id", rId)
                new_rel.set("Type", rel_type)
                new_rel.set("Target", dst_layout_target)
            continue

        if rel_type_short == "notesMaster":
            continue  # notesMaster resolves from the notes master in dst

        if mode == "External":
            new_rel = etree.SubElement(new_rels_root, f"{{{REL_NS}}}Relationship")
            new_rel.set("Id", rId)
            new_rel.set("Type", rel_type)
            new_rel.set("Target", target)
            new_rel.set("TargetMode", "External")
            continue

        # Internal part — copy bytes with collision-safe name
        src_part = _resolve_target(src_slide_part, target)
        if src_part not in src_names:
            continue

        part_bytes = src_zip.read(src_part)
        ext = src_part.rsplit(".", 1)[-1] if "." in src_part else "xml"
        part_ct = _ct_for_part(src_part, src_ct_map)

        # Determine destination path
        if rel_type_short == "notesSlide":
            dst_part = _next_name("ppt/notesSlides/notesSlide%d.xml", dst_names)
        elif rel_type_short == "image":
            dst_part = _next_name(f"ppt/media/image%d.{ext}", dst_names)
        elif rel_type_short == "chart":
            dst_part = _next_name("ppt/charts/chart%d.xml", dst_names)
        elif rel_type_short == "tags":
            dst_part = _next_name("ppt/tags/tag%d.xml", dst_names)
        elif rel_type_short == "customXml":
            dst_part = _next_name("customXml/item%d.xml", dst_names)
        elif rel_type_short == "audio":
            dst_part = _next_name(f"ppt/media/audio%d.{ext}", dst_names)
        elif rel_type_short == "video":
            dst_part = _next_name(f"ppt/media/video%d.{ext}", dst_names)
        else:
            dst_part = _next_name(f"ppt/misc/part%d.{ext}", dst_names)

        dst_names.add(dst_part)
        dst_zip.writestr(dst_part, part_bytes)
        if part_ct:
            dst_ct_overrides.append((dst_part, part_ct))

        # Handle sub-rels for this part (chart embeddings, notes master,
        # customXml props, etc.)
        src_part_rels_path = _rels_path_for(src_part)
        if src_part_rels_path in src_names:
            sub_rels_xml = src_zip.read(src_part_rels_path)
            sub_rels_root = etree.fromstring(sub_rels_xml)
            new_sub_rels = etree.Element(f"{{{REL_NS}}}Relationships")

            for sub_rel in sub_rels_root:
                sub_type = sub_rel.get("Type", "")
                sub_type_short = sub_type.split("/")[-1]
                sub_target = sub_rel.get("Target", "")
                sub_rId = sub_rel.get("Id", "")
                sub_mode = sub_rel.get("TargetMode", "Internal")

                if sub_type_short in ("slide", "slideMaster", "notesMaster", "slideLayout"):
                    # Back-ref to slide or master — skip, not needed
                    if sub_type_short == "notesMaster":
                        # Point to dst's notesMaster
                        dst_nm = _find_notes_master(dst_zip, dst_names)
                        if dst_nm:
                            sr = etree.SubElement(new_sub_rels, f"{{{REL_NS}}}Relationship")
                            sr.set("Id", sub_rId)
                            sr.set("Type", sub_type)
                            sr.set("Target", dst_nm)
                    continue

                if sub_mode == "External":
                    sr = etree.SubElement(new_sub_rels, f"{{{REL_NS}}}Relationship")
                    sr.set("Id", sub_rId)
                    sr.set("Type", sub_type)
                    sr.set("Target", sub_target)
                    sr.set("TargetMode", "External")
                    continue

                sub_src_part = _resolve_target(src_part, sub_target)
                if sub_src_part not in src_names:
                    continue

                sub_bytes = src_zip.read(sub_src_part)
                sub_ext = sub_src_part.rsplit(".", 1)[-1] if "." in sub_src_part else "bin"
                sub_ct = _ct_for_part(sub_src_part, src_ct_map)

                if sub_type_short == "package":  # Excel embedding for charts
                    sub_dst = _next_name(f"ppt/embeddings/embedding%d.{sub_ext}", dst_names)
                elif sub_type_short == "customXmlProps":
                    # itemPropsN.xml lives alongside item in customXml/
                    sub_dst = _next_name("customXml/itemProps%d.xml", dst_names)
                else:
                    sub_dst = _next_name(f"ppt/misc/sub%d.{sub_ext}", dst_names)

                dst_names.add(sub_dst)
                dst_zip.writestr(sub_dst, sub_bytes)
                if sub_ct:
                    dst_ct_overrides.append((sub_dst, sub_ct))

                # Relative target from dst_part to sub_dst
                rel_target = _rel_target(dst_part, sub_dst)
                sr = etree.SubElement(new_sub_rels, f"{{{REL_NS}}}Relationship")
                sr.set("Id", sub_rId)
                sr.set("Type", sub_type)
                sr.set("Target", rel_target)

            dst_sub_rels_path = _rels_path_for(dst_part)
            dst_names.add(dst_sub_rels_path)
            dst_zip.writestr(dst_sub_rels_path, etree.tostring(new_sub_rels, xml_declaration=True, encoding="UTF-8", standalone=True))

        # Relative target from dst_slide_part to dst_part
        new_target = _rel_target(dst_slide_part, dst_part)
        new_rel = etree.SubElement(new_rels_root, f"{{{REL_NS}}}Relationship")
        new_rel.set("Id", rId)
        new_rel.set("Type", rel_type)
        new_rel.set("Target", new_target)

    # Write slide XML
    dst_names.add(dst_slide_part)
    dst_zip.writestr(dst_slide_part, slide_xml)

    # Write slide rels
    dst_rels_path = _rels_path_for(dst_slide_part)
    dst_names.add(dst_rels_path)
    dst_zip.writestr(
        dst_rels_path,
        etree.tostring(new_rels_root, xml_declaration=True, encoding="UTF-8", standalone=True)
    )

    slide_ct = _ct_for_part(src_slide_part, src_ct_map)
    if not slide_ct:
        slide_ct = "application/vnd.openxmlformats-officedocument.presentationml.slide+xml"
    dst_ct_overrides.append((dst_slide_part, slide_ct))

    return dst_slide_part


def _rel_target(from_part: str, to_part: str) -> str:
    """Compute relative path from from_part's directory to to_part."""
    from_dir = from_part.rsplit("/", 1)[0].split("/")
    to_parts = to_part.split("/")
    # Find common prefix
    common = 0
    for a, b in zip(from_dir, to_parts):
        if a == b:
            common += 1
        else:
            break
    ups = len(from_dir) - common
    rel = [".."] * ups + to_parts[common:]
    return "/".join(rel)


def _find_dst_layout(dst_zip, layout_name: str, dst_names: set) -> str:
    """Find a layout in dst_zip by cSld name, return relative target from slides/."""
    names = dst_zip.namelist()
    for name in names:
        if "slideLayouts/slideLayout" in name and name.endswith(".xml") and "_rels" not in name:
            try:
                xml = dst_zip.read(name)
                root = etree.fromstring(xml)
                for el in root.iter():
                    if el.tag.endswith("}cSld"):
                        if el.get("name", "") == layout_name:
                            # relative from ppt/slides/slideN.xml
                            return "../slideLayouts/" + name.split("/")[-1]
            except Exception:
                pass
    # fallback: first layout
    for name in names:
        if "slideLayouts/slideLayout1.xml" in name:
            return "../slideLayouts/slideLayout1.xml"
    return ""


def _find_notes_master(dst_zip, dst_names: set) -> str:
    for name in dst_zip.namelist():
        if "notesMasters/notesMaster" in name and name.endswith(".xml"):
            return "../../notesMasters/" + name.split("/")[-1]
    return ""


# ---------------------------------------------------------------------------
# ZIP-level deck assembler
# ---------------------------------------------------------------------------

def _assemble_deck(
    base_pptx_bytes: bytes,
    slides_to_copy: list,  # list of (src_pptx_bytes, slide_index_0based)
) -> bytes:
    """
    Build a new pptx from base_pptx_bytes, replacing all slides with
    those specified in slides_to_copy.

    base_pptx_bytes: the old deck — provides master, theme, layouts
    slides_to_copy: ordered list of (src_bytes, slide_0based_index)
    """
    # Parse base as ZIP
    base_zip = zipfile.ZipFile(io.BytesIO(base_pptx_bytes))
    base_names = set(base_zip.namelist())
    base_ct_map = _parse_content_types(base_zip.read("[Content_Types].xml"))

    # Build output ZIP in memory
    out_buf = io.BytesIO()
    dst_zip = zipfile.ZipFile(out_buf, "w", compression=zipfile.ZIP_DEFLATED)
    dst_names = set()
    dst_ct_overrides = []
    dst_presentation_rels = []  # (rId, target, rel_type)

    # --- Copy all non-slide parts from base ---
    # (master, layouts, theme, media, fonts, settings, etc.)
    # Skip: slides, slide rels, [Content_Types].xml, presentation.xml,
    #        presentation.xml.rels (we'll rebuild those)
    skip_patterns = {
        "[Content_Types].xml",
        "ppt/presentation.xml",
        "ppt/_rels/presentation.xml.rels",
    }

    for name in base_zip.namelist():
        if name in skip_patterns:
            continue
        # Skip all slide content (we rebuild from scratch)
        if re.match(r"ppt/slides/slide\d+\.xml", name):
            continue
        if re.match(r"ppt/slides/_rels/slide\d+\.xml\.rels", name):
            continue
        if re.match(r"ppt/notesSlides/notesSlide\d+\.xml", name):
            continue
        if re.match(r"ppt/notesSlides/_rels/notesSlide\d+\.xml\.rels", name):
            continue
        # Keep everything else (master, layouts, theme, media, etc.)
        data = base_zip.read(name)
        dst_zip.writestr(name, data)
        dst_names.add(name)

    # --- Copy slides ---
    slide_num = 1
    for src_bytes, slide_idx in slides_to_copy:
        src_zip = zipfile.ZipFile(io.BytesIO(src_bytes))
        src_ct_map = _parse_content_types(src_zip.read("[Content_Types].xml"))
        src_slide_names = sorted(
            [n for n in src_zip.namelist()
             if re.match(r"ppt/slides/slide\d+\.xml$", n)],
            key=lambda x: int(re.search(r"\d+", x.split("/")[-1]).group())
        )
        if slide_idx >= len(src_slide_names):
            continue
        src_slide_part = src_slide_names[slide_idx]

        rId = f"rId{slide_num + 100}"  # offset to avoid collisions with existing rels

        dst_slide_part = _copy_slide_zip(
            src_zip, dst_zip,
            src_slide_part, slide_num,
            src_ct_map, dst_names,
            dst_ct_overrides, dst_presentation_rels
        )
        dst_presentation_rels.append((rId, f"slides/slide{slide_num}.xml"))
        slide_num += 1

    # --- Rebuild presentation.xml ---
    prs_xml = base_zip.read("ppt/presentation.xml")
    prs_root = etree.fromstring(prs_xml)
    P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"

    # Clear existing sldIdLst
    sldIdLst = prs_root.find(f"{{{P_NS}}}sldIdLst")
    if sldIdLst is None:
        sldIdLst = etree.SubElement(prs_root, f"{{{P_NS}}}sldIdLst")
    for child in list(sldIdLst):
        sldIdLst.remove(child)

    # Add new slide IDs
    base_id = 256
    for i, (rId, _) in enumerate(dst_presentation_rels):
        sldId = etree.SubElement(sldIdLst, f"{{{P_NS}}}sldId")
        sldId.set("id", str(base_id + i))
        sldId.set(f"{{{R_NS}}}id", rId)

    dst_zip.writestr("ppt/presentation.xml",
                     etree.tostring(prs_root, xml_declaration=True, encoding="UTF-8", standalone=True))

    # --- Rebuild presentation.xml.rels ---
    base_prs_rels = base_zip.read("ppt/_rels/presentation.xml.rels")
    prs_rels_root = etree.fromstring(base_prs_rels)
    # Remove old slide rels
    for rel in list(prs_rels_root):
        t = rel.get("Type", "")
        if "slide\"" in t or t.endswith("/slide"):
            prs_rels_root.remove(rel)
    # Add new slide rels
    for rId, target in dst_presentation_rels:
        rel = etree.SubElement(prs_rels_root, f"{{{REL_NS}}}Relationship")
        rel.set("Id", rId)
        rel.set("Type", f"http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide")
        rel.set("Target", target)

    dst_zip.writestr("ppt/_rels/presentation.xml.rels",
                     etree.tostring(prs_rels_root, xml_declaration=True, encoding="UTF-8", standalone=True))

    # --- Rebuild [Content_Types].xml ---
    ct_xml = base_zip.read("[Content_Types].xml")
    ct_root = etree.fromstring(ct_xml)
    CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
    # Remove old slide overrides
    for el in list(ct_root):
        part = el.get("PartName", "")
        if re.match(r"/ppt/slides/slide\d+\.xml", part):
            ct_root.remove(el)
        elif re.match(r"/ppt/notesSlides/notesSlide\d+\.xml", part):
            ct_root.remove(el)
    # Add new overrides
    existing_parts = {el.get("PartName", "") for el in ct_root}
    for part_name, ct in dst_ct_overrides:
        pn = "/" + part_name
        if pn not in existing_parts:
            el = etree.SubElement(ct_root, f"{{{CT_NS}}}Override")
            el.set("PartName", pn)
            el.set("ContentType", ct)
            existing_parts.add(pn)

    dst_zip.writestr("[Content_Types].xml",
                     etree.tostring(ct_root, xml_declaration=True, encoding="UTF-8", standalone=True))

    dst_zip.close()
    return out_buf.getvalue()


# ---------------------------------------------------------------------------
# Slide selection helpers
# ---------------------------------------------------------------------------

def _get_slide_list(pptx_bytes: bytes) -> list[tuple[str, str]]:
    """Return list of (title, layout_name) for each slide in a pptx."""
    with zipfile.ZipFile(io.BytesIO(pptx_bytes)) as z:
        names = z.namelist()
        slide_parts = sorted(
            [n for n in names if re.match(r"ppt/slides/slide\d+\.xml$", n)],
            key=lambda x: int(re.search(r"\d+", x.split("/")[-1]).group())
        )
        ct_map = _parse_content_types(z.read("[Content_Types].xml"))
        result = []
        for sp in slide_parts:
            xml = z.read(sp)
            title = _slide_title_from_xml(xml)
            layout_name = _slide_layout_name_from_zip(z, sp)
            result.append((title, layout_name))
        return result


# ---------------------------------------------------------------------------
# Market slide database
# ---------------------------------------------------------------------------

def load_market_db() -> list[dict]:
    if not _MARKET_DB.exists():
        return []
    for enc in ("utf-8", "utf-8-sig", "latin-1", "cp1252"):
        try:
            return json.loads(_MARKET_DB.read_text(encoding=enc))
        except (UnicodeDecodeError, ValueError):
            continue
    return []


def _get_market_slide_bytes(filename: str) -> Optional[bytes]:
    path = _MARKET_DIR / filename
    return path.read_bytes() if path.exists() else None


def _update_year_bytes(slide_xml: bytes, old_year: str, new_year: str) -> bytes:
    if not old_year or not new_year or old_year == new_year:
        return slide_xml
    return slide_xml.replace(old_year.encode(), new_year.encode())


# ---------------------------------------------------------------------------
# Main build
# ---------------------------------------------------------------------------

def build_rsm(
    old_deck_bytes: bytes,
    program_graphic_bytes: Optional[bytes],
    client_name: str,
    new_year: str,
    old_year: str,
    selected_market_ids: list[str],
) -> SkillResult:
    try:
        from pptx import Presentation  # just to check it's installed
    except ImportError:
        return SkillResult(success=False, error="python-pptx not installed.")

    slide_log = []

    # Collect (src_bytes, slide_index) pairs in order
    slides_to_copy: list[tuple[bytes, int]] = []

    # --- Walk old deck ---
    try:
        old_slide_list = _get_slide_list(old_deck_bytes)
    except Exception as e:
        return SkillResult(success=False, error=f"Could not read uploaded deck: {e}")

    for idx, (title, layout_name) in enumerate(old_slide_list):
        if _is_chrome_slide(layout_name):
            slide_log.append({"title": title or "(chrome)", "source": "skipped — back cover", "type": "skip"})
            continue
        if _is_market_slide(title, layout_name):
            slide_log.append({"title": title, "source": "skipped — market slide", "type": "skip"})
            continue
        slides_to_copy.append((old_deck_bytes, idx))
        slide_log.append({
            "title": title or f"slide {idx+1}",
            "source": "copied from old deck",
            "type": "divider" if layout_name.lower() == "divider slide" else "content",
        })

    # --- Program graphic slides ---
    if program_graphic_bytes:
        try:
            pg_list = _get_slide_list(program_graphic_bytes)
            for idx, (title, _) in enumerate(pg_list):
                slides_to_copy.append((program_graphic_bytes, idx))
                slide_log.append({"title": title or "Program Graphic", "source": "program graphic upload", "type": "diagram"})
        except Exception as e:
            slide_log.append({"title": "Program Graphic", "source": f"ERROR: {e}", "type": "error"})

    # --- Market slides from database ---
    market_by_id = {m["id"]: m for m in load_market_db()}
    for market_id in selected_market_ids:
        entry = market_by_id.get(market_id)
        if not entry:
            continue
        slide_bytes = _get_market_slide_bytes(entry["filename"])
        if not slide_bytes:
            slide_log.append({"title": entry["title"], "source": "ERROR: file not found", "type": "market"})
            continue
        try:
            mkt_list = _get_slide_list(slide_bytes)
            for idx in range(len(mkt_list)):
                slides_to_copy.append((slide_bytes, idx))
            slide_log.append({"title": entry["title"], "source": f"market database ({entry.get('quarter','')})", "type": "market"})
        except Exception as e:
            slide_log.append({"title": entry["title"], "source": f"ERROR: {e}", "type": "market"})

    # --- Back cover from template ---
    back_added = False
    if _TEMPLATE.exists():
        try:
            tmpl_bytes = _TEMPLATE.read_bytes()
            tmpl_list = _get_slide_list(tmpl_bytes)
            # Last slide of template is back cover
            last_idx = len(tmpl_list) - 1
            slides_to_copy.append((tmpl_bytes, last_idx))
            slide_log.append({"title": "Back Cover", "source": "new template", "type": "chrome"})
            back_added = True
        except Exception:
            pass

    if not back_added:
        # Fall back to old deck's last slide if it's a back cover
        if old_slide_list:
            last_title, last_layout = old_slide_list[-1]
            if _is_chrome_slide(last_layout):
                slides_to_copy.append((old_deck_bytes, len(old_slide_list) - 1))
                slide_log.append({"title": "Back Cover", "source": "old deck (fallback)", "type": "chrome"})

    # --- Assemble ---
    try:
        pptx_bytes = _assemble_deck(old_deck_bytes, slides_to_copy)
    except Exception as e:
        import traceback
        return SkillResult(success=False, error=f"Assembly failed: {e}\n{traceback.format_exc()}")

    # Count slides in output
    with zipfile.ZipFile(io.BytesIO(pptx_bytes)) as z:
        slide_count = len([n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)])

    return SkillResult(
        success=True,
        data={
            "pptx_bytes": pptx_bytes,
            "slide_count": slide_count,
            "slide_log": slide_log,
            "client_name": client_name,
            "new_year": new_year,
        },
    )
