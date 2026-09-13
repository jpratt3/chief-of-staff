from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from src.mail_models import MailMessage


@dataclass(slots=True)
class ClassifiedMailMessage:
    folder_name: str
    folder_path: str
    subject: str
    sender_name: str
    sender_email: str
    to: str
    cc: str
    received_time: str
    unread: bool
    entry_id: str
    body_preview: str
    is_sent_by_jordan: bool
    priority: str
    reason: str
    attachment_filenames: List[str] = field(default_factory=list)


def classify_message(message: MailMessage) -> ClassifiedMailMessage:
    subject_lower = message.subject.lower()
    sender_lower = message.sender_name.lower()
    folder_lower = message.folder_name.lower()

    if message.unread:
        priority = "high"
        reason = "unread message"
    elif "acknowledgement" in subject_lower or "receipt:" in subject_lower:
        priority = "low"
        reason = "automated acknowledgement or receipt"
    elif "newsletter" in subject_lower or "news" in sender_lower:
        priority = "low"
        reason = "newsletter or news sender"
    elif folder_lower not in {"inbox", "drafts", "sent items"}:
        priority = "high"
        reason = "client folder message"
    else:
        priority = "normal"
        reason = "default priority"

    return ClassifiedMailMessage(
        folder_name=message.folder_name,
        folder_path=message.folder_path,
        subject=message.subject,
        sender_name=message.sender_name,
        sender_email=message.sender_email,
        to=message.to,
        cc=message.cc,
        received_time=message.received_time,
        unread=message.unread,
        entry_id=message.entry_id,
        body_preview=message.body_preview,
        is_sent_by_jordan=message.is_sent_by_jordan,
        priority=priority,
        reason=reason,
        attachment_filenames=message.attachment_filenames,
    )