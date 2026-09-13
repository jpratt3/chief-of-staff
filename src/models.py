from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ResolvedActivity:
    """A mail message or calendar event resolved to a domain-level activity.

    Populated by the inference engine (Chunk 5).  The model is created here
    in Chunk 4 so that type annotations and imports are available before the
    inference engine is written.

    Fields
    ------
    source_type       : "mail" or "calendar"
    client_name       : canonical client name from config/clients.json
    project_name      : engagement/project name, or "" if only client-level
    engagement_label  : human-readable engagement label from clients.json
    stage_hint        : detected stage keyword, or "" if none
    activity_type     : "Complete" | "Upcoming" | "In Progress" | "Action Taken"
    timestamp         : ISO-format string of the message/event time
    reference         : subject line or event title
    sender            : sender display name (mail) or organizer (calendar)
    confidence        : "high" | "medium" | "low"
    is_internal       : True if the activity is internal-only (no client contact)
    """

    source_type: str
    client_name: str
    project_name: str
    engagement_label: str
    stage_hint: str
    activity_type: str
    timestamp: str
    reference: str
    sender: str
    confidence: str
    is_internal: bool
