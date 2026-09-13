"""
inference_engine.py
-------------------
Chunk 5 of the CoS build plan.

Single responsibility: convert raw classified mail and calendar events into
domain-level ResolvedActivity objects using the canonical client/engagement
resolution layer built in Chunk 2.

Public API
----------
run_inference(classified_mail, calendar_events, client_config, project_root)
    -> tuple[list[ResolvedActivity], list[UnmatchedItem]]

print_summary(activities, unmatched)
    Print a human-readable verification summary to stdout.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from src.client_resolver import (
    ClientConfig,
    classify_calendar_event,
    detect_stage_hint,
    resolve_client_from_folder,
    resolve_client_from_subject,
    resolve_engagement,
    _normalize_subject,
)
from src.calendar_models import CalendarEvent
from src.mail_rules import ClassifiedMailMessage
from src.models import ResolvedActivity


# ---------------------------------------------------------------------------
# UnmatchedItem — returned alongside ResolvedActivity for the discard log
# ---------------------------------------------------------------------------

@dataclass
class UnmatchedItem:
    """A mail message or calendar event that did not resolve to a client activity."""
    source_type: str        # "mail" | "calendar"
    timestamp: str          # ISO string
    subject: str            # raw subject / event title
    folder_name: str        # mail folder or "" for calendar
    discard_reason: str     # human-readable explanation


# ---------------------------------------------------------------------------
# Stage keyword loader
# ---------------------------------------------------------------------------

def _load_stage_keywords(project_root: Path) -> dict[str, list[str]]:
    """Load config/stage_keywords.json. Returns empty dict if missing."""
    path = project_root / "config" / "stage_keywords.json"
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    # Strip the comment key if present
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def _match_stage_from_keywords(
    text: str,
    stage_keywords: dict[str, list[str]],
) -> Optional[str]:
    """
    Scan text against config/stage_keywords.json keyword lists.
    Returns the first matching stage name, or None.
    Supplements (does not replace) client_resolver.detect_stage_hint().
    """
    lowered = text.lower()
    for stage_name, keywords in stage_keywords.items():
        for kw in keywords:
            if kw.lower() in lowered:
                return stage_name
    return None


# ---------------------------------------------------------------------------
# ISO datetime helper
# ---------------------------------------------------------------------------

def _parse_iso(value: str | None) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    if " " in text and "T" not in text:
        text = text.replace(" ", "T", 1)
    try:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Mail inference
# ---------------------------------------------------------------------------

def _infer_from_mail(
    message: ClassifiedMailMessage,
    cfg: ClientConfig,
    stage_keywords: dict[str, list[str]],
    now: datetime,
) -> Tuple[Optional[ResolvedActivity], Optional[UnmatchedItem]]:
    """
    Attempt to resolve a single ClassifiedMailMessage into a ResolvedActivity.

    Returns (activity, None) on success or (None, unmatched) on discard.
    """
    # --- Filter: noise ---
    if message.priority == "noise":
        return None, UnmatchedItem(
            source_type="mail",
            timestamp=message.received_time,
            subject=message.subject,
            folder_name=message.folder_name,
            discard_reason="priority=noise from mail_rules",
        )

    normalized = _normalize_subject(message.subject)

    # --- Step 1: Resolve client ---
    # Folder-based: highest confidence
    client = resolve_client_from_folder(message.folder_name, cfg)
    confidence = "high" if client else None

    # Subject-based: medium confidence
    if client is None:
        client = resolve_client_from_subject(normalized, cfg)
        if client is not None:
            confidence = "medium"

    # Body-preview: low confidence (only if subject gave nothing)
    if client is None and message.body_preview:
        client = resolve_client_from_subject(message.body_preview, cfg)
        if client is not None:
            confidence = "low"

    # No client → discard
    if client is None:
        return None, UnmatchedItem(
            source_type="mail",
            timestamp=message.received_time,
            subject=message.subject,
            folder_name=message.folder_name,
            discard_reason="no client resolved from folder, subject, or body preview",
        )

    # --- Step 2: Resolve engagement (Option A — no guessing) ---
    search_text = f"{normalized} {message.body_preview or ''}"
    engagement = resolve_engagement(client, search_text)
    project_name = engagement.project_name if engagement else ""
    engagement_label = engagement.label if engagement else ""

    # --- Step 3: Detect stage ---
    stage_hint = detect_stage_hint(normalized)
    if stage_hint is None and message.body_preview:
        stage_hint = detect_stage_hint(message.body_preview)
    if stage_hint is None:
        stage_hint = _match_stage_from_keywords(search_text, stage_keywords)
    stage_hint = stage_hint or ""

    # --- Step 4: Classify activity type ---
    if message.is_sent_by_jordan:
        activity_type = "Action Taken"
    elif message.unread:
        activity_type = "In Progress"
    else:
        activity_type = "In Progress"

    return ResolvedActivity(
        source_type="mail",
        client_name=client.display_name,
        project_name=project_name,
        engagement_label=engagement_label,
        stage_hint=stage_hint,
        activity_type=activity_type,
        timestamp=message.received_time,
        reference=normalized[:200],
        sender=message.sender_name,
        confidence=confidence,
        is_internal=False,
    ), None


# ---------------------------------------------------------------------------
# Calendar inference
# ---------------------------------------------------------------------------

def _infer_from_calendar(
    event: CalendarEvent,
    cfg: ClientConfig,
    stage_keywords: dict[str, list[str]],
    now: datetime,
) -> Tuple[Optional[ResolvedActivity], Optional[UnmatchedItem]]:
    """
    Attempt to resolve a single CalendarEvent into a ResolvedActivity.

    Returns (activity, None) on success or (None, unmatched) on discard.
    """
    normalized = _normalize_subject(event.subject)
    organizer = event.organizer or ""
    search_text = f"{normalized} {event.body_preview or ''}"

    # --- Classify via client_resolver (handles internal patterns, client match, stage hint) ---
    classification = classify_calendar_event(normalized, organizer, cfg)

    # --- Filter: internal ---
    if classification.is_internal:
        return None, UnmatchedItem(
            source_type="calendar",
            timestamp=event.start or "",
            subject=event.subject,
            folder_name="",
            discard_reason=f"internal event: {classification.reason}",
        )

    # --- Filter: no client resolved ---
    if classification.client is None:
        return None, UnmatchedItem(
            source_type="calendar",
            timestamp=event.start or "",
            subject=event.subject,
            folder_name="",
            discard_reason=f"no client resolved: {classification.reason}",
        )

    client = classification.client
    engagement = classification.engagement
    project_name = engagement.project_name if engagement else ""
    engagement_label = engagement.label if engagement else ""

    # --- Stage hint: classification result first, then keyword supplement ---
    stage_hint = classification.stage_hint
    if not stage_hint:
        stage_hint = _match_stage_from_keywords(search_text, stage_keywords)
    stage_hint = stage_hint or ""

    # --- Step 4: Classify activity type from event timing ---
    event_dt = _parse_iso(event.start)
    if event_dt is None:
        return None, UnmatchedItem(
            source_type="calendar",
            timestamp=event.start or "",
            subject=event.subject,
            folder_name="",
            discard_reason="could not parse event start datetime",
        )

    if event_dt > now:
        activity_type = "Upcoming"
    else:
        activity_type = "Complete"

    # Confidence: map from EventClassification confidence (capitalized) to lowercase strings
    confidence = classification.confidence.lower()

    return ResolvedActivity(
        source_type="calendar",
        client_name=client.display_name,
        project_name=project_name,
        engagement_label=engagement_label,
        stage_hint=stage_hint,
        activity_type=activity_type,
        timestamp=event.start or "",
        reference=normalized[:200],
        sender=organizer,
        confidence=confidence,
        is_internal=False,
    ), None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_inference(
    classified_mail: List[ClassifiedMailMessage],
    calendar_events: List[CalendarEvent],
    client_config: ClientConfig,
    project_root: Path,
) -> Tuple[List[ResolvedActivity], List[UnmatchedItem]]:
    """
    Convert classified mail and calendar events into ResolvedActivity objects.

    Parameters
    ----------
    classified_mail   : Output of [classify_message(m) for m in messages]
    calendar_events   : Output of CalendarReader.get_events_between()
    client_config     : Loaded ClientConfig from load_client_config()
    project_root      : Project root path (used to load config/stage_keywords.json)

    Returns
    -------
    activities  : List of resolved, client-matched activities
    unmatched   : List of discarded items with discard reasons
    """
    stage_keywords = _load_stage_keywords(project_root)
    now = datetime.now(timezone.utc)

    activities: List[ResolvedActivity] = []
    unmatched: List[UnmatchedItem] = []

    for message in classified_mail:
        activity, item = _infer_from_mail(message, client_config, stage_keywords, now)
        if activity is not None:
            activities.append(activity)
        elif item is not None:
            unmatched.append(item)

    for event in calendar_events:
        activity, item = _infer_from_calendar(event, client_config, stage_keywords, now)
        if activity is not None:
            activities.append(activity)
        elif item is not None:
            unmatched.append(item)

    return activities, unmatched


def print_summary(
    activities: List[ResolvedActivity],
    unmatched: List[UnmatchedItem],
) -> None:
    """Print a verification summary to stdout."""
    print(f"\n{'='*60}")
    print(f"Inference Engine Summary")
    print(f"{'='*60}")
    print(f"Resolved activities : {len(activities)}")
    print(f"Discarded (unmatched): {len(unmatched)}")

    if activities:
        print(f"\n--- Resolved (sample, up to 10) ---")
        for act in activities[:10]:
            eng = f" [{act.engagement_label}]" if act.engagement_label else ""
            stage = f" | {act.stage_hint}" if act.stage_hint else ""
            print(
                f"  {act.source_type:<8} {act.activity_type:<14} "
                f"{act.client_name}{eng}{stage} "
                f"| conf={act.confidence} | {act.reference[:60]}"
            )

    if unmatched:
        from collections import Counter
        reasons = Counter(item.discard_reason.split(":")[0].strip() for item in unmatched)
        print(f"\n--- Discard reasons ---")
        for reason, count in reasons.most_common():
            print(f"  {count:>4}x  {reason}")

    print()
