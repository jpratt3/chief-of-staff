from __future__ import annotations

from datetime import datetime
from typing import Any, List, Set

from src.mail_models import MailMessage
from src.outlook_client import OutlookClient, OutlookClientError

_BODY_PREVIEW_LENGTH = 300


class MailReaderError(Exception):
    """Raised when multi-folder mail reads fail."""


def _collect_team_emails(client_config: Any) -> Set[str]:
    """
    Flatten all team member email addresses from client_config into a
    lowercase set.  Includes Jordan's email from _meta.jordan_email.

    client_config is the ClientConfig object from client_resolver.
    Falls back gracefully if the attribute is missing.
    """
    emails: Set[str] = set()
    raw = getattr(client_config, "raw", None) or {}

    # Jordan's email from _meta
    jordan_email = str(raw.get("_meta", {}).get("jordan_email") or "").strip().lower()
    if jordan_email:
        emails.add(jordan_email)

    for client in raw.get("clients", []):
        team = client.get("team") or {}
        for key, entry in team.items():
            if isinstance(entry, list):
                for member in entry:
                    email = str(member.get("email") or "").strip().lower()
                    if email:
                        emails.add(email)
            elif isinstance(entry, dict):
                email = str(entry.get("email") or "").strip().lower()
                if email:
                    emails.add(email)

    return emails


def _read_attachment_filenames(item: Any) -> List[str]:
    """
    Iterate item.Attachments and collect FileName values.
    Returns an empty list if Attachments is unavailable or iteration fails.
    """
    filenames: List[str] = []
    try:
        attachments = getattr(item, "Attachments", None)
        if attachments is None:
            return filenames
        count = getattr(attachments, "Count", 0) or 0
        for i in range(1, count + 1):
            try:
                attachment = attachments.Item(i)
                name = getattr(attachment, "FileName", "") or ""
                if name:
                    filenames.append(str(name))
            except Exception:
                continue
    except Exception:
        pass
    return filenames


class MailReader:
    """Read messages from selected Outlook mail folders."""

    def __init__(self, outlook_client: OutlookClient) -> None:
        self._client = outlook_client

    def get_messages_since(
        self,
        folder_paths: list[str],
        since_dt: datetime,
        limit_per_folder: int = 50,
        client_config: Any = None,
    ) -> list[MailMessage]:
        if limit_per_folder <= 0 or not folder_paths:
            return []

        # Build team email set once for all folders
        team_emails: Set[str] = (
            _collect_team_emails(client_config) if client_config is not None else set()
        )

        results: list[MailMessage] = []
        since_str = since_dt.strftime("%m/%d/%Y %I:%M %p")

        for folder_path in folder_paths:
            try:
                folder = self._resolve_folder_by_path(folder_path)
                items = folder.Items
                items.Sort("[ReceivedTime]", True)
                restricted_items = items.Restrict(f"[ReceivedTime] >= '{since_str}'")
            except Exception:
                continue

            folder_name = getattr(folder, "Name", "") or ""
            folder_full_path = getattr(folder, "FolderPath", "") or ""
            is_sent_folder = folder_name.lower() == "sent items"
            count = 0

            for item in restricted_items:
                if count >= limit_per_folder:
                    break

                try:
                    raw_body = getattr(item, "Body", "") or ""
                    body_preview = raw_body[:_BODY_PREVIEW_LENGTH].replace("\r\n", " ").replace("\n", " ").strip()
                    sender_email = (getattr(item, "SenderEmailAddress", "") or "").strip().lower()

                    # Scan attachments when sender is a known team member
                    attachment_filenames: List[str] = []
                    if team_emails and sender_email in team_emails:
                        attachment_filenames = _read_attachment_filenames(item)

                    message = MailMessage(
                        folder_name=folder_name,
                        folder_path=folder_full_path,
                        subject=getattr(item, "Subject", "") or "",
                        sender_name=getattr(item, "SenderName", "") or "",
                        sender_email=sender_email,
                        to=getattr(item, "To", "") or "",
                        cc=getattr(item, "CC", "") or "",
                        received_time=str(getattr(item, "ReceivedTime", "") or ""),
                        unread=bool(getattr(item, "UnRead", False)),
                        entry_id=getattr(item, "EntryID", "") or "",
                        body_preview=body_preview,
                        is_sent_by_jordan=is_sent_folder,
                        attachment_filenames=attachment_filenames,
                    )
                    results.append(message)
                    count += 1
                except Exception:
                    continue

        return results

    def _resolve_folder_by_path(self, folder_path: str):
        try:
            inbox = self._client.get_inbox()
            store = inbox.Store
            root_folder = store.GetRootFolder()
        except OutlookClientError as exc:
            raise MailReaderError(f"Failed to access Outlook store: {exc}") from exc
        except Exception as exc:
            raise MailReaderError(f"Failed to prepare folder resolution: {exc}") from exc

        normalized_path = folder_path.strip("\\")
        parts = normalized_path.split("\\")

        if len(parts) < 2:
            raise MailReaderError(f"Invalid folder path: {folder_path}")

        current_folder = root_folder

        for part in parts[1:]:
            try:
                current_folder = current_folder.Folders[part]
            except Exception as exc:
                raise MailReaderError(f"Failed to resolve folder path: {folder_path}") from exc

        return current_folder
