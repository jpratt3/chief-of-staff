from __future__ import annotations

"""Briefing sender — Chunk 9.

Single responsibility: take the plain-text briefing string produced by
briefing_builder.build_briefing() and send it as an Outlook email.

Design decisions locked in Chunk 9 pre-build:
- Always self-send (recipient is the caller's own address).
- Plain-text body; format logic is isolated here for easy HTML upgrade later.
- Raise on send failure — the run should abort visibly if the email did not go out.
"""

from src.outlook_client import OutlookClient, OutlookClientError


class BriefingSendError(Exception):
    """Raised when the briefing email cannot be sent."""


def send_briefing(
    briefing_text: str,
    outlook_client: OutlookClient,
    recipient: str,
    subject: str,
) -> None:
    """Send the daily briefing as a plain-text Outlook email.

    Args:
        briefing_text: Plain-text string returned by build_briefing().
        outlook_client: Connected OutlookClient instance.
        recipient: Destination address (typically Jordan's own address).
        subject: Email subject line.

    Raises:
        BriefingSendError: If creating or sending the mail item fails.
    """
    if not briefing_text or not briefing_text.strip():
        raise BriefingSendError("Briefing text is empty — refusing to send a blank email.")

    try:
        mail_item = outlook_client.create_mail_item(
            subject=subject,
            body=briefing_text,
            to=recipient,
        )
    except OutlookClientError as exc:
        raise BriefingSendError(f"Failed to create Outlook mail item: {exc}") from exc

    try:
        mail_item.Send()
    except Exception as exc:
        raise BriefingSendError(f"Outlook Send() call failed: {exc}") from exc
