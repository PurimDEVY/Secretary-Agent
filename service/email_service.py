from typing import Any
class EmailService:
    def __init__(self, gmail_client, db_service) -> None:
        self.gmail = gmail_client
        self.db = db_service

    def handle_event(self, payload: dict[str, Any], attributes: dict[str, Any] | None = None) -> None:
        pass