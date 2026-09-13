"""
client_resolver.py
------------------
Chunk 2 of the CoS build plan.

Single responsibility: given a mail message or calendar event, return the
canonical client and project it belongs to — or None if it is not a client
activity.

Public API
----------
load_client_config(project_root)           → ClientConfig
resolve_client_from_folder(folder, cfg)    → ClientRecord | None
resolve_client_from_subject(subject, cfg)  → ClientRecord | None
classify_calendar_event(subject, organizer, cfg) → EventClassification
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SYSTEM_FOLDERS: frozenset[str] = frozenset({
    "inbox",
    "sent items",
    "drafts",
    "deleted items",
    "junk email",
    "outbox",
    "conversation history",
    "archive",
    "notes",
    "tasks",
    "calendar",
    "contacts",
})

# Calendar event subjects that are always internal — skip regardless of client keyword presence
INTERNAL_SUBJECT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bcheck[- ]?in\b", re.IGNORECASE),
    re.compile(r"\btouch[- ]?base\b", re.IGNORECASE),
    re.compile(r"\btraining\s+series\b", re.IGNORECASE),
    re.compile(r"^busy$", re.IGNORECASE),
    re.compile(r"^canceled:", re.IGNORECASE),
    re.compile(r"\bwebinar\b", re.IGNORECASE),
    re.compile(r"\bteam\s+meeting\b", re.IGNORECASE),
    re.compile(r"\bstaff\s+meeting\b", re.IGNORECASE),
    re.compile(r"\ball[- ]?hands\b", re.IGNORECASE),
    re.compile(r"\btown[- ]?hall\b", re.IGNORECASE),
    re.compile(r"\b1:1\b|\bone[- ]on[- ]one\b", re.IGNORECASE),
    re.compile(r"\binterview\b", re.IGNORECASE),
    re.compile(r"\bblock(?:ed)?\s+time\b", re.IGNORECASE),
    re.compile(r"\bhold\b", re.IGNORECASE),
    re.compile(r"\bout\s+of\s+office\b", re.IGNORECASE),
    re.compile(r"\booo\b", re.IGNORECASE),
    re.compile(r"\bvacation\b", re.IGNORECASE),
    re.compile(r"\bpto\b", re.IGNORECASE),
]

# Stage keywords for hint detection (order matters — more specific first)
STAGE_HINT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\brenewal\s+prep(?:aration)?\b", re.IGNORECASE), "Renewal Preparation"),
    (re.compile(r"\bism\b", re.IGNORECASE), "Renewal Preparation"),   # ISM (Internal Strategy Meeting) → kicks off prep stage
    (re.compile(r"\brsm\b", re.IGNORECASE), "RSM"),
    (re.compile(r"\bsubmission\b", re.IGNORECASE), "Submission"),
    (re.compile(r"\bproposal\b", re.IGNORECASE), "Proposal"),
    (re.compile(r"\bbind(?:er|ing)?\b", re.IGNORECASE), "Bind"),
    (re.compile(r"\binvoice\b", re.IGNORECASE), "Invoice"),
    (re.compile(r"\bpost[- ]?bind(?:ing)?\b", re.IGNORECASE), "Post Binding"),
    (re.compile(r"\bdeliver\s+polic(?:y|ies)\b", re.IGNORECASE), "Deliver Policies"),
    (re.compile(r"\brenewal\b", re.IGNORECASE), "Renewal Preparation"),
]


# ---------------------------------------------------------------------------
# Domain dataclasses
# ---------------------------------------------------------------------------

@dataclass
class Engagement:
    """A single line of business / renewal engagement for a client."""
    label: str                          # e.g. "P&C", "ML + Cyber", "" (single-engagement)
    legal_name: str                       # full legal entity name (e.g. "Northwind Foods Corporation")
    project_name: str                   # canonical project name: "12/18/2026 Renewal (P&C)"
    renewal_date: str                   # MM/DD/YYYY
    stage_dates: Dict[str, str] = field(default_factory=dict)  # stage → MM/DD/YYYY


@dataclass
class ClientRecord:
    """A single client entry from clients.json."""
    folder_name: str                    # Outlook folder name: "Northwind", "Vantage", …
    display_name: str                   # Short display name: "Northwind Foods", "Vantage"
    active: bool
    jordan_role: str                    # "AE" | "AAE" | "AR"
    show_all_workflow_tasks: bool
    notes: str
    engagements: List[Engagement] = field(default_factory=list)
    aliases: List[str] = field(default_factory=list)  # alternate names / abbreviations


@dataclass
class ClientConfig:
    """
    Loaded and indexed view of config/clients.json.

    Pre-builds fast lookup structures so callers never iterate the full list.
    """
    clients: List[ClientRecord]
    _by_folder: Dict[str, ClientRecord] = field(default_factory=dict, repr=False)
    _by_display: Dict[str, ClientRecord] = field(default_factory=dict, repr=False)
    # display_name lowercased → ClientRecord (for case-insensitive matching)
    _by_display_lower: Dict[str, ClientRecord] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        for client in self.clients:
            self._by_folder[client.folder_name] = client
            self._by_display[client.display_name] = client
            self._by_display_lower[client.display_name.lower()] = client

    @property
    def active_clients(self) -> List[ClientRecord]:
        return [c for c in self.clients if c.active]

    @property
    def folder_names(self) -> List[str]:
        return [c.folder_name for c in self.clients]


@dataclass
class EventClassification:
    """Result of classify_calendar_event()."""
    client: Optional[ClientRecord]      # None if no client match
    engagement: Optional[Engagement]    # None if client has 1 engagement or no label match
    is_internal: bool                   # True → discard; not a client-facing work item
    stage_hint: Optional[str]           # Stage keyword if detected, else None
    confidence: str                     # "High" | "Medium" | "Low"
    reason: str                         # Human-readable explanation for debug/logging



# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

def load_client_config(project_root: Path) -> ClientConfig:
    """
    Load config/clients.json and return a ClientConfig with pre-built lookup indexes.

    Raises FileNotFoundError if the file does not exist.
    Raises ValueError if the JSON structure is invalid.
    """
    config_path = project_root / "config" / "clients.json"
    if not config_path.exists():
        raise FileNotFoundError(f"clients.json not found at {config_path}")

    raw = json.loads(config_path.read_text(encoding="utf-8"))

    clients_raw = raw.get("clients")
    if not isinstance(clients_raw, list):
        raise ValueError("clients.json must have a top-level 'clients' array")

    clients: List[ClientRecord] = []
    for entry in clients_raw:
        engagements: List[Engagement] = []
        for eng in entry.get("engagements", []):
            engagements.append(Engagement(
                label=eng.get("label", ""),
                legal_name=eng.get("legal_name", ""),
                project_name=eng.get("project_name", ""),
                renewal_date=eng.get("renewal_date", ""),
                stage_dates=eng.get("stage_dates", {}),
            ))

        clients.append(ClientRecord(
            folder_name=entry.get("folder_name", ""),
            display_name=entry.get("display_name", ""),
            active=bool(entry.get("active", True)),
            jordan_role=entry.get("jordan_role", ""),
            show_all_workflow_tasks=bool(entry.get("show_all_workflow_tasks", True)),
            notes=entry.get("notes", ""),
            engagements=engagements,
            aliases=entry.get("aliases", []),
        ))

    return ClientConfig(clients=clients)


# ---------------------------------------------------------------------------
# Folder-based resolution (highest confidence)
# ---------------------------------------------------------------------------

def resolve_client_from_folder(
    folder_name: str,
    cfg: ClientConfig,
) -> Optional[ClientRecord]:
    """
    Return the ClientRecord whose folder_name matches exactly (case-sensitive).

    Returns None if:
    - folder_name is a system folder (Inbox, Sent Items, …)
    - folder_name is not in the client config

    Confidence: High — folder is the most reliable client signal.
    """
    if not folder_name:
        return None

    if folder_name.lower() in SYSTEM_FOLDERS:
        return None

    return cfg._by_folder.get(folder_name)


# ---------------------------------------------------------------------------
# Subject-based resolution (lower confidence)
# ---------------------------------------------------------------------------

def resolve_client_from_subject(
    subject: str,
    cfg: ClientConfig,
) -> Optional[ClientRecord]:
    """
    Try to match a client display_name within the email/event subject.

    Resolution order (stops at first match):
    1. Exact case-insensitive match against display_name
    2. Subject starts-with display_name (e.g. "Northwind - RSM review")
    3. Subject contains display_name as a whole word

    Returns None if no match — does NOT guess.

    Confidence: Medium (subject-only) → callers should note this.
    """
    if not subject:
        return None

    normalized = _normalize_subject(subject)
    lowered = normalized.lower()

    # Sorted longest-name-first to avoid "TRM" matching before "TRM Holdings" etc.
    sorted_clients = sorted(
        cfg.active_clients,
        key=lambda c: len(c.display_name),
        reverse=True,
    )

    for client in sorted_clients:
        # Build the full set of terms to match: display_name + all aliases
        # Sorted longest-first within the client so a longer alias wins over a short one
        terms = sorted(
            [client.display_name] + client.aliases,
            key=len,
            reverse=True,
        )

        for term in terms:
            term_lower = term.lower()
            if not term_lower:
                continue

            # 1. Exact
            if lowered == term_lower:
                return client

            # 2. Starts-with (followed by non-alphanumeric or end)
            if lowered.startswith(term_lower) and (
                len(lowered) == len(term_lower)
                or not lowered[len(term_lower)].isalnum()
            ):
                return client

            # 3. Whole-word contains
            escaped = re.escape(term_lower)
            if re.search(rf"\b{escaped}\b", lowered):
                return client

    return None


# ---------------------------------------------------------------------------
# Engagement resolution
# ---------------------------------------------------------------------------

def resolve_engagement(
    client: ClientRecord,
    subject: str,
) -> Optional[Engagement]:
    """
    Given a client that has multiple engagements, try to identify which one
    the subject/text refers to.

    Returns:
    - The single engagement if the client has only one.
    - The matching engagement if a label keyword is found in the subject.
    - None if ambiguous (multiple engagements, no label match) — caller should
      record the activity against the client rather than a specific engagement.
    """
    if not client.engagements:
        return None

    if len(client.engagements) == 1:
        return client.engagements[0]

    lowered = subject.lower()
    for eng in client.engagements:
        if eng.label and eng.label.lower() in lowered:
            return eng

    return None


# ---------------------------------------------------------------------------
# Stage hint detection
# ---------------------------------------------------------------------------

def detect_stage_hint(text: str) -> Optional[str]:
    """
    Scan text for a stage keyword and return the canonical stage name.
    Returns None if no stage keyword is found.
    """
    if not text:
        return None
    for pattern, stage_name in STAGE_HINT_PATTERNS:
        if pattern.search(text):
            return stage_name
    return None


# ---------------------------------------------------------------------------
# Calendar event classifier
# ---------------------------------------------------------------------------

def classify_calendar_event(
    subject: str,
    organizer: str,
    cfg: ClientConfig,
    *,
    jordan_name: str = "Pratt, Jordan",
) -> EventClassification:
    """
    Classify a calendar event as internal or client-facing.

    Rules (evaluated in order):
    1. Subject matches an internal pattern → is_internal=True immediately.
    2. Subject contains a known client name → client-facing, confidence=High.
    3. Subject contains a stage keyword → probably client-facing, confidence=Medium
       (caller may want to verify folder as well).
    4. Organizer is Jordan AND no client/stage keyword → is_internal=True.
    5. Default: is_internal=True (conservative — don't add noise to workbook).

    Parameters
    ----------
    subject    : Calendar event subject
    organizer  : Organizer display name (e.g. "Pratt, Jordan")
    cfg        : Loaded ClientConfig
    jordan_name: Jordan's display name as it appears in Outlook organizer field
    """
    normalized = _normalize_subject(subject)

    # --- Rule 1: Hard internal patterns ---
    for pattern in INTERNAL_SUBJECT_PATTERNS:
        if pattern.search(normalized):
            return EventClassification(
                client=None,
                engagement=None,
                is_internal=True,
                stage_hint=None,
                confidence="High",
                reason=f"Matched internal subject pattern: {pattern.pattern!r}",
            )

    # --- Rule 2: Client name in subject ---
    client = resolve_client_from_subject(normalized, cfg)
    if client is not None:
        engagement = resolve_engagement(client, normalized)
        stage_hint = detect_stage_hint(normalized)
        return EventClassification(
            client=client,
            engagement=engagement,
            is_internal=False,
            stage_hint=stage_hint,
            confidence="High",
            reason=f"Client name '{client.display_name}' found in subject",
        )

    # --- Rule 3: Stage keyword present (no client name) ---
    stage_hint = detect_stage_hint(normalized)
    if stage_hint is not None:
        return EventClassification(
            client=None,
            engagement=None,
            is_internal=False,
            stage_hint=stage_hint,
            confidence="Medium",
            reason=f"Stage keyword '{stage_hint}' found in subject, but no client name matched",
        )

    # --- Rule 4: Jordan is organizer, no client/stage signal → internal ---
    if organizer and jordan_name.lower() in organizer.lower():
        return EventClassification(
            client=None,
            engagement=None,
            is_internal=True,
            stage_hint=None,
            confidence="High",
            reason="Jordan is organizer with no client or stage keyword — treating as internal",
        )

    # --- Rule 5: Default → internal (conservative) ---
    return EventClassification(
        client=None,
        engagement=None,
        is_internal=True,
        stage_hint=None,
        confidence="Low",
        reason="No client name, no stage keyword, organizer unknown — defaulting to internal",
    )


# ---------------------------------------------------------------------------
# Summary / diagnostic helper
# ---------------------------------------------------------------------------

def print_config_summary(cfg: ClientConfig) -> None:
    """Print a human-readable summary of the loaded config.  Useful for verification."""
    active = cfg.active_clients
    print(f"\n{'='*60}")
    print(f"Client Config Summary  ({len(active)} active clients, "
          f"{sum(len(c.engagements) for c in active)} engagements)")
    print(f"{'='*60}")
    print(f"{'Folder':<20} {'Display Name':<20} {'Engagements':<12} Renewal Date(s)")
    print(f"{'-'*20} {'-'*20} {'-'*12} {'-'*30}")
    for client in sorted(active, key=lambda c: c.display_name):
        dates = ", ".join(
            f"{eng.renewal_date}" + (f" ({eng.label})" if eng.label else "")
            for eng in client.engagements
        )
        print(f"{client.folder_name:<20} {client.display_name:<20} "
              f"{len(client.engagements):<12} {dates}")
    print()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _normalize_subject(subject: str | None) -> str:
    """Strip RE:/FW: prefixes and collapse whitespace."""
    if not subject:
        return ""
    value = str(subject).strip()
    prefix_pattern = re.compile(r"^(?:(?:re|fw|fwd)\s*:\s*)+", re.IGNORECASE)
    previous = None
    while previous != value:
        previous = value
        value = prefix_pattern.sub("", value).strip()
    return re.sub(r"\s+", " ", value)
