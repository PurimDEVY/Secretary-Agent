from __future__ import annotations

from typing import Any, Dict


class DbService:
    """Minimal DB service placeholder for idempotency and persistence hooks.

    Replace in production with real implementation using your DB driver.
    """

    def __init__(self) -> None:  # pragma: no cover - placeholder
        pass

    def has_processed(self, message_id: str) -> bool:
        """Return True if the message_id has been processed already."""
        return False

    def mark_processed(self, message_id: str) -> None:
        """Record that the message_id is processed."""
        # Implement persistence
        return None

    def save_email(self, email: Dict[str, Any]) -> None:
        """Persist normalized email object."""
        # Implement persistence
        return None
