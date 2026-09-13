from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class CalendarEvent:
    subject: str
    start: str
    end: str
    location: str
    organizer: str
    is_organizer_jordan: bool
    attendees: str
    entry_id: str
    body_preview: str
