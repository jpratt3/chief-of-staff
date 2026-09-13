from __future__ import annotations

from datetime import datetime

from src.calendar_models import CalendarEvent
from src.outlook_client import OutlookClient, OutlookClientError

_BODY_PREVIEW_LENGTH = 300

# Outlook MeetingStatus constants
_OL_NON_MEETING = 0   # appointment (Jordan created, no invites sent)
_OL_MEETING = 1       # meeting Jordan organized and sent invites for


class CalendarReaderError(Exception):
    """Raised when Calendar reads fail."""


class CalendarReader:
    """Read Outlook Calendar items with richer fields."""

    def __init__(self, outlook_client: OutlookClient) -> None:
        self._client = outlook_client

    def get_upcoming_events(self, limit: int = 10) -> list[CalendarEvent]:
        if limit <= 0:
            return []

        try:
            calendar = self._client.get_calendar()
            items = calendar.Items
            items.IncludeRecurrences = True
            items.Sort("[Start]")

            now = datetime.now().strftime("%m/%d/%Y %I:%M %p")
            restricted_items = items.Restrict(f"[Start] >= '{now}'")
        except OutlookClientError as exc:
            raise CalendarReaderError(f"Failed to access Calendar: {exc}") from exc
        except Exception as exc:
            raise CalendarReaderError(f"Failed to prepare Calendar items: {exc}") from exc

        events: list[CalendarEvent] = []
        count = 0

        for item in restricted_items:
            if count >= limit:
                break

            event = self._build_event(item)
            if event is not None:
                events.append(event)
                count += 1

        return events

    def get_events_between(
        self,
        start_dt: datetime,
        end_dt: datetime,
        limit: int = 100,
    ) -> list[CalendarEvent]:
        if limit <= 0:
            return []

        try:
            calendar = self._client.get_calendar()
            items = calendar.Items
            items.IncludeRecurrences = True
            items.Sort("[Start]")

            start_str = start_dt.strftime("%m/%d/%Y %I:%M %p")
            end_str = end_dt.strftime("%m/%d/%Y %I:%M %p")
            restricted_items = items.Restrict(
                f"[Start] >= '{start_str}' AND [Start] <= '{end_str}'"
            )
        except OutlookClientError as exc:
            raise CalendarReaderError(f"Failed to access Calendar: {exc}") from exc
        except Exception as exc:
            raise CalendarReaderError(f"Failed to prepare Calendar items: {exc}") from exc

        events: list[CalendarEvent] = []
        count = 0

        for item in restricted_items:
            if count >= limit:
                break

            event = self._build_event(item)
            if event is not None:
                events.append(event)
                count += 1

        return events

    def _build_event(self, item) -> CalendarEvent | None:
        """Build a CalendarEvent from a COM AppointmentItem. Returns None on error."""
        try:
            meeting_status = int(getattr(item, "MeetingStatus", _OL_NON_MEETING))
            is_organizer_jordan = meeting_status in (_OL_NON_MEETING, _OL_MEETING)

            required = getattr(item, "RequiredAttendees", "") or ""
            optional = getattr(item, "OptionalAttendees", "") or ""
            attendee_parts = [p.strip() for p in (required + "; " + optional).split(";") if p.strip()]
            attendees = "; ".join(attendee_parts)

            raw_body = getattr(item, "Body", "") or ""
            body_preview = raw_body[:_BODY_PREVIEW_LENGTH].replace("\r\n", " ").replace("\n", " ").strip()

            return CalendarEvent(
                subject=getattr(item, "Subject", "") or "",
                start=str(getattr(item, "Start", "") or ""),
                end=str(getattr(item, "End", "") or ""),
                location=getattr(item, "Location", "") or "",
                organizer=getattr(item, "Organizer", "") or "",
                is_organizer_jordan=is_organizer_jordan,
                attendees=attendees,
                entry_id=getattr(item, "EntryID", "") or "",
                body_preview=body_preview,
            )
        except Exception:
            return None
