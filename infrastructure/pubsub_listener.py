import json
import logging
import os
import threading
from typing import Any, Callable, Optional

from google.cloud import pubsub_v1
from google.oauth2 import service_account
try:
    from google.api_core import exceptions as gax_exceptions
except Exception:  # pragma: no cover - optional at runtime
    gax_exceptions = None  # type: ignore[assignment]
try:
    from googleapiclient.errors import HttpError  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - optional at runtime
    HttpError = None  # type: ignore[assignment]


class PubSubListener:
    def __init__(
        self,
        subscription: Optional[str] = None,
        project_id: Optional[str] = None,
        subscription_id: Optional[str] = None,
        message_handler: Optional[Callable[[Any, dict[str, Any]], None]] = None,
    ) -> None:
        # Allow configuration purely via environment variables when not passed explicitly
        if subscription is None and project_id is None and subscription_id is None:
            subscription = os.getenv("PUBSUB_SUBSCRIPTION")
            project_id = os.getenv("GCP_PROJECT_ID")
            subscription_id = os.getenv("PUBSUB_SUBSCRIPTION_ID")

        self._creds = self._load_credentials()
        self._subscriber = pubsub_v1.SubscriberClient(credentials=self._creds) if self._creds else pubsub_v1.SubscriberClient()
        self._subscription_path = self._resolve_subscription_path(subscription, project_id, subscription_id)
        if not self._subscription_path:
            raise ValueError("Pub/Sub subscription not provided. Set PUBSUB_SUBSCRIPTION or GCP_PROJECT_ID + PUBSUB_SUBSCRIPTION_ID")

        self._future: Optional[pubsub_v1.subscriber.futures.StreamingPullFuture] = None
        self._thread: Optional[threading.Thread] = None
        self._handler = message_handler

    def start(self) -> None:
        logging.info(f"Starting Pub/Sub listener: {self._subscription_path}")
        # Flow control via environment
        max_messages = int(os.getenv("PUBSUB_MAX_MESSAGES", "10"))
        max_bytes = int(os.getenv("PUBSUB_MAX_BYTES", str(10 * 1024 * 1024)))
        max_lease = int(os.getenv("PUBSUB_MAX_LEASE_DURATION", "600"))
        flow_control = pubsub_v1.types.FlowControl(
            max_messages=max_messages,
            max_bytes=max_bytes,
            max_lease_duration=max_lease,
        )
        self._future = self._subscriber.subscribe(
            self._subscription_path,
            callback=self._on_message,
            flow_control=flow_control,
        )
        self._thread = threading.Thread(target=self._wait_forever, name="pubsub-listener", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        logging.info("Stopping Pub/Sub listener...")
        if self._future:
            self._future.cancel()
            try:
                # Wait briefly for background threads to quiesce after cancel
                self._future.result(timeout=5)
            except Exception:
                # Expected on cancellation or timeout; proceed to close channel
                pass
        # Close the underlying gRPC channel after cancellation settles
        self._subscriber.close()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logging.info("Pub/Sub listener stopped.")

    def _wait_forever(self) -> None:
        try:
            assert self._future is not None
            self._future.result()
        except Exception as e:
            # This will also trigger if .cancel() is called; that's OK on shutdown.
            logging.warning(f"Pub/Sub listener terminated: {e}")

    def _on_message(self, message: pubsub_v1.subscriber.message.Message) -> None:
        try:
            payload = message.data.decode("utf-8") if message.data else ""
            attributes = dict(message.attributes or {})
            logging.info(f"Received message: ack_id={message.ack_id}, size={len(payload)}")
            # Try parse JSON if applicable
            try:
                parsed = json.loads(payload)
                logging.debug(f"Parsed JSON: {parsed}")
            except Exception:
                parsed = payload
                logging.debug("Message is not valid JSON; delivering raw text.")
            # Log attributes at debug level to keep visibility and satisfy linters
            logging.debug(f"Attributes: {attributes}")
            # Route to handler if provided
            if self._handler:
                try:
                    self._handler(parsed, attributes)
                except Exception as handler_exc:  # noqa: BLE001
                    if self._is_transient_error(handler_exc):
                        logging.warning("Transient error in message handler; NACK for retry: %s", handler_exc)
                        message.nack()
                        return
                    logging.exception("Non-retryable error in message handler; ACK to drop")
                    message.ack()
                    return
            # Default behavior or successful handling
            message.ack()
        except Exception as e:
            logging.exception(f"Error handling message: {e}")
            # If unexpected error here, prefer NACK to allow retry once
            try:
                message.nack()
            except Exception:
                logging.debug("Failed to NACK after exception; possibly already settled")

    def _is_transient_error(self, exc: Exception) -> bool:
        """Best-effort classification of transient errors suitable for retry via NACK."""
        # Network and timeout-like errors
        if isinstance(exc, (TimeoutError, ConnectionError)):
            return True
        # google.api_core exceptions with retryable nature
        if gax_exceptions is not None and isinstance(
            exc,
            (
                getattr(gax_exceptions, "ServiceUnavailable", tuple()),
                getattr(gax_exceptions, "DeadlineExceeded", tuple()),
                getattr(gax_exceptions, "InternalServerError", tuple()),
                getattr(gax_exceptions, "TooManyRequests", tuple()),
            ),
        ):
            return True
        # googleapiclient HttpError: retry on 5xx and 429
        if HttpError is not None and isinstance(exc, HttpError):  # type: ignore[misc]
            try:
                status = int(getattr(exc, "status_code", None) or getattr(exc, "resp", {}).status)
            except Exception:
                status = None
            if status and (status >= 500 or status == 429):
                return True
        return False

    @staticmethod
    def _load_credentials():
        key = "GOOGLE_APPLICATION_CREDENTIALS"
        file_path = os.getenv(key)
        if file_path and os.path.isfile(file_path):
            try:
                creds = service_account.Credentials.from_service_account_file(file_path)
                logging.info(f"Using GCP service account from {key}={file_path}.")
                return creds
            except Exception as e:
                logging.error(f"Invalid service account file at {file_path}: {e}")



    def _resolve_subscription_path(
        self,
        subscription: Optional[str],
        project_id: Optional[str],
        subscription_id: Optional[str],
    ) -> Optional[str]:
        if subscription:
            # Accept fully-qualified path or short name; prefer fully-qualified.
            if subscription.startswith("projects/"):
                return subscription
            if project_id:
                return self._subscriber.subscription_path(project_id, subscription)
        if project_id and subscription_id:
            return self._subscriber.subscription_path(project_id, subscription_id)
        return None
