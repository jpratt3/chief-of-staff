"""
engine/loss_run.py
Loss Run Request skill — business logic. No Flask imports.

EXTRACTION APPROACH: layout text + scored candidates
====================================================
Every carrier generates binders differently, so nothing here assumes a format.
Three layers do the work:

1. Text layer — pdfplumber's layout mode, which preserves column alignment.
   That is what makes "Label:   value", header-row-over-value tables and
   two-column forms all reachable with the same rules. pypdf provides the
   cheap first pass used to rank pages; a shifted-encoding decoder repairs
   PDFs whose font maps come out garbled.

2. Page selection — pages are scored for declarations-likeness and only the
   best few are re-read in layout mode. The first two pages always keep a
   slot: cover letters name the insured and carrier without using any label.

3. Field layer — each field generates scored candidates from every place its
   value could sit (same line, the column below, the line above, the
   filename, free text), then the best-scoring candidate wins. Labels that
   introduce someone else's policy ("Followed Policy", "Underlying",
   "Quota Share") push candidates down rather than being ignored outright.

Gazetteers do the heavy lifting for entities: carriers come from
companymap.md and clients from clients.json, so a name known to the business
is recognised anywhere on the page. _CARRIER_ENTITY_ALIASES bridges the gap
between the legal entity a binder names and the group companymap files it
under; companymap.md remains the source of truth for the grouping itself.

File routing:
  .pdf / .PDF  → page selection + layout text
  .docx        → python-docx, tables rendered as label/value lines
  .xlsx/.xls   → pandas
  .csv         → pandas

"Could not extract" is a UI state, not a hard error. The caller always gets
partial results so the user can fill in or correct fields manually. A PDF with
no selectable text still returns whatever the filename yields, plus a note.
"""
from __future__ import annotations

import io
import json
import re
import unicodedata
from dataclasses import dataclass, asdict
from datetime import date
from pathlib import Path
from typing import Optional

from .base import SkillResult

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_HERE = Path(__file__).parent
_ROOT = _HERE.parent
_CARRIERS_DB      = _ROOT / "data"   / "loss_run_carriers.json"
_CLIENTS_PATH     = _ROOT / "config" / "clients.json"
_COV_ALIASES_PATH = _ROOT / "config" / "coverage_aliases.json"
_COMPANYMAP_PATH  = _ROOT / "docs" / "Skills" / "companymap.md"
_EMAILMAP_PATH    = _ROOT / "docs" / "Skills" / "emailmap.md"
_COVERAGES_MD_PATH = _ROOT / "docs" / "Skills" / "coverages.md"

# ---------------------------------------------------------------------------
# Coverage normalisation
# ---------------------------------------------------------------------------
_COVERAGE_MAP = {
    "MOD PKG":               "Property",
    "PKG":                   "Property",
    "PROPERTY":              "Property",
    "PROP":                  "Property",
    "AUTO":                  "Automobile",
    "AUTOMOBILE":            "Automobile",
    "TRUCKING":              "Automobile",
    "WORK COMP":             "Workers Compensation",
    "WC":                    "Workers Compensation",
    "WORKERS COMP":          "Workers Compensation",
    "UMBRELLA":              "Umbrella",
    "UMB":                   "Umbrella",
    "EXCESS UMBRELLA":       "Umbrella",
    "EXCESS":                "Excess",
    "EXCESS CASUALTY":       "Excess",
    "GL":                    "General Liability",
    "GENERAL LIABILITY":     "General Liability",
    "LIABILITY":             "General Liability",
    "D&O":                   "Directors & Officers",
    "EPL":                   "Employment Practices Liability",
    "FIDUCIARY":             "Fiduciary Liability",
    "CRIME":                 "Crime",
    "CYBER":                 "Cyber",
    "INLAND MARINE":         "Inland Marine",
    "OCEAN MARINE":          "Ocean Marine",
    "PROFESSIONAL":          "Professional Liability",
    "E&O":                   "Professional Liability",
    "AVIATION":              "Aviation",
    "CHARTERER":             "Marine",
    "MARINE":                "Marine",
}

# ---------------------------------------------------------------------------
# Label vocabulary
#
# One scored table per field. Higher score = more trustworthy label. Entries
# may carry a third element, `free`, which exempts a phrase label from the
# anchoring rule in _label_hits (see there).
# ---------------------------------------------------------------------------
_PN_LABELS: list[tuple] = [
    (r"Policy\s*Symbol\s*and\s*Number",                      100),
    (r"Policy\s*Number\s*/\s*Insurance\s*Contract\s*Number",  100),
    (r"Assigned\s*Policy\s*Number",                           100),
    (r"Policy\s*Nu?mber",                                      98),
    (r"Policy\s*No\.?",                                        96),
    (r"Policy\s*#",                                            96),
    (r"POLICY#",                                               96),
    (r"Bond\s*Number",                                         96),
    (r"Bond\s*No\.?",                                          92),
    (r"Policy\s*Nbr",                                          90),
    (r"Unique\s*Market\s*Reference(?:\s*/\s*Contract\s*Number)?", 92),
    (r"UMR",                                                   90),
    (r"Certificate\s*Number",                                  88),
    (r"Certificate\s*of\s*Entry",                              86),
    (r"(?:Insurance\s*)?Contract\s*Number",                    84),
]

# Words that, sitting just before a policy-number label, mean the value belongs
# to some *other* policy — expiring, underlying, followed, lead, and so on.
_PN_NEGATIVE_CONTEXT = re.compile(
    r"(?:renewal\s+of|renewal|expiring|replace[sd]?|replacement|previous|prior|"
    r"underlying|followed|follow(?:ing)?|lead|quota\s*share|schedule\s+of|"
    r"controlling|excess\s+of|binder\s+renewal)",
    re.IGNORECASE,
)

_CARRIER_LABELS: list[tuple] = [
    (r"Policy\s*Issuing\s*Company",   100),
    (r"Insurance\s*Carrier",          100),
    (r"Issuing\s*Company",             98),
    (r"Insuring\s*Company",            98),
    (r"Underwriting\s*Company",        96),
    (r"Policy\s*Issued\s*[Bb]y",       96),
    (r"Insurance\s*Company",           94),
    (r"Name\s*of\s*Company",           94),
    (r"Insured\s*[Bb]y",               92),
    (r"Issued\s*[Bb]y",                90),
    (r"Underwritten\s*[Bb]y",          90),
    (r"Insurer",                       88),
    (r"Carrier",                       84),
    (r"Company",                       70),
]

_CARRIER_NEGATIVE_CONTEXT = re.compile(
    r"(?:underlying|followed|follow(?:ing)?\s+form|lead|quota\s*share|excess\s+of|"
    r"schedule\s+of|notice\s+to|claims?|broker|producer|agent|reinsur)",
    re.IGNORECASE,
)

_INSURED_LABELS: list[tuple] = [
    (r"First\s*Named\s*Insured",                       100),
    (r"Named\s*Insured\s*(?:Name)?",                   100),
    (r"Name\s*Insured",                                 98),
    (r"Insured\s*Name",                                 98),
    (r"Binder\s*[Ff]or\s*Insurance",                    96, True),
    (r"Insured\s*Company(?:\s*Principal\s*Address)?",   94),
    (r"Assured",                                        92),
    (r"Policyholder",                                    90),
    (r"Named\s*Entity",                                 90),
    (r"Applicant\s*Name",                               90),
    (r"Account\s*Name",                                 88),
    (r"In\s*the\s*name\s*of",                           88, True),
    (r"Binder\s*[Ff]or",                                86, True),
    (r"Proposal\s*[Ff]or",                              84, True),
    (r"Program\s*[Ff]or",                               84, True),
    (r"Prepared\s*[Ff]or",                              82, True),
    (r"Account",                                        80),
    (r"Insured",                                        82),
    (r"Applicant",                                      76),
    (r"Customer",                                       70),
    (r"RE",                                             64),
    (r"Subject",                                        60),
]

_EFF_LABELS: list[tuple] = [
    (r"Policy\s*Effective\s*Dates?",                        100),
    (r"Policy\s*Period\s*/\s*Insurance\s*Contract\s*Term",  100),
    (r"Policy\s*Period",                                     98),
    (r"Policy\s*Term",                                       96),
    (r"Period\s*of\s*Insurance",                             96),
    (r"Coverage\s*Period",                                   94),
    (r"Effective\s*Dates?",                                  92),
    (r"Inception\s*Date",                                    92),
    (r"Term\s*of\s*this\s*Policy\s*is",                      92),
    (r"Binder\s*Effective",                                  80),
    (r"Effective",                                           78),
    (r"Period",                                              74),
    (r"Term",                                                70),
]

_EFF_NEGATIVE_CONTEXT = re.compile(
    r"(?:expir|retroactive|continuity|pending|prior|issued|issuance|application|"
    r"release|underlying|followed|binder\s+expiration|renewal\s+of|discovery)",
    re.IGNORECASE,
)

_COVERAGE_LABELS: list[tuple] = [
    (r"Line\s*of\s*Business",   100),
    (r"Type\s*of\s*Coverage",   100),
    (r"Coverage\s*Type",         98),
    (r"Type\s*of\s*Policy",      96),
    (r"Policy\s*Type",           94),
    (r"Product(?:\s*Type)?",     90),
    (r"Coverage",                84),
    (r"Type",                    70),
]

_DATE_RE = re.compile(
    # The trailing guard is a lookahead rather than \b: some PDFs run words
    # together ("2/9/2025to2/9/2026") and the date still has to be found.
    r"\b(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4}"
    r"|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s*\d{1,2}(?:st|nd|rd|th)?,?\s*\d{4}"
    r"|\d{1,2}(?:st|nd|rd|th)?\s*(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?,?\s*\d{4})"
    r"(?![\d/])",
    re.IGNORECASE,
)

_APPROVED_COVERAGE_CATEGORIES = set(_COVERAGE_MAP.values())


# ---------------------------------------------------------------------------
# Carrier DB
# ---------------------------------------------------------------------------

def _load_carrier_db() -> dict:
    if not _CARRIERS_DB.exists():
        return {"carriers": {}, "aliases": {}}
    return json.loads(_CARRIERS_DB.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Text normalisation helpers
# ---------------------------------------------------------------------------

def _clean(text: str) -> str:
    """Normalise unicode whitespace and strip."""
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"[\u00a0\u2002-\u200b\u202f\u205f\u3000]", " ", text)
    return text.strip()


_companymap_cache: dict | None = None
_emailmap_cache: dict | None = None
_coverages_md_cache: dict | None = None


def _canonicalize_key(text: str) -> str:
    cleaned = _clean(text or '').lower()
    cleaned = re.sub(r"[^a-z0-9]+", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _load_companymap() -> dict:
    global _companymap_cache
    if _companymap_cache is not None:
        return _companymap_cache

    canonical_to_aliases: dict[str, list[str]] = {}
    alias_to_canonical: dict[str, str] = {}
    current: str | None = None

    if _COMPANYMAP_PATH.exists():
        for raw_line in _COMPANYMAP_PATH.read_text(encoding='utf-8').splitlines():
            if not raw_line.strip():
                continue
            if raw_line.startswith('- '):
                current = _clean(raw_line[2:])
                canonical_to_aliases.setdefault(current, [])
                alias_to_canonical[_canonicalize_key(current)] = current
            elif current and raw_line.startswith('  - '):
                alias = _clean(raw_line[4:])
                if alias:
                    canonical_to_aliases[current].append(alias)
                    alias_to_canonical[_canonicalize_key(alias)] = current

    _companymap_cache = {
        'canonical_to_aliases': canonical_to_aliases,
        'alias_to_canonical': alias_to_canonical,
    }
    return _companymap_cache


def _load_emailmap() -> dict:
    global _emailmap_cache
    if _emailmap_cache is not None:
        return _emailmap_cache

    mapping: dict[str, str] = {}
    if _EMAILMAP_PATH.exists():
        for raw_line in _EMAILMAP_PATH.read_text(encoding='utf-8').splitlines():
            line = raw_line.strip()
            if not line.startswith('- ') or ':' not in line:
                continue
            carrier, email = line[2:].split(':', 1)
            carrier = _clean(carrier)
            email = _clean(email)
            if carrier:
                mapping[carrier] = email

    _emailmap_cache = mapping
    return _emailmap_cache


def _load_coverages_md() -> dict:
    global _coverages_md_cache
    if _coverages_md_cache is not None:
        return _coverages_md_cache

    categories: set[str] = set(_APPROVED_COVERAGE_CATEGORIES)
    terms: dict[str, str] = {}

    explicit_term_map = {
        'property': 'Property',
        'commercial property': 'Property',
        'all risk property': 'Property',
        'general liability': 'General Liability',
        'commercial general liability': 'General Liability',
        'cgl': 'General Liability',
        'completed operations': 'General Liability',
        'automobile': 'Automobile',
        'commercial auto liability': 'Automobile',
        'hired and non-owned auto': 'Automobile',
        'owned auto': 'Automobile',
        'workers compensation': 'Workers Compensation',
        'workers compensation employers liability accident': 'Workers Compensation',
        'workers compensation / employers liability / accident': 'Workers Compensation',
        'employers liability': 'Workers Compensation',
        'professional liability': 'Professional Liability',
        'errors and omissions eo': 'Professional Liability',
        'errors and omissions': 'Professional Liability',
        'e&o': 'Professional Liability',
        'directors and officers': 'Directors & Officers',
        'd&o': 'Directors & Officers',
        'employment practices liability': 'Employment Practices Liability',
        'epli': 'Employment Practices Liability',
        'fiduciary liability': 'Fiduciary Liability',
        'crime': 'Crime',
        'commercial crime': 'Crime',
        'cyber': 'Cyber',
        'cyber liability': 'Cyber',
        'marine': 'Marine',
        'marine cargo': 'Marine',
        'charterers liability': 'Marine',
        'charterers legal liability': 'Marine',
        'inland marine': 'Inland Marine',
        'ocean marine': 'Ocean Marine',
        'aviation': 'Aviation',
        'aviation liability': 'Aviation',
        'aircraft liability': 'Aviation',
        'umbrella': 'Umbrella',
        'umbrella liability': 'Umbrella',
        'lead umbrella': 'Umbrella',
        'excess': 'Excess',
        'excess liability': 'Excess',
        'follow form excess': 'Excess',
        'standalone excess': 'Excess',
    }

    if _COVERAGES_MD_PATH.exists():
        for raw_line in _COVERAGES_MD_PATH.read_text(encoding='utf-8').splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith('## '):
                heading = re.sub(r'^##\s*', '', line)
                heading = re.sub(r'^\d+\)\s*', '', heading).strip()
                heading_key = _canonicalize_key(heading)
                if heading_key in explicit_term_map:
                    canonical = explicit_term_map[heading_key]
                    categories.add(canonical)
                    terms[heading_key] = canonical
                continue
            if line.startswith('- '):
                term = _clean(line[2:])
                key = _canonicalize_key(term)
                if key in explicit_term_map:
                    canonical = explicit_term_map[key]
                    categories.add(canonical)
                    terms[key] = canonical

    for key, value in _COVERAGE_MAP.items():
        categories.add(value)
        terms[_canonicalize_key(key)] = value
        terms[_canonicalize_key(value)] = value

    _coverages_md_cache = {
        'categories': categories,
        'terms': terms,
    }
    return _coverages_md_cache


# ===========================================================================
# EXTRACTION ENGINE
#
# Text layer  : pdfplumber layout-preserving text (falls back to pypdf, then to
#               a shifted-encoding decode for PDFs with broken font maps).
# Field layer : scored candidate generation per field, then best-candidate pick.
# Gazetteers  : carriers come from companymap.md, clients from clients.json —
#               matching a known entity anywhere in the text beats guessing.
# ===========================================================================

# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

_APOSTROPHES = "'‘’ʼ´`"


def _norm(text: str) -> str:
    """
    Aggressive normalisation used for gazetteer matching and comparisons.
    Splits camel case first: some PDFs emit no spaces at all, so
    "StarrIndemnity&LiabilityCompany" has to reduce to the same key as
    "Starr Indemnity & Liability Company". Gazetteer entries go through the
    same function, so the transform stays symmetric.
    """
    text = _clean(text or "")
    for ch in _APOSTROPHES:
        text = text.replace(ch, "")
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    text = re.sub(r"[^A-Za-z0-9]+", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------------
# Carrier gazetteer
#
# companymap.md stays the source of truth for grouping. The table below only
# maps *legal entity names that appear on binders* onto a companymap group —
# e.g. binders say "Continental Casualty Company", companymap says "CNA".
# ---------------------------------------------------------------------------

_CARRIER_ENTITY_ALIASES: dict[str, str] = {
    # AIG
    "commerce and industry insurance":      "AIG",
    "lexington insurance":                  "AIG",
    "national union fire":                  "AIG",
    "aig specialty":                        "AIG",
    # Chubb / ACE / Federal
    "federal insurance company":            "Chubb",
    "great northern insurance":             "Chubb",
    "vigilant insurance company":           "Chubb",
    "pacific indemnity company":            "Chubb",
    "ace american insurance":               "Chubb",
    "ace property and casualty":            "Chubb",
    "illinois national insurance":          "Chubb",
    "chubb group of insurance companies":   "Chubb",
    # CNA
    "continental casualty company":         "CNA",
    "the continental insurance company":    "CNA",
    "continental insurance company":        "CNA",
    "cna paramount":                        "CNA",
    # Great American
    "great american insurance":             "Great American",
    "great american spirit":                "Great American",
    "great american professional risk":     "Great American",
    "great american alliance":              "Great American",
    # Munich Re
    "american alternative insurance":       "MunichRe",
    "munich reinsurance america":           "MunichRe",
    "munich re":                            "MunichRe",
    # Nationwide
    "freedom specialty insurance":          "Nationwide",
    "scottsdale insurance":                 "Nationwide",
    # Zurich
    "zurich american insurance":            "Zurich",
    "zurich select plus":                   "Zurich",
    # Hartford
    "hartford steam boiler":                "Hartford",
    "twin city fire insurance":             "Hartford",
    # Allianz
    "allianz global risks":                 "Allianz",
    "allianz global corporate":             "Allianz",
    # Starr
    "starr indemnity":                      "Starr",
    "starr surplus lines":                  "Starr",
    # Ironshore / Liberty
    "ironshore indemnity":                  "Ironshore",
    "ironshore specialty insurance":        "Ironshore",
    "ironshore insurance services":         "Ironshore",
    "liberty surplus insurance":            "Liberty Surplus",
    "liberty mutual insurance":             "Liberty Mutual",
    # Others
    "crum forster specialty":               "Crum & Forster",
    "crum and forster":                     "Crum & Forster",
    "ascot insurance company":              "Ascot Insurance Group",
    "coalition insurance solutions":        "Coalition",
    "old republic insurance":               "Old Republic",
    "hanover specialty industrial":         "Hanover",
    "the hanover insurance":                "Hanover",
    "affiliated fm insurance":              "AFM",
    "tt club mutual":                       "Thomas Miller - TT Club",
    "tt club":                              "Thomas Miller - TT Club",
    "international transport intermediaries": "Thomas Miller - TT Club",
    "underwriters at lloyds":               "Lloyds of London",
    "lloyds of london":                     "Lloyds of London",
    "certain underwriters at lloyds":       "Lloyds of London",
    "berkshire hathaway specialty":         "Berkshire Hathaway",
    "everest national":                     "Everest",
    "everest indemnity":                    "Everest",
    "everest reinsurance":                  "Everest",
    "navigators insurance":                 "Navigators",
    "arch specialty insurance":             "Arch",
    "aspen specialty insurance":            "Aspen Specialty",
    "qbe":                                  "QBE",
    "qbe insurance":                        "QBE",
    "qbe specialty insurance":              "QBE",
    "associated industries insurance":      "Amtrust",
    "travelers property casualty":          "Travelers",
    "travelers indemnity company of":       "Travelers",
    "markel american insurance":            "Markel",
    "markel insurance":                     "Markel",
    "homeland insurance company of new york": "Homeland",
    "insurance company of the state of pennsylvania": "AIG",
    "xl specialty insurance":               "AXA",
    "xl insurance":                         "AXA",
    "allied world":                         "Allied World Assurance Company",
    "allied world national assurance":      "Allied World Assurance Company",
    "axis insurance company":               "AXIS",
    "axis excess insurance":                "AXIS",
    "travelers indemnity":                  "Travelers",
    "endurance american insurance":         "Sompo",
    "endurance america":                    "Sompo",
    "sompo international":                  "Sompo",
    "national casualty company":            "Nationwide",
    "chubb custom insurance":               "Chubb",
    "agcs marine insurance":                "Allianz",
    "hartford fire insurance":              "Hartford",
    "starr aviation":                       "Starr",
}

# companymap contains a few dictionary-generic group names ("Insurance") that
# would otherwise match the boilerplate on every binder.
_GENERIC_CARRIER_KEYS = {
    "insurance", "insurance company", "company", "group", "national",
    "american", "the insurance", "insurance co", "mutual", "specialty",
    "indemnity", "casualty", "underwriters", "assurance", "select",
    "united", "james", "homeland", "reliance", "indiana", "century",
    "national liability", "pacific", "atlantic", "western", "midwest",
    "phoenix", "liberty", "franklin", "empire",
    # "ALL RISKS OF PHYSICAL LOSS OR DAMAGE" is policy language, not a market.
    "all risks",
}

# Short aliases are only trusted when they appear as standalone tokens.
_SHORT_CARRIER_TOKENS = {
    "aig", "cna", "qbe", "hsb", "ace", "afm", "chubb", "zurich", "starr",
    "allianz", "hanover", "sompo", "arch", "aspen", "axa", "everest",
    "travelers", "markel", "axis", "ironshore", "nationwide", "rli",
}

_carrier_gazetteer_cache: list[tuple[str, str]] | None = None


def _carrier_gazetteer() -> list[tuple[str, str]]:
    """[(normalised_alias, canonical_group)] sorted longest-alias-first."""
    global _carrier_gazetteer_cache
    if _carrier_gazetteer_cache is not None:
        return _carrier_gazetteer_cache

    entries: dict[str, str] = {}

    def add(alias: str, canonical: str) -> None:
        key = _norm(alias)
        if not key:
            return
        if key in _GENERIC_CARRIER_KEYS:
            return
        if len(key) < 4 and key not in _SHORT_CARRIER_TOKENS:
            return
        entries.setdefault(key, canonical)

    companymap = _load_companymap()
    for canonical, aliases in companymap.get("canonical_to_aliases", {}).items():
        add(canonical, canonical)
        for alias in aliases:
            # companymap aliases sometimes carry a parenthetical or a trailing
            # "(See also X)" note — index the bare name too.
            add(alias, canonical)
            add(re.sub(r"\s*[\(\-].*$", "", alias), canonical)

    for alias, canonical in _CARRIER_ENTITY_ALIASES.items():
        entries[_norm(alias)] = canonical

    _carrier_gazetteer_cache = sorted(
        entries.items(), key=lambda kv: len(kv[0]), reverse=True
    )
    return _carrier_gazetteer_cache


def _match_carrier(text: str) -> list[tuple[str, str, int]]:
    """
    Find carrier gazetteer hits in text.
    Returns [(canonical_group, matched_alias, position)] ordered by position.
    """
    if not text:
        return []
    normalised = _norm(text)
    if not normalised:
        return []
    hits: list[tuple[str, str, int]] = []
    claimed: list[tuple[int, int]] = []
    for alias, canonical in _carrier_gazetteer():
        for m in re.finditer(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", normalised):
            span = (m.start(), m.end())
            if any(span[0] >= s and span[1] <= e for s, e in claimed):
                continue
            claimed.append(span)
            hits.append((canonical, alias, m.start()))
    hits.sort(key=lambda hit: hit[2])
    return hits


# ---------------------------------------------------------------------------
# Client (insured) gazetteer
# ---------------------------------------------------------------------------

_client_gazetteer_cache: list[tuple[str, str, str]] | None = None


def _client_gazetteer() -> list[tuple[str, str, str]]:
    """[(normalised_name, display_name, folder_name)] sorted longest-first."""
    global _client_gazetteer_cache
    if _client_gazetteer_cache is not None:
        return _client_gazetteer_cache

    entries: dict[str, tuple[str, str]] = {}
    for client in _load_clients():
        display = client.get("display_name", "")
        folder = client.get("folder_name", "")
        names = [display] + list(client.get("aliases", []))
        for engagement in client.get("engagements", []):
            legal = engagement.get("legal_name", "")
            if legal:
                names.append(re.sub(r"\s*\(.*?\)\s*", " ", legal).strip())
        for name in names:
            key = _norm(name)
            if len(key) >= 3:
                entries.setdefault(key, (display, folder))

    _client_gazetteer_cache = sorted(
        ((k, v[0], v[1]) for k, v in entries.items()),
        key=lambda triple: len(triple[0]),
        reverse=True,
    )
    return _client_gazetteer_cache


def _match_client(text: str):
    """Return (display_name, folder_name, position) for the earliest client hit."""
    normalised = _norm(text)
    if not normalised:
        return None
    best = None
    for key, display, folder in _client_gazetteer():
        for m in re.finditer(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", normalised):
            # A carrier name can swallow a client alias: "Berkshire Hathaway
            # Specialty Insurance" contains a short client-gazetteer entry
            # belonging to an unrelated account. Skip hits that sit inside a
            # longer carrier match.
            window = normalised[m.start():m.start() + 70]
            covered = [alias for group, alias, pos in _match_carrier(window)
                       if pos == 0 and len(alias) > len(key)]
            if covered:
                continue
            if best is None or m.start() < best[2]:
                best = (display, folder, m.start())
            break
    return best


# ---------------------------------------------------------------------------
# Coverage term index
# ---------------------------------------------------------------------------

_SUPPLEMENTAL_COVERAGE_TERMS: dict[str, str] = {
    "fi bond": "Crime", "financial institution bond": "Crime",
    "fidelity bond": "Crime", "crime and fidelity": "Crime",
    "k and r": "Crime", "kidnap and ransom": "Crime",
    "management liability": "Directors & Officers",
    "finpro": "Directors & Officers", "side a": "Directors & Officers",
    "d and o": "Directors & Officers", "epack": "Directors & Officers",
    "investment management insurance": "Professional Liability",
    "imi": "Professional Liability",
    "architects and engineers": "Professional Liability",
    "a and e": "Professional Liability", "a e": "Professional Liability",
    "professional liability": "Professional Liability",
    "cyber tech": "Cyber", "tech e o": "Cyber", "cyber and multimedia": "Cyber",
    "multimedia liability": "Cyber", "network risk": "Cyber",
    "professional liability and network risk": "Cyber",
    "network security and privacy": "Cyber",
    "package": "Property", "mod pkg": "Property", "pkg": "Property",
    "equipment breakdown": "Property", "equipment insurance": "Property",
    "multi peril": "Property", "dic": "Property",
    "difference in conditions": "Property", "all risk property": "Property",
    "boiler and machinery": "Property", "storage tank": "Property",
    "rolling stock": "Inland Marine",
    "mail and transit": "Inland Marine", "mail floater": "Inland Marine",
    "installation floater": "Inland Marine",
    "glpl": "General Liability", "gl": "General Liability",
    "casualty": "General Liability", "foreign casualty": "General Liability",
    "environmental": "General Liability", "pollution": "General Liability",
    "aviation": "Aviation", "hull and liability": "Aviation",
    "aviation commercial general liability": "Aviation",
    "excess": "Excess", "follow form excess": "Excess",
    "umbrella": "Umbrella",
    "work comp": "Workers Compensation", "wc": "Workers Compensation",
    "workers comp": "Workers Compensation",
    "auto": "Automobile", "automobile": "Automobile",
    "marine cargo": "Ocean Marine", "cargo": "Ocean Marine",
    "charterers legal liability": "Marine",
    "fiduciary": "Fiduciary Liability",
    "epl": "Employment Practices Liability",
}

_coverage_index_cache: list[tuple[str, str]] | None = None


def _coverage_index() -> list[tuple[str, str]]:
    """[(normalised_term, category)] sorted longest-term-first."""
    global _coverage_index_cache
    if _coverage_index_cache is not None:
        return _coverage_index_cache

    approved = _load_coverages_md().get("categories", set())
    terms: dict[str, str] = {}

    # Bare "liability" sits inside Excess Liability, Umbrella Liability, Cyber
    # Liability and a dozen more — as a term of its own it only misleads.
    generic = {"liability", "insurance", "coverage", "policy", "binder"}

    def add(term: str, category: str) -> None:
        key = _norm(term)
        if key and key not in generic and category in approved:
            terms.setdefault(key, category)

    for term, category in _SUPPLEMENTAL_COVERAGE_TERMS.items():
        add(term, category)
    for term, category in _load_coverage_aliases().items():
        add(term, category)
    for term, category in _load_coverages_md().get("terms", {}).items():
        add(term, category)
    for term, category in _COVERAGE_MAP.items():
        add(term, category)
    for category in approved:
        add(category, category)

    _coverage_index_cache = sorted(
        terms.items(), key=lambda kv: len(kv[0]), reverse=True
    )
    return _coverage_index_cache


def _match_coverage(text: str):
    """Return (category, term_length) for the most specific term found."""
    normalised = _norm(text)
    if not normalised:
        return None
    for term, category in _coverage_index():
        if re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", normalised):
            return (category, len(term))
    return None


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

_MONTHS = {
    m: i + 1 for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun",
         "jul", "aug", "sep", "oct", "nov", "dec"])
}


def _normalize_date(value: str) -> str:
    """Return MM/DD/YYYY, or '' if the string is not a recognisable date."""
    value = _clean(value or "")
    if not value:
        return ""
    m = re.match(r"^(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{2,4})$", value)
    if m:
        month, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if year < 100:
            year += 2000
        if 1 <= month <= 12 and 1 <= day <= 31:
            return f"{month:02d}/{day:02d}/{year}"
        return ""
    m = re.match(r"^([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})$", value)
    if m and m.group(1)[:3].lower() in _MONTHS:
        return f"{_MONTHS[m.group(1)[:3].lower()]:02d}/{int(m.group(2)):02d}/{m.group(3)}"
    m = re.match(r"^(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})$", value)
    if m and m.group(2)[:3].lower() in _MONTHS:
        return f"{_MONTHS[m.group(2)[:3].lower()]:02d}/{int(m.group(1)):02d}/{m.group(3)}"
    return ""


def _dates_in(text: str) -> list[str]:
    return [d for d in (_normalize_date(m.group(0)) for m in _DATE_RE.finditer(text or "")) if d]


def _extract_dates_from_period(period_text: str) -> tuple[str, str]:
    dates = _dates_in(period_text)
    if len(dates) >= 2:
        return dates[0], dates[1]
    if len(dates) == 1:
        return dates[0], ""
    return "", ""


# ---------------------------------------------------------------------------
# Layout-aware label/value engine
#
# All PDF text is extracted with pdfplumber's layout mode, which preserves
# column alignment. That makes three value positions reachable with simple
# rules: same line after the label, the line below in the label's column, and
# the next non-empty line when the label owns its whole line.
# ---------------------------------------------------------------------------

_ALL_LABEL_WORDS = re.compile(
    r"^(?:policy|named|first|insured|insurer|insuring|issuing|carrier|company|"
    r"coverage|effective|expiration|period|term|binder|bond|certificate|"
    r"product|type|line|account|premium|commission|limit|address|producer|"
    r"broker|underwriter|date|renewal|previous|form|trigger|endorsement|item|"
    r"schedule|notice|class|total|attachment|interest|state|agent|issue|"
    r"billing|payment|quote|submission|surcharge|deductible|retention|"
    r"subject|applicant|writing|contact|email|phone|name|title|number|amount|"
    r"premises|location|choice|declarations|table|section|page)"
    r"\b[\w\s/&#\.\-\(\)]{0,45}:?\s*$",
    re.IGNORECASE,
)


def _looks_like_label(text: str) -> bool:
    text = _clean(text)
    if not text or len(text) > 60:
        return False
    return bool(_ALL_LABEL_WORDS.match(text))


def _label_hits(lines: list[str], labels, allow_qualifier: bool = False):
    """
    Yield (line_index, match_start, match_end, score, line) for every label
    occurrence. A hit requires a ':' after the label, or the label sitting at
    the start of its line / after a run of two or more spaces — that keeps
    label words from matching inside prose.
    """
    for entry in labels:
        pattern, score = entry[0], entry[1]
        # Phrase labels ("...Program for", "...Binder for") read naturally
        # mid-sentence, so they are exempt from the anchoring rule.
        free = len(entry) > 2 and entry[2]
        for idx, line in enumerate(lines):
            for m in re.finditer(
                rf"(?<![A-Za-z])(?:{pattern})(?:\(s\))?(\s*[:\-–])?[ \t\.]*",
                line, re.IGNORECASE
            ):
                start = m.start()
                before = line[:start].strip()
                anchored = (start == 0
                            or not before
                            or line[max(0, start - 2):start] == "  "
                            # A single qualifier word or an item enumerator
                            # ahead of the label: "Marine Policy No.",
                            # "A. POLICY TERM", "ITEM 4. POLICY PERIOD".
                            # Only for labels where that form is real — it
                            # would otherwise match inside carrier names in
                            # tables ("Chaucer Insurance Company DAC").
                            or (allow_qualifier and bool(re.fullmatch(
                                r"(?:ITEM\s*)?[A-Z0-9]{1,4}\.?|[A-Z][A-Za-z&/]{1,12}",
                                before, re.IGNORECASE))))
                if not (free or m.group(1) or anchored):
                    continue
                yield idx, start, m.end(), score, line


def _same_line_value(line: str, end: int, max_chars: int = 160) -> str:
    tail = line[end:].lstrip(" \t:.-–")
    if not tail.strip():
        return ""
    # Layout mode separates columns with runs of spaces; a run of 4+ spaces
    # means we have crossed into the next column.
    value = re.split(r"\s{4,}", tail)[0].strip()
    return value[:max_chars].strip()


def _below_value(lines: list[str], idx: int, col: int, max_chars: int = 160,
                 whole_line: bool = False) -> str:
    """
    Value printed underneath the label. When the label shares its line with
    other labels (a header row) the value sits in the label's own column;
    when the label owns its whole line the value below is the whole line.
    """
    for j in range(idx + 1, min(idx + 4, len(lines))):
        line = lines[j]
        if not line.strip():
            continue
        if whole_line:
            candidate = re.split(r"\s{4,}", line.strip())[0].strip()
        else:
            # Never slice a word in half: back up to the start of the token
            # the label column lands in.
            start = min(col, len(line))
            while 0 < start < len(line) and line[start - 1] != " ":
                start -= 1
            segment = line[start:]
            candidate = re.split(r"\s{4,}", segment.strip())[0].strip() if segment.strip() else ""
            if not candidate:
                candidate = re.split(r"\s{4,}", line.strip())[0].strip()
        if candidate and not _looks_like_label(candidate):
            return candidate[:max_chars]
        return ""
    return ""


def _label_owns_line(line: str, end: int) -> bool:
    """True when nothing but whitespace follows the label on its own line."""
    return not line[end:].strip()


def _above_value(lines: list[str], idx: int, max_chars: int = 160) -> str:
    """
    Some forms set the value on the line above its label (the label prints on
    the lower baseline of the field box). Only consulted as a last resort.
    """
    if idx == 0:
        return ""
    line = lines[idx - 1]
    if not line.strip():
        return ""
    candidate = re.split(r"\s{6,}", line.strip())[0].strip()
    if candidate and not _looks_like_label(candidate):
        return candidate[:max_chars]
    return ""


def _resolve_label_values(lines: list[str], idx: int, start: int, end: int,
                          max_chars: int) -> list[tuple[str, str]]:
    """
    Every place the value for a label hit could sit: on the same line, in the
    label's column below, and — when the label sits alone on its line — on the
    line above. All are returned as scored candidates; the per-field validators
    decide which one is real.
    """
    line = lines[idx]
    out: list[tuple[str, str]] = []

    same = _same_line_value(line, end, max_chars)
    if same and not _looks_like_label(same):
        out.append((same, "same_line"))

    owns = _label_owns_line(line, end)
    below = _below_value(lines, idx, start, max_chars, whole_line=owns)
    if below:
        out.append((below, "below"))

    if owns:
        above = _above_value(lines, idx, max_chars)
        if above:
            out.append((above, "above"))

    return out


_VALUE_SOURCE_PENALTY = {"same_line": 0, "below": -3, "above": -8, "": 0}


def _label_values(lines: list[str], labels, max_chars: int = 160):
    """
    Yield (value, score, line_index, line) for each label hit, resolving the
    value from the same line, the label's column below, or the next line.
    """
    for idx, start, end, score, line in _label_hits(lines, labels):
        for value, source in _resolve_label_values(lines, idx, start, end, max_chars):
            yield value, score + _VALUE_SOURCE_PENALTY[source], idx, line


def _context_before(line: str, start: int, window: int = 45) -> str:
    return line[max(0, start - window):start]


def _block_context(lines: list[str], idx: int, lookback: int = 3) -> str:
    """
    The preceding non-empty lines. Declarations pages introduce underlying and
    followed policies as a block — the disqualifying word sits a line or two
    above the label, not on it.
    """
    out: list[str] = []
    j = idx - 1
    while j >= 0 and len(out) < lookback:
        if lines[j].strip():
            out.append(lines[j])
        j -= 1
    return " ".join(out)


# ---------------------------------------------------------------------------
# Policy number
# ---------------------------------------------------------------------------

_ID_TOKEN = re.compile(r"^[/#]?[A-Za-z0-9][A-Za-z0-9\-/\.]*$")
_ID_STOPWORDS = {
    "BINDER", "NEW", "TBD", "NA", "N/A", "NONE", "PENDING", "RENEWAL",
    "POLICY", "NUMBER", "TO", "FROM", "AND", "THE", "OF", "SEE",
}


def _id_from_span(span: str) -> str:
    """Pull the leading identifier out of a captured value span."""
    span = _clean(span).strip(" :–-\t")
    # Chubb prefixes the policy year: "(26)7362-22-95".
    span, stripped = re.subn(r"^\(\d{2}\)\s*", "", span)
    out: list[str] = []
    seen_year = bool(stripped)   # a stripped leading year still counts as seen
    for token in span.split():
        token = token.strip(",;")
        if not token:
            break
        # A parenthesised two-digit year sits inside the number rather than
        # ending it — Liberty writes "ECO (26) 69956991". Keep it verbatim,
        # but a *second* marker means the expiring policy has started:
        # "(27) 7183-49-23 (26) 7183-49-23" is two policies, not one.
        if re.fullmatch(r"\(\d{2}\)", token):
            if seen_year:
                break
            seen_year = True
            out.append(token)
            continue
        if token in {"-", "–"}:
            if out:
                out.append("-")
                continue
            break
        if not _ID_TOKEN.match(token):
            break
        if token.upper() in _ID_STOPWORDS:
            break
        # A lower-case word is prose, not part of an identifier.
        if re.fullmatch(r"[A-Za-z][a-z]+", token):
            break
        # An abbreviated word ("W.", "St.") means we have wandered into an
        # address, not a policy number.
        if re.fullmatch(r"[A-Za-z]{1,2}\.", token):
            break
        if len(token) > 28:
            break
        out.append(token)
        if len(" ".join(out)) > 34:
            break
    while out and out[-1] == "-":
        out.pop()
    return " ".join(out).strip()


def _looks_like_policy_number(value: str) -> bool:
    value = _clean(value)
    if not value or len(value) < 3 or len(value) > 40:
        return False
    if len(re.findall(r"\d", value)) < 2:
        return False
    if _normalize_date(value):
        return False
    # Money, not an identifier. Plain digit runs stay eligible — plenty of
    # carriers issue all-numeric policy numbers.
    if "$" in value or re.fullmatch(r"[\d,]+\.\d+|\d{1,3}(?:,\d{3})+|\d+\s*[mMkK]", value):
        return False
    if re.fullmatch(r"\(?\d{3}\)?[\s\-]\d{3}[\s\-]\d{4}", value):       # phone
        return False
    if re.fullmatch(r"\d{5}(?:-\d{4})?", value):                        # zip
        return False
    if re.search(r"(?:insurance|company|binder|premium|aggregate|limit|broker)",
                 value, re.IGNORECASE):
        return False
    return True


# An identifier dense enough to be a policy number on sight: letters and
# digits mixed, or a long digit run, with no surrounding prose.
_NEARBY_ID = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Z]{2,6}[\- ]?)?[A-Z0-9]*\d[A-Z0-9]{4,}(?:-[A-Z0-9]+)*(?![A-Za-z0-9])")
_STATE_ZIP = re.compile(r"^[A-Z]{2}[\s\-]\d{5}(?:-\d{4})?$")


def _best_nearby_id(line: str) -> str:
    """Longest identifier-shaped token on a line, ignoring 'IL 60606' pairs."""
    best = ""
    for m in _NEARBY_ID.finditer(line):
        value = _clean(m.group(0))
        if _STATE_ZIP.match(value) or len(re.sub(r"[^A-Z0-9]", "", value)) < 8:
            continue
        if _looks_like_policy_number(value) and len(value) > len(best):
            best = value
    return best


def _policy_number_candidates(pages: list[dict], filename: str) -> list[dict]:
    candidates: list[dict] = []
    for page in pages:
        lines = page["lines"]
        for idx, start, end, score, line in _label_hits(lines, _PN_LABELS,
                                                        allow_qualifier=True):
            if _PN_NEGATIVE_CONTEXT.search(_context_before(line, start)):
                continue
            penalty = 25 if _PN_NEGATIVE_CONTEXT.search(_block_context(lines, idx)) else 0
            found = False
            for span, source in _resolve_label_values(lines, idx, start, end, 60):
                value = _id_from_span(span)
                if not _looks_like_policy_number(value):
                    continue
                found = True
                candidates.append({
                    "value": value,
                    "score": score + page["score"] - penalty
                             + _VALUE_SOURCE_PENALTY[source],
                    "source": f"label_{source}",
                })
            if found:
                continue
            # Right-aligned forms park the value in a column the label never
            # touches. Fall back to the nearest identifier-shaped token, scored
            # well below a properly located value.
            for near in range(max(0, idx - 1), min(idx + 3, len(lines))):
                nearby = _best_nearby_id(lines[near])
                if nearby:
                    candidates.append({
                        "value": nearby,
                        "score": score + page["score"] - penalty - 32,
                        "source": "label_nearby",
                    })
                    break

    if filename:
        stem = Path(filename).stem
        pattern = r"\b([A-Z]{2,6}\d{4,}[A-Z0-9\-]*|[A-Z]{1,4}\d{2,}-[A-Z0-9\-]+)\b"
        for m in re.finditer(pattern, stem):
            value = _id_from_span(m.group(0))
            if _looks_like_policy_number(value):
                candidates.append({"value": value, "score": 55, "source": "filename"})

    return candidates


def _choose_policy_number(candidates: list[dict]) -> str:
    tally: dict[str, int] = {}
    for candidate in candidates:
        tally[candidate["value"]] = tally.get(candidate["value"], 0) + 1
    best_value, best_score = "", -1
    for candidate in candidates:
        score = candidate["score"] + min(tally[candidate["value"]], 4) * 2
        score += min(len(re.findall(r"\d", candidate["value"])), 12)
        if re.search(r"[A-Za-z]", candidate["value"]):
            score += 3
        if score > best_score:
            best_score, best_value = score, candidate["value"]
    return best_value


# ---------------------------------------------------------------------------
# Carrier
# ---------------------------------------------------------------------------

# Large retail/wholesale brokerages. A loss run often carries the broker's name in
# the same region of the page as the carrier's, so these are disqualified as carrier
# candidates. All are publicly traded or widely known firms; this is detection logic
# over third-party documents, not a statement of affiliation.
_BROKER_NAMES = re.compile(
    r"(?:marsh|aon|willis|gallagher|lockton|amwins|crc insurance|"
    r"risk specialists companies|brown\s*&\s*brown)",
    re.IGNORECASE,
)
# "Followed Policy Number:" scopes a policy *number*, not a carrier, so it must
# not disqualify the carrier named on the next line.
_POLICY_NUMBER_PHRASE = re.compile(
    r"(?:followed|underlying|lead|prior|expiring|renewal\s+of)\s+polic\w*\s*"
    r"(?:number|no\.?|#)", re.IGNORECASE)


def _carrier_block_context(lines: list[str], idx: int) -> str:
    return _POLICY_NUMBER_PHRASE.sub(" ", _block_context(lines, idx))


_CARRIER_ENTITY_WORDS = re.compile(
    r"(?:insurance|indemnity|assurance|casualty|underwriters|specialty|"
    r"mutual|company|corporation|syndicate)", re.IGNORECASE)


def _carrier_candidates(pages: list[dict], filename: str) -> list[dict]:
    candidates: list[dict] = []

    for page in pages:
        lines = page["lines"]
        # A page naming three or more different carriers under carrier labels is
        # a schedule of underlying/followed policies, not a declarations page.
        named = {_norm(v) for v in
                 (_same_line_value(ln, e, 90)
                  for _i, _s, e, _sc, ln in _label_hits(lines, _CARRIER_LABELS))
                 if v and (_match_carrier(v) or _CARRIER_ENTITY_WORDS.search(v))}
        schedule_penalty = 30 if len(named) >= 3 else 0
        for idx, start, end, score, line in _label_hits(lines, _CARRIER_LABELS):
            if _CARRIER_NEGATIVE_CONTEXT.search(_context_before(line, start)):
                continue
            for span, source in _resolve_label_values(lines, idx, start, end, 90):
                if _BROKER_NAMES.search(span):
                    continue
                hits = _match_carrier(span)
                base = score + page["score"] + _VALUE_SOURCE_PENALTY[source] - schedule_penalty
                if _CARRIER_NEGATIVE_CONTEXT.search(_carrier_block_context(lines, idx)):
                    base -= 35
                if hits:
                    candidates.append({"group": hits[0][0], "raw": span,
                                       "score": base, "source": f"label_{source}"})
                elif len(span.split()) >= 2 and _CARRIER_ENTITY_WORDS.search(span):
                    candidates.append({"group": "", "raw": span,
                                       "score": base - 25, "source": "label_unmapped"})

    if filename:
        for group, alias, _pos in _match_carrier(Path(filename).stem):
            candidates.append({"group": group, "raw": alias.title(),
                               "score": 78, "source": "filename"})

    # Lloyd's placements name syndicates, never a single carrier — but they
    # always carry a "B<nnnn>..." broker contract reference. The reference and
    # the corroborating language often sit on different pages of the slip.
    corpus = "\n".join(page["text"] for page in pages)
    if re.search(r"\bB\d{4}[A-Z0-9]{6,}\b", corpus) and re.search(
            r"syndicate|surplus\s+line|lineslip|LMA\d{4}|lloyd|"
            r"confirmation\s+of\s+cover", corpus, re.IGNORECASE):
        candidates.append({"group": "Lloyds of London", "raw": "Lloyd's syndicates",
                           "score": 84, "source": "lloyds_reference"})

    # Free-text sweep of the best pages — catches letterhead-only carriers.
    for page in pages:
        normalised = _norm(page["text"])
        for group, alias, position in _match_carrier(page["text"])[:6]:
            # A one-word alias ("United", "Homeland") is only a carrier when a
            # carrier word follows it; multi-word aliases speak for themselves.
            if " " not in alias and alias not in _SHORT_CARRIER_TOKENS:
                following = normalised[position + len(alias):position + len(alias) + 34]
                if not _CARRIER_ENTITY_WORDS.search(following):
                    continue
            # Quota-share and underlying-insurance tables list carrier after
            # carrier under a single heading, so the disqualifying words can be
            # several rows back.
            penalty = 40 if _CARRIER_NEGATIVE_CONTEXT.search(
                normalised[max(0, position - 220):position]) else 0
            candidates.append({
                "group": group, "raw": alias.title(),
                "score": 58 + page["score"] - penalty - min(position // 400, 8),
                "source": "freetext",
            })

    return candidates


def _choose_carrier(candidates: list[dict]) -> tuple[str, str]:
    """Return (carrier_group, carrier_raw)."""
    grouped: dict[str, dict] = {}
    for candidate in candidates:
        key = candidate["group"] or f"__raw__{_norm(candidate['raw'])}"
        entry = grouped.setdefault(key, {"support": 0.0, "best": -1,
                                         "raw": candidate["raw"],
                                         "group": candidate["group"]})
        entry["support"] += max(candidate["score"], 0) * 0.15
        if candidate["score"] > entry["best"]:
            entry["best"] = candidate["score"]
            if candidate["source"] != "freetext":
                entry["raw"] = candidate["raw"]
    if not grouped:
        return "", ""
    # Repetition corroborates but cannot outweigh a decisive hit: an underlying
    # carrier named in three table rows must not beat the issuing company.
    winner = max(grouped.values(),
                 key=lambda entry: entry["best"] + min(entry["support"], 20))
    return winner["group"], winner["raw"]


# ---------------------------------------------------------------------------
# Insured
# ---------------------------------------------------------------------------

_INSURED_TRAILERS = re.compile(
    r"\s+(?:and\s+as\s+defined|and\s+all\s+their|and\s+its\s+wholly|"
    r"and\s+subsidiaries|in\s+care\s+of|c/o|attn\b).*$",
    re.IGNORECASE,
)
_ORG_SUFFIX = re.compile(
    r"\b(?:LLC|L\.L\.C|INC|CORP|CORPORATION|COMPANY|CO|LTD|LP|L\.P|LLP|PLC|"
    r"HOLDINGS|GROUP|PARTNERS|TRUST|COOPERATIVE|ASSOCIATION|GMBH|BV)\b\.?",
    re.IGNORECASE,
)
_ADDRESS_HINT = re.compile(
    r"\b(?:street|st\.|road|rd\.|avenue|ave\.|suite|ste\.|floor|drive|"
    r"blvd|boulevard|lane|p\.?o\.? box)\b|\b\d{5}(?:-\d{4})?\b", re.IGNORECASE)


def _clean_insured(value: str) -> str:
    # Layout mode pads inside centred text, so only a wide gap is a real
    # column break; everything narrower collapses to a single space.
    value = re.split(r"\s{6,}", _clean(value))[0].strip()
    value = re.sub(r"\s+", " ", value)
    value = _INSURED_TRAILERS.sub("", value)
    value = re.sub(r"\s*–.*$", "", value)
    return value.strip(" ,;:-")


_INSURED_REJECT_CHARS = re.compile(r"[;(){}\[\]<>@]")
# Endorsement headings ("Additional Insured – Where Required...") carry insured
# labels but never name the insured.
_INSURED_NEGATIVE_CONTEXT = re.compile(
    r"\b(?:additional|unnamed|other|each|any|all|schedule\s+of|definition|"
    r"endorsement|exclusion)\s+(?:named\s+)?insured", re.IGNORECASE)
_INSURED_BAD_LEAD = re.compile(
    r"^(?:where|when|which|that|as|if|under|per|any|all|each|other|additional|"
    r"including|subject|no|not|see|refer)\b", re.IGNORECASE)


def _valid_insured(value: str) -> bool:
    if not value or len(value) < 5 or len(value) > 90:
        return False
    if not re.match(r"^[A-Z0-9]", value):          # org names start capitalised
        return False
    if _INSURED_REJECT_CHARS.search(value):
        return False
    if _BROKER_NAMES.search(value) or _ADDRESS_HINT.search(value):
        return False
    if _looks_like_label(value):
        return False
    if re.match(r"^(?:and|of|by|for|to|the|this|we|please)\b", value, re.IGNORECASE):
        return False
    if _INSURED_BAD_LEAD.match(value):
        return False
    if len(re.sub(r"[^A-Za-z]", "", value)) < 6:
        return False
    # An insurer named where the insured belongs is a mis-read of an
    # underlying-carrier line, not a client.
    carriers = _match_carrier(value)
    if carriers and carriers[0][2] == 0 and _CARRIER_ENTITY_WORDS.search(value):
        return False
    if _ORG_SUFFIX.search(value):
        return True
    # No corporate suffix, so it has to read like a proper-noun name. Lower-case
    # connectives mean it is a sentence fragment from a heading, not a company:
    # "Cancellation of Local Underlying Policies".
    if re.search(r"\s(?:of|and|the|for|to|in|on|with|under|by)\s", value):
        return False
    return len([w for w in re.split(r"[\s,]+", value) if len(w) >= 3]) >= 2


def _insured_candidates(pages: list[dict], filename: str) -> list[dict]:
    candidates: list[dict] = []

    for page in pages:
        for value, score, idx, line in _label_values(page["lines"], _INSURED_LABELS, 120):
            if _INSURED_NEGATIVE_CONTEXT.search(line):
                continue
            value = _clean_insured(value)
            if not _valid_insured(value):
                continue
            # A labelled value that also names a known client is the strongest
            # signal there is — it beats a bare gazetteer hit elsewhere.
            bonus = 25 if _match_client(value) else 0
            candidates.append({"value": value, "score": score + page["score"] + bonus,
                               "source": "label"})

    # A name from clients.json appearing anywhere in the document is about as
    # strong as evidence gets — these are the only insureds we ever request
    # loss runs for.
    for page in pages:
        hit = _match_client(page["text"])
        if hit:
            candidates.append({"value": hit[0], "score": 96 + page["score"],
                               "source": "client_gazetteer"})

    hit = _match_client(Path(filename).stem) if filename else None
    if hit:
        candidates.append({"value": hit[0], "score": 100, "source": "filename_client"})

    return candidates


def _choose_insured(candidates: list[dict]) -> str:
    tally: dict[str, int] = {}
    for candidate in candidates:
        key = _norm(candidate["value"])
        tally[key] = tally.get(key, 0) + 1
    best_value, best_score = "", -1
    for candidate in candidates:
        score = candidate["score"] + min(tally[_norm(candidate["value"])], 4) * 3
        if score > best_score:
            best_score, best_value = score, candidate["value"]
    return best_value


# ---------------------------------------------------------------------------
# Effective date
# ---------------------------------------------------------------------------

def _filename_dates(filename: str) -> list[str]:
    """Binder filenames usually lead with the effective date: '3.28.25-4.1.26'."""
    m = re.match(r"^\s*(\d{1,2})[\.\-/](\d{1,2})[\.\-/](\d{2,4})", Path(filename).stem)
    if not m:
        return []
    month, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if year < 100:
        year += 2000
    if 1 <= month <= 12 and 1 <= day <= 31:
        return [f"{month:02d}/{day:02d}/{year}"]
    return []


def _effective_date_candidates(pages: list[dict], filename: str) -> list[dict]:
    candidates: list[dict] = []

    for page in pages:
        lines = page["lines"]
        for idx, start, end, score, line in _label_hits(lines, _EFF_LABELS,
                                                        allow_qualifier=True):
            if _EFF_NEGATIVE_CONTEXT.search(_context_before(line, start)):
                continue
            if re.search(r"expir|binder\s*period", line[start:end], re.IGNORECASE):
                continue
            dates = _dates_in(line[end:end + 160])
            source = "same_line"
            if not dates:
                for j in range(idx + 1, min(idx + 3, len(lines))):
                    dates = _dates_in(lines[j])
                    if dates:
                        source = "below"
                        break
            if not dates and idx and _label_owns_line(line, end):
                dates = _dates_in(lines[idx - 1])
                source = "above"
            if not dates:
                continue
            candidates.append({
                "value": dates[0],
                "score": score + page["score"] + _VALUE_SOURCE_PENALTY[source],
                "source": source,
            })

    for value in _filename_dates(filename):
        candidates.append({"value": value, "score": 90, "source": "filename"})

    return candidates


def _choose_effective_date(candidates: list[dict]) -> str:
    tally: dict[str, int] = {}
    for candidate in candidates:
        tally[candidate["value"]] = tally.get(candidate["value"], 0) + 1
    best_value, best_score = "", -1
    for candidate in candidates:
        score = candidate["score"] + min(tally[candidate["value"]], 5) * 4
        if score > best_score:
            best_score, best_value = score, candidate["value"]
    return best_value


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------

_COVERAGE_PHRASE = re.compile(
    r"(?:binder|coverage|policy|proposal)\s+(?:of|for)\s+([A-Z][^\n\.]{2,60})",
)


def _coverage_candidates(pages: list[dict], filename: str) -> list[dict]:
    candidates: list[dict] = []

    # Binder filenames name the line of business far more reliably than the
    # marketing copy inside the document, so they lead.
    if filename:
        hit = _match_coverage(re.sub(r"[_\-]+", " ", Path(filename).stem))
        if hit:
            candidates.append({"value": hit[0], "score": 120 + hit[1], "source": "filename"})

    for page in pages:
        for value, score, _idx, _line in _label_values(page["lines"], _COVERAGE_LABELS, 70):
            hit = _match_coverage(value)
            if hit:
                candidates.append({"value": hit[0], "score": score + page["score"] + hit[1],
                                   "source": "label"})
        # Title lines ("INLAND MARINE BINDER", "Excess/Excess Umbrella").
        for line in page["lines"][:25]:
            stripped = _clean(line)
            if 4 <= len(stripped) <= 60 and re.search(
                    r"binder|policy|insurance|coverage", stripped, re.IGNORECASE):
                hit = _match_coverage(stripped)
                if hit:
                    candidates.append({"value": hit[0], "score": 55 + page["score"] + hit[1],
                                       "source": "title"})
        # "...our binder for Property-Multi Peril being offered with..."
        for m in _COVERAGE_PHRASE.finditer(page["text"]):
            hit = _match_coverage(m.group(1))
            if hit:
                candidates.append({"value": hit[0], "score": 60 + page["score"] + hit[1],
                                   "source": "phrase"})

    return candidates


def _choose_coverage(candidates: list[dict]) -> str:
    best: dict[str, float] = {}
    support: dict[str, float] = {}
    for candidate in candidates:
        value = candidate["value"]
        best[value] = max(best.get(value, 0), candidate["score"])
        support[value] = support.get(value, 0) + candidate["score"] * 0.1
    if not best:
        return ""
    # Repetition is corroboration, not proof: cap it so boilerplate mentioning
    # "excess liability" a dozen times cannot outvote a decisive signal.
    return max(best, key=lambda value: best[value] + min(support[value], 20))


# ---------------------------------------------------------------------------
# Text layer
# ---------------------------------------------------------------------------

_GARBLE_RE = re.compile(r"/C[0-9A-Fa-f]{2}")
_COMMON_WORDS = {"the", "of", "and", "policy", "insurance", "to", "for",
                 "company", "in", "is", "this", "date", "coverage", "number"}


def _looks_garbled(text: str) -> bool:
    if not text or len(text) < 40:
        return True
    if _GARBLE_RE.search(text):
        return True
    words = re.findall(r"[A-Za-z]{2,}", text)
    if len(words) < 8:
        return True
    return not any(w.lower() in _COMMON_WORDS for w in words)


def _shift_decode(text: str, shift: int) -> str:
    return "".join(
        chr(ord(c) - shift) if 32 <= ord(c) - shift < 127 else c for c in text
    )


def _repair_text(text: str) -> str:
    """Some PDFs ship a custom font encoding; text comes out uniformly shifted."""
    if not _looks_garbled(text):
        return text
    for shift in (3, -3, 1, -1):
        candidate = _shift_decode(text, shift)
        if not _looks_garbled(candidate):
            return candidate
    return text


_DECL_SIGNALS = [
    (re.compile(r"policy\s*(?:symbol\s*and\s*)?(?:number|no\.?|#)", re.I), 4),
    (re.compile(r"(?:first\s*)?named\s*insured|insured\s*name|name\s*insured|"
                r"policyholder|assured", re.I), 3),
    (re.compile(r"policy\s*period|policy\s*term|effective\s*dates?", re.I), 3),
    (re.compile(r"insuring\s*company|issuing\s*company|insurance\s*carrier|"
                r"insurance\s*company|name\s*of\s*company|insurer", re.I), 3),
    (re.compile(r"\bbinder\b|declarations|confirmation\s+of\s+cover|"
                r"certificate\s+of\s+entry", re.I), 2),
    (re.compile(r"period\s+of\s+insurance|policy\s*no\b|bond\s*(?:number|no)|"
                r"unique\s+market\s+reference|\bumr\b|^\s*period\s*:", re.I | re.M), 2),
    (re.compile(r"limit\s*of\s*(?:liability|insurance)|premium", re.I), 1),
]


def _score_page_for_declarations(text: str) -> int:
    return sum(weight for pattern, weight in _DECL_SIGNALS if pattern.search(text))


# A page scoring this high is unmistakably a declarations/binder page; once two
# of them are in hand there is nothing to gain from reading the rest of a
# 250-page policy.
_DECL_SCORE_SATISFIED = 13


def _pdf_page_texts(file_bytes: bytes, max_pages: int, early_stop: bool = True) -> list[str]:
    """Cheap first pass: pypdf text, repaired if a broken font map mangled it."""
    try:
        from pypdf import PdfReader
    except ImportError:
        return []
    try:
        reader = PdfReader(io.BytesIO(file_bytes))
    except Exception:
        return []
    out: list[str] = []
    strong = 0
    for page in reader.pages[:max_pages]:
        try:
            text = _repair_text(page.extract_text() or "")
        except Exception:
            text = ""
        out.append(text)
        if early_stop and text.strip():
            if _score_page_for_declarations(text) >= _DECL_SCORE_SATISFIED:
                strong += 1
                if strong >= 2 and len(out) >= 3:
                    break
    return out


def _pdf_layout_text(file_bytes: bytes, page_indexes: list[int]) -> dict:
    """Layout-preserving text for selected pages — the extraction workhorse."""
    try:
        import pdfplumber
    except ImportError:
        return {}
    out: dict = {}
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for idx in page_indexes:
                if idx >= len(pdf.pages):
                    continue
                try:
                    text = pdf.pages[idx].extract_text(layout=True) or ""
                except Exception:
                    try:
                        text = pdf.pages[idx].extract_text() or ""
                    except Exception:
                        text = ""
                out[idx] = _repair_text(text)
    except Exception:
        return out
    return out


def _make_page(text: str, index: int, score: int) -> dict:
    return {"index": index, "text": text, "lines": text.split("\n"), "score": score}


def _select_pages(file_bytes: bytes, full_scan: bool) -> list[dict]:
    """
    Rank pages by declarations-likeness using cheap pypdf text, then re-extract
    the top pages in layout mode. Returns pages ordered best-first.
    """
    cheap = _pdf_page_texts(file_bytes, 2000 if full_scan else 30,
                            early_stop=not full_scan)

    scored = [(_score_page_for_declarations(text), idx) for idx, text in enumerate(cheap)]

    # Pages with no pypdf text still deserve a look — pdfplumber often reads
    # them fine — so keep the first few blank ones in the running.
    blanks = [idx for idx, text in enumerate(cheap) if not text.strip()]
    for idx in blanks[:4]:
        scored[idx] = (2, idx)

    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    # The opening pages carry the cover letter — insured, carrier and the
    # coverage description live there even when they score no label keywords —
    # so they get reserved slots instead of competing on score.
    opening = [idx for idx in (0, 1) if idx < max(len(cheap), 2)]
    ranked = [idx for score, idx in scored if score > 0 and idx not in opening][:4]

    layout = _pdf_layout_text(file_bytes, opening + ranked)

    pages: list[dict] = []
    for idx in opening + ranked:
        text = layout.get(idx, "")
        if not _clean(text) and idx < len(cheap):
            text = cheap[idx]
        if _clean(text):
            pages.append(_make_page(text, idx, _score_page_for_declarations(text)))

    pages.sort(key=lambda page: (-page["score"], page["index"]))
    return pages


# ---------------------------------------------------------------------------
# Field assembly
# ---------------------------------------------------------------------------

def _extract_fields(pages: list[dict], filename: str) -> dict:
    policy_number = _choose_policy_number(_policy_number_candidates(pages, filename))
    carrier_group, carrier_raw = _choose_carrier(_carrier_candidates(pages, filename))
    insured = _choose_insured(_insured_candidates(pages, filename))
    effective_date = _choose_effective_date(_effective_date_candidates(pages, filename))
    coverage = _choose_coverage(_coverage_candidates(pages, filename))

    expiration_date = ""
    for page in pages:
        for value, _score, _idx, _line in _label_values(page["lines"], _EFF_LABELS, 160):
            dates = _dates_in(value)
            if len(dates) >= 2 and dates[0] == effective_date:
                expiration_date = dates[1]
                break
        if expiration_date:
            break

    period = " to ".join(d for d in (effective_date, expiration_date) if d)

    return {
        "policy_number":     policy_number,
        "carrier":           carrier_raw,
        "carrier_group":     carrier_group,
        "insured":           insured,
        "effective_date":    effective_date,
        "expiration_date":   expiration_date,
        "policy_period":     period,
        "coverage_raw":      coverage,
        "coverage_category": coverage,
        "_decl_page":        pages[0]["index"] if pages else -1,
    }


def _sanitize_extracted_value(val: Optional[str], field: str = "") -> str:
    """
    The engine already validates per field; this only normalises whitespace and
    re-checks the couple of shapes that must never reach the UI.
    """
    value = _clean(val or "")
    if not value:
        return ""
    if field == "policy_number" and not _looks_like_policy_number(value):
        return ""
    if field == "effective_date" and not _normalize_date(value):
        return ""
    return value


def _parse_filename_hints(filename: str) -> dict:
    """Filename-only hints, kept for callers outside the engine."""
    hints: dict = {}
    stem = Path(filename).stem
    dates = _filename_dates(filename)
    if dates:
        hints["effective_date"] = dates[0]
        hints["effective_year"] = int(dates[0][-4:])
    coverage = _match_coverage(re.sub(r"[_\-]+", " ", stem))
    if coverage:
        hints["coverage_raw"] = coverage[0]
    carriers = _match_carrier(stem)
    if carriers:
        hints["carrier_raw"] = carriers[0][0]
    client = _match_client(stem)
    if client:
        hints["insured"] = client[0]
    return hints


# ---------------------------------------------------------------------------
# Per-format entry points
# ---------------------------------------------------------------------------

def _extract_from_pdf(file_bytes: bytes, filename: str = "", full_scan: bool = False) -> dict:
    try:
        import pypdf  # noqa: F401
    except ImportError:
        return {"_error": "pypdf not installed. Run: pip install pypdf"}
    try:
        pages = _select_pages(file_bytes, full_scan)
    except Exception as exc:
        return {"_error": f"Could not read PDF: {exc}"}
    if not pages:
        # Scanned/image-only PDF. The filename still names the client, carrier
        # and line of business often enough to be worth returning.
        fields = _extract_fields([], filename)
        fields["extract_note"] = (
            "No selectable text in this PDF (likely a scan) — fields below come "
            "from the filename only."
        )
        return fields
    return _extract_fields(pages, filename)


def _extract_from_docx(file_bytes: bytes, filename: str = "") -> dict:
    try:
        from docx import Document
    except ImportError:
        return {"_error": "python-docx not installed. Run: pip install python-docx"}
    try:
        document = Document(io.BytesIO(file_bytes))
    except Exception as exc:
        return {"_error": f"Could not read Word document: {exc}"}

    body = "\n".join(p.text for p in document.paragraphs if p.text.strip())

    table_lines: list[str] = []
    for table in document.tables:
        # A cell can stack several values on separate lines (one policy number
        # per line). Join with the column separator so the label engine reads
        # only the first, rather than gluing them into one bogus identifier.
        rows = [[_clean(c.text.replace("\n", "    ")) for c in row.cells]
                for row in table.rows]
        header = rows[0] if rows else []
        # A header row of short label-ish cells means the table is read by
        # column: pair each header with the cell underneath it. Otherwise the
        # first cell is the label and the rest is the value.
        columnar = (
            len(rows) > 1
            and len(header) > 1
            and all(0 < len(cell) <= 40 for cell in header)
            and any(_looks_like_label(cell) for cell in header if cell)
        )
        for position, cells in enumerate(rows):
            if columnar and position == 0:
                continue
            if columnar:
                for label, value in zip(header, cells):
                    if label and value and label != value:
                        table_lines.append(f"{label}:    {value}")
                continue
            filled = [c for c in cells if c]
            if not filled:
                continue
            if len(filled) == 1:
                table_lines.append(filled[0])
            else:
                table_lines.append(f"{filled[0]}:    " + "    ".join(filled[1:]))
    tables = "\n".join(table_lines)

    pages = [_make_page(text, i, _score_page_for_declarations(text))
             for i, text in enumerate((body, tables)) if text.strip()]
    if not pages:
        return {"_error": "Word document contained no text."}
    pages.sort(key=lambda page: (-page["score"], page["index"]))
    return _extract_fields(pages, filename)


def _extract_from_excel(file_bytes: bytes, filename: str) -> dict:
    try:
        import pandas as pd
    except ImportError:
        return {"_error": "pandas not installed."}
    try:
        ext = Path(filename).suffix.lower()
        if ext == ".csv":
            frame = pd.read_csv(io.BytesIO(file_bytes))
        else:
            frame = pd.read_excel(io.BytesIO(file_bytes))
    except Exception as exc:
        return {"_error": f"Could not read spreadsheet: {exc}"}

    rows = ["    ".join(str(v) for v in row if pd.notna(v) and str(v).strip())
            for _, row in frame.iterrows()]
    text = "\n".join(["    ".join(str(c) for c in frame.columns)]
                     + [r for r in rows if r.strip()])
    if not text.strip():
        return {"_error": "Spreadsheet contained no data."}
    return _extract_fields([_make_page(text, 0, _score_page_for_declarations(text))], filename)


# ---------------------------------------------------------------------------
# File type router
# ---------------------------------------------------------------------------

_ALLOWED_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".xls", ".csv"}


def extract_policy_info(file_bytes: bytes, filename: str, full_scan: bool = False) -> SkillResult:
    """
    Extract policy fields from any supported document type.

    Returns SkillResult with data:
      {
        "policy_number":    str,   # may be empty
        "carrier":          str,   # raw extracted string
        "insured":          str,
        "effective_date":   str,
        "expiration_date":  str,
        "policy_period":    str,
        "coverage_raw":     str,
        "coverage_category": str,  # normalised
        "partial":          bool,  # True if any key field is missing
        "filename":         str,
      }
    """
    ext = Path(filename).suffix.lower()

    if ext not in _ALLOWED_EXTENSIONS:
        return SkillResult(
            success=False,
            error=f"Unsupported file type '{ext}'. Supported: PDF, DOCX, XLSX, CSV."
        )

    if ext == ".pdf":
        raw = _extract_from_pdf(file_bytes, filename, full_scan=full_scan)
    elif ext == ".docx":
        raw = _extract_from_docx(file_bytes, filename)
    else:
        raw = _extract_from_excel(file_bytes, filename)

    # Hard extraction error (bad file, wrong password, missing library)
    if "_error" in raw:
        # Still return success=True with empty fields — UI handles gracefully
        return SkillResult(
            success=True,
            data={
                "policy_number": "",
                "carrier": "",
                "insured": "",
                "effective_date": "",
                "expiration_date": "",
                "policy_period": "",
                "coverage_raw": "",
                "coverage_category": "",
                "carrier_group": "",
                "decl_page": -1,
                "partial": True,
                "extract_note": raw["_error"],
                "filename": filename,
            }
        )

    policy_number = _sanitize_extracted_value(raw.get("policy_number", ""), "policy_number")
    carrier = _sanitize_extracted_value(raw.get("carrier", ""), "carrier")
    insured = _sanitize_extracted_value(raw.get("insured", ""), "insured")
    effective_date = _sanitize_extracted_value(raw.get("effective_date", ""), "effective_date")
    expiration_date = _sanitize_extracted_value(raw.get("expiration_date", ""), "effective_date")
    policy_period = _sanitize_extracted_value(raw.get("policy_period", ""), "policy_period")

    # Normalise coverage
    cov_raw = _sanitize_extracted_value(raw.get("coverage_raw", ""), "coverage_raw")
    coverage_category = resolve_coverage(cov_raw)

    key_fields = [policy_number, carrier, insured, effective_date]
    partial = any(not value.strip() for value in key_fields)

    return SkillResult(
        success=True,
        data={
            "policy_number":    policy_number,
            "carrier":          carrier,
            "insured":          insured,
            "effective_date":   effective_date,
            "expiration_date":  expiration_date,
            "policy_period":    policy_period,
            "coverage_raw":     cov_raw,
            "coverage_category": coverage_category,
            "carrier_group":    raw.get("carrier_group", ""),
            "decl_page":        raw.get("_decl_page", -1),
            "partial":          partial,
            "extract_note":     raw.get("extract_note", ""),
            "filename":         filename,
        }
    )


# ---------------------------------------------------------------------------
# Carrier lookup — matches against carrier DB, not hardcoded keywords
# ---------------------------------------------------------------------------

def resolve_carrier(carrier_raw: str) -> str:
    """Map extracted carrier text to a canonical carrier from companymap.md."""
    if not carrier_raw:
        return ""

    companymap = _load_companymap()
    alias_to_canonical = companymap.get("alias_to_canonical", {})
    carrier_clean = _clean(carrier_raw)
    carrier_key = _canonicalize_key(carrier_clean)
    if not carrier_key:
        return ""

    if carrier_key in alias_to_canonical:
        return alias_to_canonical[carrier_key]

    # The gazetteer already encodes every guard this needs — word boundaries,
    # the generic-name blocklist, the short-alias whitelist — so try it before
    # the looser passes below.
    hits = _match_carrier(carrier_clean)
    if hits:
        return hits[0][0]

    canonical_names = list(companymap.get("canonical_to_aliases", {}).keys())
    for canonical in canonical_names:
        canonical_key = _canonicalize_key(canonical)
        # Only multi-word names are safe to substring-match. companymap has 162
        # single-word groups including "Insurance", "The", "New" and "National",
        # and a bare substring test lets those swallow arbitrary prose. Any
        # single-word group that genuinely appears was already caught above.
        if not canonical_key or len(canonical_key.split()) < 2:
            continue
        if _norm(canonical) in _GENERIC_CARRIER_KEYS:
            continue
        if canonical_key in carrier_key or carrier_key in canonical_key:
            return canonical

    common_words = {
        "insurance", "company", "national", "american", "specialty",
        "general", "casualty", "indemnity", "mutual", "surety",
        "financial", "group", "corp", "corporation", "llc", "inc",
        "fire", "property", "liability", "the", "and", "risk",
    }
    raw_tokens = {
        token for token in re.findall(r"\w{5,}", carrier_key)
        if token not in common_words
    }
    best_match = ""
    best_overlap = 0
    for canonical in canonical_names:
        canonical_key = _canonicalize_key(canonical)
        # Never let the loose token overlap land on a dictionary-generic group
        # name — "QBE Specialty Insurance Company" must not resolve to the
        # companymap group literally called "Insurance".
        if _norm(canonical) in _GENERIC_CARRIER_KEYS:
            continue
        canonical_tokens = {
            token for token in re.findall(r"\w{5,}", canonical_key)
            if token not in common_words
        }
        overlap = len(raw_tokens & canonical_tokens)
        if overlap > best_overlap:
            best_overlap = overlap
            best_match = canonical

    return best_match if best_overlap >= 1 else ""


def resolve_email_for_carrier(carrier_group: str) -> str:
    """Resolve a canonical carrier to its primary email from emailmap.md."""
    if not carrier_group:
        return ""
    emailmap = _load_emailmap()
    return _clean(emailmap.get(carrier_group, ""))


def lookup_carrier(carrier_raw: str, coverage_category: str) -> dict:
    """
    Resolve carrier via companymap.md, then resolve email via emailmap.md.
    Keeps the existing return shape for the UI and draft pipeline.
    """
    carrier_group = resolve_carrier(carrier_raw)
    primary_email = resolve_email_for_carrier(carrier_group)

    if not carrier_group:
        return {
            "carrier_group": "",
            "sub_company": "",
            "primary_email": "",
            "secondary_email": "",
            "portal_link": "",
            "coverage": coverage_category,
            "notes": "Carrier did not map to companymap.md — verify manually.",
        }

    if not primary_email:
        return {
            "carrier_group": carrier_group,
            "sub_company": "",
            "primary_email": "",
            "secondary_email": "",
            "portal_link": "",
            "coverage": coverage_category,
            "notes": "Carrier mapped, but no email found in emailmap.md.",
        }

    return {
        "carrier_group": carrier_group,
        "sub_company": "",
        "primary_email": primary_email,
        "secondary_email": "",
        "portal_link": "",
        "coverage": coverage_category,
        "notes": "",
    }


# ---------------------------------------------------------------------------
# Email draft builder
# ---------------------------------------------------------------------------

def _firm_name() -> str:
    """
    The firm's legal name, used as the signature block on generated requests.

    Lives in config/settings.json rather than in source so the repo carries no
    firm-specific identifiers. settings.json is gitignored; settings.example.json
    shows the shape.
    """
    try:
        import json as _json
        from pathlib import Path as _Path
        _p = _Path(__file__).resolve().parents[1] / "config" / "settings.json"
        return (_json.loads(_p.read_text(encoding="utf-8"))
                .get("identity", {})
                .get("firm_name") or "Your Brokerage LLC")
    except Exception:
        return "Your Brokerage LLC"


FIRM_NAME = _firm_name()

# Concatenated rather than interpolated: this is a .format() template, so the
# firm name must be baked in without becoming a format key the callers owe.
_EMAIL_TEMPLATE = """\
Subject: Loss Run Request – {insured} – {carrier_group}

To: {primary_email}

Dear {carrier_group} Loss Runs Team,

Please provide loss runs for the following policy for our insured, {insured}. \
We are requesting {years} years of loss history for renewal purposes.

  Policy Number:   {policy_number}
  Coverage:        {coverage}
  Effective Date:  {effective_date}

Please send the loss runs to {requestor_email} at your earliest convenience. \
If you have any questions, please don't hesitate to reach out.

Thank you,
{requestor_name}
""" + FIRM_NAME


def build_draft(
    insured: str,
    carrier_group: str,
    primary_email: str,
    policy_number: str,
    coverage: str,
    effective_date: str,
    requestor_name: str = "",
    requestor_email: str = "",
    years: int = 5,
) -> str:
    return _EMAIL_TEMPLATE.format(
        insured=insured or "[INSURED NAME]",
        carrier_group=carrier_group or "[CARRIER]",
        primary_email=primary_email or "[CONTACT EMAIL]",
        policy_number=policy_number or "[POLICY NUMBER]",
        coverage=coverage or "[COVERAGE TYPE]",
        effective_date=effective_date or "[EFFECTIVE DATE]",
        requestor_name=requestor_name,
        requestor_email=requestor_email or "[YOUR EMAIL]",
        years=years,
    ).strip()


# ---------------------------------------------------------------------------
# Stage 1: process_upload — extract fields from document
# Returns extracted fields for user review/correction in the UI.
# ---------------------------------------------------------------------------

def process_upload(file_bytes: bytes, filename: str) -> SkillResult:
    """
    Extract policy fields from uploaded document.

    Returns SkillResult with data:
      {
        "policy_number":     str,
        "carrier":           str,
        "insured":           str,
        "effective_date":    str,
        "expiration_date":   str,
        "coverage_raw":      str,
        "coverage_category": str,
        "partial":           bool,
        "extract_note":      str,
        "filename":          str,
        "today":             str,
      }

    Always succeeds (success=True) unless the file type is unsupported.
    Partial extractions return empty strings for missing fields.
    """
    result = extract_policy_info(file_bytes, filename)
    if not result.success:
        return result

    result.data["today"] = date.today().strftime("%m/%d/%Y")
    return result


# ---------------------------------------------------------------------------
# Stage 2: build_draft_from_confirmed — generate draft from user-confirmed fields
# Called after the user reviews and corrects extracted fields in the UI.
# ---------------------------------------------------------------------------

def build_draft_from_confirmed(confirmed: dict) -> SkillResult:
    """
    Build a loss run email draft from user-confirmed field values.

    Expected keys in confirmed:
      insured, policy_number, carrier, effective_date, coverage_category,
      requestor_name (optional), requestor_email (optional), years (optional)

    Performs carrier lookup from the confirmed carrier string, then builds
    the draft. Returns the draft text + carrier contact info.
    """
    insured        = _clean(confirmed.get("insured", ""))
    policy_number  = _clean(confirmed.get("policy_number", ""))
    carrier_raw    = _clean(confirmed.get("carrier", ""))
    effective_date = _clean(confirmed.get("effective_date", ""))
    coverage_cat   = _clean(confirmed.get("coverage_category", ""))
    requestor_name = confirmed.get("requestor_name", "")
    requestor_email = confirmed.get("requestor_email", "")
    years          = int(confirmed.get("years", 5))

    carrier_info = lookup_carrier(carrier_raw, coverage_cat)

    draft = build_draft(
        insured=insured,
        carrier_group=carrier_info["carrier_group"],
        primary_email=carrier_info["primary_email"],
        policy_number=policy_number,
        coverage=coverage_cat or carrier_info["coverage"],
        effective_date=effective_date,
        requestor_name=requestor_name,
        requestor_email=requestor_email,
        years=years,
    )

    return SkillResult(
        success=True,
        data={
            "draft":           draft,
            "carrier_group":   carrier_info["carrier_group"],
            "primary_email":   carrier_info["primary_email"],
            "secondary_email": carrier_info["secondary_email"],
            "portal_link":     carrier_info["portal_link"],
            "has_portal":      bool(carrier_info["portal_link"]),
            "missing_contact": (
                not carrier_info["primary_email"]
                and not carrier_info["portal_link"]
            ),
            "notes":           carrier_info["notes"],
            "insured":         insured,
            "policy_number":   policy_number,
            "carrier_raw":     carrier_raw,
            "today":           date.today().strftime("%m/%d/%Y"),
        }
    )


# ===========================================================================
# CHUNK 3 — Batch processing: ExtractedRow model, client resolution,
#           coverage alias resolution, confidence scoring, batch pipeline
# ===========================================================================

# ---------------------------------------------------------------------------
# ExtractedRow — canonical data unit for all batch operations
# ---------------------------------------------------------------------------

@dataclass
class ExtractedRow:
    """
    One row per uploaded file. Produced by process_batch(), consumed by
    build_draft_batch(). Table row in the UI = one ExtractedRow.
    """
    filename:          str = ""
    insured_raw:       str = ""   # extracted as-is from document
    insured_resolved:  str = ""   # after client alias lookup
    client_key:        str = ""   # matched folder_name from clients.json ("" if no match)
    alias_resolved:    bool = False  # True if insured_resolved differs from insured_raw
    policy_number:     str = ""
    carrier_raw:       str = ""   # extracted as-is
    carrier_group:     str = ""   # after DB lookup
    primary_email:     str = ""
    secondary_email:   str = ""
    portal_link:       str = ""
    effective_date:    str = ""
    coverage_raw:      str = ""
    coverage_category: str = ""
    confidence:        int = 0    # 0-100: 25 per populated key field
    partial:           bool = True
    extract_note:      str = ""
    decl_page:         int = -1

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Coverage alias resolution — config/coverage_aliases.json
# ---------------------------------------------------------------------------

_coverage_aliases_cache: dict | None = None

def _load_coverage_aliases() -> dict:
    global _coverage_aliases_cache
    if _coverage_aliases_cache is not None:
        return _coverage_aliases_cache
    if not _COV_ALIASES_PATH.exists():
        _coverage_aliases_cache = {}
        return {}
    data = json.loads(_COV_ALIASES_PATH.read_text(encoding="utf-8"))
    _coverage_aliases_cache = data.get("aliases", {})
    return _coverage_aliases_cache


def resolve_coverage(coverage_raw: str) -> str:
    """
    Map a verbose coverage string extracted from a binder to a canonical
    coverage category. coverages.md is the source of truth; coverage_aliases.json
    and _COVERAGE_MAP remain helper maps for common abbreviations and product names.
    Returns empty string if no approved canonical match is found.
    """
    if not coverage_raw:
        return ""

    coverages_md = _load_coverages_md()
    approved_categories = coverages_md.get("categories", set())
    terms = coverages_md.get("terms", {})

    raw_clean = _clean(coverage_raw)
    raw_upper = raw_clean.upper().strip()
    raw_key = _canonicalize_key(raw_clean)

    if raw_clean in approved_categories:
        return raw_clean

    if raw_key in terms:
        mapped = terms[raw_key]
        return mapped if mapped in approved_categories else ""

    if raw_upper in _COVERAGE_MAP:
        mapped = _COVERAGE_MAP[raw_upper]
        return mapped if mapped in approved_categories else ""

    aliases = _load_coverage_aliases()
    raw_lower = raw_clean.lower()
    for alias_key in sorted(aliases.keys(), key=len, reverse=True):
        if alias_key.lower() in raw_lower:
            mapped = aliases[alias_key]
            return mapped if mapped in approved_categories else ""

    for term_key, mapped in sorted(terms.items(), key=lambda item: len(item[0]), reverse=True):
        if term_key and term_key in raw_key:
            return mapped if mapped in approved_categories else ""

    for key, val in _COVERAGE_MAP.items():
        if key in raw_upper:
            return val if val in approved_categories else ""

    return ""


# ---------------------------------------------------------------------------
# Client alias resolution — config/clients.json
# ---------------------------------------------------------------------------

_clients_cache: list | None = None

def _load_clients() -> list:
    global _clients_cache
    if _clients_cache is not None:
        return _clients_cache
    if not _CLIENTS_PATH.exists():
        _clients_cache = []
        return []
    data = json.loads(_CLIENTS_PATH.read_text(encoding="utf-8"))
    _clients_cache = data.get("clients", [])
    return _clients_cache


def resolve_client(insured_raw: str) -> tuple[str, str, bool]:
    """
    Match insured_raw against clients.json display_name, aliases, and
    engagement legal_name values.

    Returns (insured_resolved, client_key, alias_resolved) where:
      insured_resolved: canonical display_name if matched, else insured_raw
      client_key:       folder_name of matched client ("" if no match)
      alias_resolved:   True if match was via alias (not exact display_name)

    Matching order:
      1. Case-insensitive exact match on display_name
      2. Case-insensitive exact match on any alias
      3. Case-insensitive exact match on any engagement legal_name
      4. Bidirectional substring match (4+ char tokens) on display_name + aliases
      5. No match → return (insured_raw, "", False)
    """
    if not insured_raw:
        return ("", "", False)

    clients = _load_clients()
    raw_lower = insured_raw.lower().strip()

    # Passes 1-3: exact matches
    for client in clients:
        display = client.get("display_name", "")
        folder  = client.get("folder_name", "")
        aliases = client.get("aliases", [])
        legal_names = [
            e.get("legal_name", "")
            for e in client.get("engagements", [])
            if e.get("legal_name", "")
        ]

        if display.lower() == raw_lower:
            return (display, folder, False)

        for alias in aliases:
            if alias.lower() == raw_lower:
                return (display, folder, True)

        for legal in legal_names:
            if legal.lower() == raw_lower:
                return (display, folder, True)

    # Pass 3b: gazetteer hit — "Heritage Holdings Corporation" contains the
    # alias "Heritage Holdings", which exact matching misses.
    hit = _match_client(insured_raw)
    if hit:
        return (hit[0], hit[1], _norm(hit[0]) != _norm(insured_raw))

    # Pass 4: bidirectional substring on 4+ char tokens
    raw_tokens = set(t for t in re.findall(r"\b\w{4,}\b", raw_lower))
    if raw_tokens:
        best_overlap = 0
        best_client  = None
        for client in clients:
            display = client.get("display_name", "")
            aliases = client.get("aliases", [])
            candidate_strings = [display] + aliases + [
                e.get("legal_name", "")
                for e in client.get("engagements", [])
            ]
            client_tokens = set(
                t for s in candidate_strings
                for t in re.findall(r"\b\w{4,}\b", s.lower())
            )
            overlap = len(raw_tokens & client_tokens)
            if overlap > best_overlap:
                best_overlap = overlap
                best_client = client

        # Require at least 2 overlapping tokens to avoid false positives
        if best_overlap >= 2 and best_client:
            display = best_client.get("display_name", "")
            folder  = best_client.get("folder_name", "")
            return (display, folder, True)

    return (insured_raw, "", False)


# ---------------------------------------------------------------------------
# Confidence scoring
# ---------------------------------------------------------------------------

_CONFIDENCE_FIELDS = ["policy_number", "carrier_raw", "insured_raw", "effective_date"]

def score_confidence(row: ExtractedRow) -> int:
    """
    Score 0-100 based on how many of the 4 key fields are populated.
    25 points per field. Used to highlight rows in the batch table.

      100 = all four present       → no highlight
       75 = one missing            → no highlight
       50 = two missing            → yellow
       25 = three missing          → red
        0 = all missing            → red
    """
    populated = sum(
        1 for f in _CONFIDENCE_FIELDS
        if getattr(row, f, "").strip()
    )
    return populated * 25


# ---------------------------------------------------------------------------
# Batch pipeline
# ---------------------------------------------------------------------------

def process_batch(files: list[tuple[bytes, str]], full_scan: bool = False) -> list[ExtractedRow]:
    """
    Process a list of (file_bytes, filename) tuples.
    Returns one ExtractedRow per file.
    Extraction errors never raise — they produce a partial row with extract_note set.

    Pipeline per file:
      1. Extract raw fields (existing 3-pass pipeline)
      2. Resolve coverage_raw → coverage_category via aliases
      3. Resolve insured_raw → canonical client via clients.json
      4. Lookup carrier → primary_email, portal_link, etc.
      5. Score confidence
    """
    rows: list[ExtractedRow] = []

    for file_bytes, filename in files:
        row = ExtractedRow(filename=filename)

        try:
            # Step 1: extract
            result = extract_policy_info(file_bytes, filename, full_scan=full_scan)
            if not result.success:
                row.extract_note = result.error
                row.confidence = score_confidence(row)
                rows.append(row)
                continue

            d = result.data
            row.insured_raw    = d.get("insured", "")
            row.policy_number  = d.get("policy_number", "")
            row.carrier_raw    = d.get("carrier", "")
            row.effective_date = d.get("effective_date", "")
            row.coverage_raw   = d.get("coverage_raw", "")
            row.extract_note   = d.get("extract_note", "")
            row.partial        = d.get("partial", True)
            row.decl_page      = d.get("decl_page", -1)

            # Step 2: coverage alias resolution
            row.coverage_category = resolve_coverage(row.coverage_raw)

            # Step 3: client resolution
            resolved, client_key, was_alias = resolve_client(row.insured_raw)
            row.insured_resolved = resolved
            row.client_key       = client_key
            row.alias_resolved   = was_alias

            # Step 4: carrier lookup
            carrier_info         = lookup_carrier(
                d.get("carrier_group", "") or row.carrier_raw, row.coverage_category)
            row.carrier_group    = carrier_info["carrier_group"]
            row.primary_email    = carrier_info["primary_email"]
            row.secondary_email  = carrier_info["secondary_email"]
            row.portal_link      = carrier_info["portal_link"]

            # Step 5: confidence
            row.confidence = score_confidence(row)

        except Exception as e:
            row.extract_note = f"Unexpected error: {e}"
            row.confidence = score_confidence(row)

        rows.append(row)

    return rows


# ---------------------------------------------------------------------------
# Batch draft generation — groups by primary_email (or portal_link)
# ---------------------------------------------------------------------------

@dataclass
class DraftResult:
    """One draft = one unique recipient (email or portal)."""
    recipient_key:   str        # primary_email or portal_link
    carrier_group:   str
    primary_email:   str
    secondary_email: str
    portal_link:     str
    has_portal:      bool
    missing_contact: bool
    insured:         str        # resolved insured name for this group
    rows:            list[dict] # the ExtractedRow dicts that fed this draft
    draft:           str
    notes:           str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def build_draft_batch(
    confirmed_rows: list[dict],
    requestor_name:  str = "",
    requestor_email: str = "",
    years:           int = 5,
) -> SkillResult:
    """
    Accept a list of confirmed row dicts (from the batch review table),
    group by primary_email (or portal_link for portal-only carriers),
    and produce one DraftResult per unique recipient.

    Each confirmed row dict should have:
      insured, policy_number, carrier, effective_date, coverage_category,
      carrier_group, primary_email, secondary_email, portal_link,
      filename (for display only)

    Returns SkillResult with data:
      {
        "drafts": [DraftResult.to_dict(), ...],
        "total_files": int,
        "total_drafts": int,
        "today": str,
      }
    """
    if not confirmed_rows:
        return SkillResult(success=False, error="No rows provided.")

    # Group rows by recipient key
    groups: dict[str, dict] = {}

    for row in confirmed_rows:
        primary  = _clean(row.get("primary_email", ""))
        portal   = _clean(row.get("portal_link", ""))
        recipient_key = primary or portal or "__no_contact__"

        if recipient_key not in groups:
            groups[recipient_key] = {
                "recipient_key":   recipient_key,
                "carrier_group":   _clean(row.get("carrier_group", row.get("carrier", ""))),
                "primary_email":   primary,
                "secondary_email": _clean(row.get("secondary_email", "")),
                "portal_link":     portal,
                "insured":         _clean(row.get("insured", "")),
                "rows":            [],
                "notes":           _clean(row.get("notes", "")),
            }

        groups[recipient_key]["rows"].append(row)

    # Build one draft per group
    drafts: list[DraftResult] = []
    today = date.today().strftime("%m/%d/%Y")

    for rk, grp in groups.items():
        has_portal      = bool(grp["portal_link"])
        missing_contact = not grp["primary_email"] and not has_portal

        # Build policy lines for this group
        policy_lines_parts = []
        for r in grp["rows"]:
            pn  = _clean(r.get("policy_number", "")) or "[POLICY NUMBER]"
            cov = _clean(r.get("coverage_category", r.get("coverage_raw", ""))) or "[COVERAGE]"
            eff = _clean(r.get("effective_date", "")) or "[EFFECTIVE DATE]"
            fn  = r.get("filename", "")
            policy_lines_parts.append(f"  \u2022 {cov} | Policy No. {pn} | Effective {eff}")

        policy_block = "\n".join(policy_lines_parts)
        insured      = grp["insured"] or "[INSURED NAME]"
        carrier_grp  = grp["carrier_group"] or "[CARRIER]"
        to_email     = grp["primary_email"] or "[CONTACT EMAIL]"

        draft_text = f"""Subject: Loss Run Request \u2013 {insured} \u2013 {carrier_grp}

To: {to_email}

Dear {carrier_grp} Loss Runs Team,

Please provide loss runs for the following polic{'y' if len(grp['rows']) == 1 else 'ies'} for our insured, {insured}. We are requesting {years} years of loss history for renewal purposes.

{policy_block}

Please send the loss runs to {requestor_email or '[YOUR EMAIL]'} at your earliest convenience. If you have any questions, please don\u2019t hesitate to reach out.

Thank you,
{requestor_name}
{FIRM_NAME}""".strip()

        drafts.append(DraftResult(
            recipient_key   = rk,
            carrier_group   = carrier_grp,
            primary_email   = grp["primary_email"],
            secondary_email = grp["secondary_email"],
            portal_link     = grp["portal_link"],
            has_portal      = has_portal,
            missing_contact = missing_contact,
            insured         = insured,
            rows            = grp["rows"],
            draft           = draft_text,
            notes           = grp["notes"],
        ))

    # Sort: email contacts first, portal second, missing last
    drafts.sort(key=lambda d: (d.missing_contact, d.has_portal and not d.primary_email))

    return SkillResult(
        success=True,
        data={
            "drafts":        [d.to_dict() for d in drafts],
            "total_files":   len(confirmed_rows),
            "total_drafts":  len(drafts),
            "today":         today,
        }
    )
