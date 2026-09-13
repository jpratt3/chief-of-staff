from __future__ import annotations

from typing import Optional

import win32com.client


class OutlookClientError(Exception):
    """Raised when Outlook COM operations fail."""


class OutlookClient:
    """Minimal Outlook desktop COM client for local-only automation."""

    _OL_FOLDER_INBOX = 6
    _OL_FOLDER_CALENDAR = 9
    _OL_MAIL_ITEM = 0

    def __init__(self, outlook_app=None, logger=None) -> None:
        self._outlook = outlook_app
        self._namespace = None
        self._logger = logger

    def connect(self):
        """Connect to Outlook and cache the MAPI namespace."""
        if self._outlook is None:
            try:
                self._outlook = win32com.client.Dispatch("Outlook.Application")
            except Exception as exc:
                raise OutlookClientError(f"Failed to start Outlook.Application: {exc}") from exc

        if self._namespace is None:
            try:
                self._namespace = self._outlook.GetNamespace("MAPI")
            except Exception as exc:
                raise OutlookClientError(f"Failed to get MAPI namespace: {exc}") from exc

        return self._outlook

    def get_namespace(self):
        """Return the cached MAPI namespace, connecting first if needed."""
        self.connect()
        return self._namespace

    def get_current_user(self) -> str:
        """Return the current Outlook user display value."""
        namespace = self.get_namespace()
        try:
            current_user = namespace.CurrentUser
            return str(getattr(current_user, "Name", "")).strip()
        except Exception as exc:
            raise OutlookClientError(f"Failed to resolve current Outlook user: {exc}") from exc

    def get_inbox(self):
        """Return the default Inbox folder."""
        namespace = self.get_namespace()
        try:
            return namespace.GetDefaultFolder(self._OL_FOLDER_INBOX)
        except Exception as exc:
            raise OutlookClientError(f"Failed to get default Inbox: {exc}") from exc

    def get_calendar(self):
        """Return the default Calendar folder."""
        namespace = self.get_namespace()
        try:
            return namespace.GetDefaultFolder(self._OL_FOLDER_CALENDAR)
        except Exception as exc:
            raise OutlookClientError(f"Failed to get default Calendar: {exc}") from exc

    def create_mail_item(
        self,
        subject: str = "",
        body: str = "",
        to: Optional[str] = None,
        cc: Optional[str] = None,
        bcc: Optional[str] = None,
        save: bool = False,
    ):
        """Create a new Outlook mail item and optionally save it as a draft."""
        self.connect()

        try:
            mail_item = self._outlook.CreateItem(self._OL_MAIL_ITEM)
            mail_item.Subject = subject or ""
            mail_item.Body = body or ""

            if to:
                mail_item.To = to
            if cc:
                mail_item.CC = cc
            if bcc:
                mail_item.BCC = bcc

            if save:
                mail_item.Save()

            return mail_item
        except Exception as exc:
            raise OutlookClientError(f"Failed to create Outlook mail item: {exc}") from exc
