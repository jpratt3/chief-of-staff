from __future__ import annotations

from typing import Any

from src.outlook_client import OutlookClient, OutlookClientError


class MailFolderReaderError(Exception):
    """Raised when mail folder discovery fails."""


class MailFolderReader:
    """Minimal reader for Outlook mailbox folders."""

    def __init__(self, outlook_client: OutlookClient) -> None:
        self._client = outlook_client

    def list_top_level_folders(self) -> list[dict[str, Any]]:
        try:
            root_folder = self._get_root_folder()
        except Exception as exc:
            raise MailFolderReaderError(str(exc)) from exc

        folders: list[dict[str, Any]] = []

        for folder in root_folder.Folders:
            try:
                folders.append(self._folder_to_dict(folder, depth=0))
            except Exception:
                continue

        return folders

    def list_all_folders(self) -> list[dict[str, Any]]:
        try:
            root_folder = self._get_root_folder()
        except Exception as exc:
            raise MailFolderReaderError(str(exc)) from exc

        folders: list[dict[str, Any]] = []

        for folder in root_folder.Folders:
            self._walk_folder(folder=folder, depth=0, results=folders)

        return folders

    def list_included_branches(
        self,
        include_root_folder_names: list[str],
        exclude_root_folder_names: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        try:
            root_folder = self._get_root_folder()
        except Exception as exc:
            raise MailFolderReaderError(str(exc)) from exc

        include_set = {name.casefold() for name in include_root_folder_names}
        exclude_set = {
            "deleted items",
            "junk email",
            "outbox",
            "yammer root",
            "conversation history",
            "externalcontacts",
            "notes",
            "tasks",
            "journal",
            "contacts",
            "calendar",
            "files",
        }

        if exclude_root_folder_names:
            exclude_set.update(name.casefold() for name in exclude_root_folder_names)

        folders: list[dict[str, Any]] = []

        for folder in root_folder.Folders:
            try:
                folder_name = (getattr(folder, "Name", "") or "").strip()
                normalized_name = folder_name.casefold()

                if normalized_name in exclude_set:
                    continue

                if normalized_name not in include_set:
                    continue

                self._walk_folder(folder=folder, depth=0, results=folders)
            except Exception:
                continue

        return folders

    def _get_root_folder(self):
        try:
            inbox = self._client.get_inbox()
            store = inbox.Store
            return store.GetRootFolder()
        except OutlookClientError as exc:
            raise MailFolderReaderError(f"Failed to access Outlook folders: {exc}") from exc
        except Exception as exc:
            raise MailFolderReaderError(f"Failed to prepare folder discovery: {exc}") from exc

    def _walk_folder(self, folder, depth: int, results: list[dict[str, Any]]) -> None:
        try:
            results.append(self._folder_to_dict(folder, depth=depth))
        except Exception:
            return

        try:
            for child in folder.Folders:
                self._walk_folder(folder=child, depth=depth + 1, results=results)
        except Exception:
            return

    def _folder_to_dict(self, folder, depth: int) -> dict[str, Any]:
        items = getattr(folder, "Items", None)
        item_count = 0

        if items is not None:
            try:
                item_count = int(items.Count)
            except Exception:
                item_count = 0

        return {
            "name": getattr(folder, "Name", "") or "",
            "full_path": getattr(folder, "FolderPath", "") or "",
            "item_count": item_count,
            "depth": depth,
        }
