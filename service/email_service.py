from __future__ import annotations

import base64
import logging
from typing import Any, Dict, List, Optional, Tuple


class EmailService:
    """Business service for handling email-related events.

    Expects a `gmail_client` that implements at minimum:
      - fetch_message(message_id: str) -> dict  # Gmail message resource
      - fetch_attachment(message_id: str, attachment_id: str) -> bytes | str (optional)

    And an optional `db_service` with:
      - save_email(email: dict) -> None
    """

    def __init__(self, gmail_client, db_service) -> None:
        self.gmail = gmail_client
        self.db = db_service
        self._logger = logging.getLogger("services.email_service")

    def handle_event(self, payload: dict[str, Any], attributes: dict[str, Any] | None = None) -> Optional[dict[str, Any]]:
        """Handle an event (e.g. from Pub/Sub) and return normalized email details.

        The payload should contain a Gmail message id in one of these fields:
          - "message_id" | "gmailMessageId" | payload["message"]["id"]
        """
        message_id = (
            payload.get("message_id")
            or payload.get("gmailMessageId")
            or (payload.get("message", {}).get("id") if isinstance(payload.get("message"), dict) else None)
        )

        if not message_id:
            self._logger.warning("EmailService: missing message_id in payload; nothing to do")
            return None

        if not self.gmail or not hasattr(self.gmail, "fetch_message"):
            self._logger.error("EmailService: gmail_client.fetch_message is not available")
            return None

        try:
            raw_msg: Dict[str, Any] = self.gmail.fetch_message(message_id)
        except Exception:  # noqa: BLE001
            self._logger.exception("EmailService: failed to fetch message %s", message_id)
            return None

        email = self._extract_email_details(raw_msg)

        # Attempt to load attachment bytes if client provides method
        if email.get("attachments"):
            for attachment in email["attachments"]:
                if (
                    attachment.get("data") is None
                    and attachment.get("attachmentId")
                    and hasattr(self.gmail, "fetch_attachment")
                ):
                    try:
                        blob = self.gmail.fetch_attachment(message_id, attachment["attachmentId"])  # type: ignore[call-arg]
                        if isinstance(blob, (bytes, bytearray)):
                            attachment["data"] = base64.b64encode(blob).decode("ascii")
                        elif isinstance(blob, str):
                            attachment["data"] = blob
                    except Exception:  # noqa: BLE001
                        self._logger.exception(
                            "EmailService: failed to fetch attachment '%s'", attachment.get("filename")
                        )

        # Best-effort persistence
        try:
            if self.db and hasattr(self.db, "save_email"):
                self.db.save_email(email)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            self._logger.exception("EmailService: failed to persist email %s", email.get("id"))

        return email

    def _extract_email_details(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize Gmail message resource to a compact, useful shape."""
        payload = msg.get("payload") or {}
        headers_list: List[Dict[str, str]] = payload.get("headers") or []
        headers = {h.get("name", "").lower(): h.get("value", "") for h in headers_list}

        subject = headers.get("subject", "")
        from_ = headers.get("from", "")
        to = headers.get("to", "")
        cc = headers.get("cc", "")
        bcc = headers.get("bcc", "")
        snippet = msg.get("snippet", "")

        body_text, body_html, attachments = self._parse_payload(payload)

        return {
            "id": msg.get("id"),
            "threadId": msg.get("threadId"),
            "historyId": msg.get("historyId"),
            "internalDate": msg.get("internalDate"),
            "subject": subject,
            "from": from_,
            "to": to,
            "cc": cc,
            "bcc": bcc,
            "snippet": snippet,
            "body_text": body_text,
            "body_html": body_html,
            "attachments": attachments,
        }

    def _parse_payload(self, payload: Dict[str, Any]) -> Tuple[str, str, List[Dict[str, Any]]]:
        """Walk Gmail payload parts to extract text bodies and attachments."""
        body_text: str = ""
        body_html: str = ""
        attachments: List[Dict[str, Any]] = []

        def decode_data(b64: Optional[str]) -> str:
            if not b64:
                return ""
            try:
                return base64.urlsafe_b64decode(b64.encode("utf-8")).decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                return ""

        def walk(part: Dict[str, Any]) -> None:
            nonlocal body_text, body_html, attachments

            mime_type = part.get("mimeType")
            body = part.get("body") or {}
            data = body.get("data")

            # Capture inline text bodies (prefer first occurrence)
            if data:
                decoded = decode_data(data)
                if mime_type == "text/plain" and not body_text:
                    body_text = decoded
                elif mime_type == "text/html" and not body_html:
                    body_html = decoded

            # Collect attachments (parts with filename)
            filename = part.get("filename")
            if filename:
                attachments.append(
                    {
                        "filename": filename,
                        "mimeType": mime_type,
                        "size": body.get("size"),
                        "attachmentId": body.get("attachmentId"),
                        # If small inline attachment comes with data, keep it; otherwise we'll try fetching it
                        "data": data if data else None,
                    }
                )

            for sub in part.get("parts") or []:
                walk(sub)

        # Handle top-level part and any subparts
        if payload:
            walk(payload)

        return body_text, body_html, attachments