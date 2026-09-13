from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass(slots=True)
class MailMessage:
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
    attachment_filenames: List[str] = field(default_factory=list)
