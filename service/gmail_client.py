from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Dict

from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials


class GmailApiClient:
    """Thin Gmail API client providing message and attachment fetching.

    Credentials are loaded from an OAuth token JSON file produced by the diagnostics/setup_gmail_watch.py script.
    """

    def __init__(self, creds: Credentials) -> None:
        self._service = build("gmail", "v1", credentials=creds)

    @classmethod
    def from_token_file(cls, token_file: str | Path) -> "GmailApiClient":
        token_path = Path(token_file)
        if not token_path.exists():
            raise FileNotFoundError(f"Token file not found: {token_file}")
        creds = Credentials.from_authorized_user_file(str(token_path))
        # Lazy import to avoid hard dependency at import time
        import logging as _logging
        _logging.info("GmailApiClient: loaded token from %s", token_path)
        return cls(creds)

    @classmethod
    def from_email_and_dir(cls, email_address: str, tokens_dir: str | Path) -> "GmailApiClient":
        token_file = Path(tokens_dir) / f"{email_address}.json"
        return cls.from_token_file(token_file)

    def fetch_message(self, message_id: str) -> Dict[str, Any]:
        return (
            self._service
            .users()
            .messages()
            .get(userId="me", id=message_id, format="full")
            .execute()
        )

    def fetch_attachment(self, message_id: str, attachment_id: str) -> bytes:
        obj = (
            self._service
            .users()
            .messages()
            .attachments()
            .get(userId="me", messageId=message_id, id=attachment_id)
            .execute()
        )
        data = obj.get("data")
        if not data:
            return b""
        # Gmail API returns base64url data; decode to raw bytes
        return base64.urlsafe_b64decode(data.encode("utf-8"))

    def list_history_since(self, start_history_id: str) -> tuple[list[str], str]:
        """Return (message_ids, new_last_history_id) since start_history_id.

        Aggregates messagesAdded across all pages. If no history, returns ([], start_history_id).
        """
        service = self._service
        user = "me"
        page_token: str | None = None
        message_ids: set[str] = set()
        last_history_id: str = start_history_id

        while True:
            req = service.users().history().list(
                userId=user,
                startHistoryId=start_history_id,
                pageToken=page_token,
            )
            resp = req.execute()
            for h in resp.get("history", []):
                # Track the largest historyId we've seen
                hid = str(h.get("id", last_history_id))
                if hid > last_history_id:
                    last_history_id = hid
                for added in h.get("messagesAdded", []) or []:
                    msg = added.get("message") or {}
                    mid = msg.get("id")
                    if mid:
                        message_ids.add(mid)
            page_token = resp.get("nextPageToken")
            if not page_token:
                break

        # Log compact summary for investigation
        try:
            import logging as _logging
            _logging.info(
                "GmailApiClient.history: start=%s → msgs=%d last_hid=%s",
                start_history_id,
                len(message_ids),
                last_history_id,
            )
        except Exception:
            pass
        return list(message_ids), last_history_id


