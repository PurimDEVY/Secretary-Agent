import logging
import os
import time

from dotenv import load_dotenv
from google.api_core.exceptions import NotFound, PermissionDenied, Unauthenticated
from google.cloud import pubsub_v1
from google.oauth2 import service_account
if __package__:
    from infrastructure.pubsub_listener import PubSubListener
else:
    # Running directly: `python diagnostics/testpubsubemail.py`
    import sys
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from infrastructure.pubsub_listener import PubSubListener


# Load environment variables from .env if present
load_dotenv()


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("diagnostics.testpubsubemail")


def _load_credentials():
    """Load GCP credentials, preferring inline JSON, then file-based, else default ADC."""
    key = "GOOGLE_APPLICATION_CREDENTIALS"
    path = os.getenv(key)
    if path and os.path.exists(path):
        try:
            logger.info("Using credentials file from %s: %s", key, path)
            return service_account.Credentials.from_service_account_file(path)
        except Exception:
            logger.exception("Failed to load credentials from %s", key)

    logger.info("Using Application Default Credentials if available")
    return None


def _resolve_subscription_path(
    subscription: str | None,
    project_id: str | None,
    subscription_id: str | None,
) -> str | None:
    # Allow env-driven config
    if subscription is None and project_id is None and subscription_id is None:
        subscription = os.getenv("PUBSUB_SUBSCRIPTION")
        project_id = os.getenv("GCP_PROJECT_ID")
        subscription_id = os.getenv("PUBSUB_SUBSCRIPTION_ID")

    if subscription:
        if subscription.startswith("projects/"):
            return subscription
        if project_id:
            client = pubsub_v1.SubscriberClient()
            return client.subscription_path(project_id, subscription)

    if project_id and subscription_id:
        client = pubsub_v1.SubscriberClient()
        return client.subscription_path(project_id, subscription_id)

    return None


def test_pubsub_connection() -> bool:
    """Attempt to fetch the subscription to verify connectivity and permissions."""
    creds = _load_credentials()
    principal_email = getattr(creds, "service_account_email", None) if creds else None
    if principal_email:
        logger.info("Using service account principal: %s", principal_email)
    subscriber = pubsub_v1.SubscriberClient(credentials=creds) if creds else pubsub_v1.SubscriberClient()

    sub_path = _resolve_subscription_path(
        subscription=os.getenv("PUBSUB_SUBSCRIPTION"),
        project_id=os.getenv("GCP_PROJECT_ID"),
        subscription_id=os.getenv("PUBSUB_SUBSCRIPTION_ID"),
    )
    if not sub_path:
        logger.error(
            "Missing subscription configuration. Set PUBSUB_SUBSCRIPTION or GCP_PROJECT_ID + PUBSUB_SUBSCRIPTION_ID"
        )
        return False

    logger.info("Checking Pub/Sub subscription: %s", sub_path)
    try:
        sub = subscriber.get_subscription(request={"subscription": sub_path})
        logger.info("✅ Connected. Subscription exists. ack_deadline=%s, retain_acked=%s", sub.ack_deadline_seconds, sub.retain_acked_messages)
        return True
    except NotFound:
        logger.exception("❌ Subscription not found: %s", sub_path)
        return False
    except PermissionDenied:
        logger.exception("❌ Permission denied. Check service account roles for Pub/Sub Subscriber on the subscription.")
        return False
    except Unauthenticated:
        logger.exception("❌ Unauthenticated. Credentials missing/invalid.")
        return False
    except Exception:
        logger.exception("❌ Unexpected error while checking subscription")
        return False
    finally:
        subscriber.close()


def test_pubsub_listener_start(duration_seconds: int = 5) -> bool:
    """Smoke test: construct listener, start, wait a bit, then stop.
    Succeeds if no exception occurs during start/stop and env config is valid.
    """
    logger.info("Starting Pub/Sub listener smoke test for %s seconds", duration_seconds)
    try:
        listener = PubSubListener()
        listener.start()
        time.sleep(duration_seconds)
        listener.stop()
        logger.info("✅ Listener started and stopped successfully")
        return True
    except Exception:
        logger.exception("❌ Listener start/stop failed")
        return False


# ---------------------------
# Additional unit-like tests
# ---------------------------

def test_listener_resolve_subscription_path() -> bool:
    """Unit test: ensure subscription path resolution covers short, qualified, and project+id forms.
    Uses a fake SubscriberClient to avoid real GCP calls.
    """
    try:
        import infrastructure.pubsub_listener as _pl

        # Backup and monkeypatch SubscriberClient
        OriginalClient = _pl.pubsub_v1.SubscriberClient

        class FakeSubscriberClient:
            def __init__(self, *_, **__):
                self.closed = False

            def subscription_path(self, project_id: str, sub_id: str) -> str:
                return f"projects/{project_id}/subscriptions/{sub_id}"

            def close(self) -> None:
                self.closed = True

        _pl.pubsub_v1.SubscriberClient = FakeSubscriberClient

        # Case 1: short subscription with project_id
        l1 = _pl.PubSubListener(subscription="shortsub", project_id="my-proj")
        assert l1._subscription_path == "projects/my-proj/subscriptions/shortsub"
        l1.stop()

        # Case 2: fully-qualified subscription path
        fq = "projects/p123/subscriptions/s456"
        l2 = _pl.PubSubListener(subscription=fq)
        assert l2._subscription_path == fq
        l2.stop()

        # Case 3: project + subscription_id
        l3 = _pl.PubSubListener(project_id="projx", subscription_id="suby")
        assert l3._subscription_path == "projects/projx/subscriptions/suby"
        l3.stop()

        return True
    except Exception:
        logger.exception("listener_resolve_subscription_path failed")
        return False
    finally:
        try:
            _pl.pubsub_v1.SubscriberClient = OriginalClient  # type: ignore[name-defined]
        except Exception:
            pass


def test_listener_on_message_ack() -> bool:
    """Unit test: ensure _on_message decodes payload, parses JSON when possible, and acks."""
    try:
        import infrastructure.pubsub_listener as _pl

        # Monkeypatch SubscriberClient to avoid real network usage during instance creation
        OriginalClient = _pl.pubsub_v1.SubscriberClient

        class FakeSubscriberClient:
            def __init__(self, *_, **__):
                pass

            def subscription_path(self, project_id: str, sub_id: str) -> str:
                return f"projects/{project_id}/subscriptions/{sub_id}"

            def close(self) -> None:
                pass

        _pl.pubsub_v1.SubscriberClient = FakeSubscriberClient

        listener = _pl.PubSubListener(subscription="s", project_id="p")

        class FakeMessage:
            def __init__(self, data: bytes):
                self.data = data
                self.attributes = {"key": "val"}
                self.acked = False
                self.ack_id = "ack-123"

            def ack(self):
                self.acked = True

        # JSON payload
        json_msg = FakeMessage(b'{"a": 1}')
        listener._on_message(json_msg)
        assert json_msg.acked is True

        # Non-JSON payload
        txt_msg = FakeMessage(b"hello")
        listener._on_message(txt_msg)
        assert txt_msg.acked is True

        listener.stop()
        return True
    except Exception:
        logger.exception("listener_on_message_ack failed")
        return False
    finally:
        try:
            _pl.pubsub_v1.SubscriberClient = OriginalClient  # type: ignore[name-defined]
        except Exception:
            pass


def test_listener_start_stop_with_fakes() -> bool:
    """Unit test: start() subscribes and spawns waiter thread; stop() cancels and closes client.
    Fully fake to avoid network.
    """
    try:
        import threading
        import infrastructure.pubsub_listener as _pl

        OriginalClient = _pl.pubsub_v1.SubscriberClient

        class FakeFuture:
            def __init__(self):
                self._cancelled = False
                self._event = threading.Event()

            def cancel(self):
                self._cancelled = True
                self._event.set()

            def result(self, timeout: float | None = None):
                self._event.wait(timeout)
                # Simulate termination due to cancel
                raise RuntimeError("cancelled")

        class FakeSubscriberClient:
            def __init__(self, *_, **__):
                self.closed = False

            def subscription_path(self, project_id: str, sub_id: str) -> str:
                return f"projects/{project_id}/subscriptions/{sub_id}"

            def subscribe(self, *_args, **_kwargs):
                return FakeFuture()

            def close(self):
                self.closed = True

        _pl.pubsub_v1.SubscriberClient = FakeSubscriberClient

        listener = _pl.PubSubListener(subscription="s", project_id="p")
        listener.start()
        # Give the waiter thread a moment to call future.result()
        import time as _t
        _t.sleep(0.1)
        listener.stop()

        # After stop, the subscriber should be closed and thread joined
        assert listener._thread is None or not listener._thread.is_alive()
        return True
    except Exception:
        logger.exception("listener_start_stop_with_fakes failed")
        return False
    finally:
        try:
            _pl.pubsub_v1.SubscriberClient = OriginalClient  # type: ignore[name-defined]
        except Exception:
            pass


def test_listener_load_credentials() -> bool:
    """Unit test: _load_credentials uses env var and handles failures gracefully."""
    try:
        import infrastructure.pubsub_listener as _pl

        # Backup
        original_env = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        OriginalFromFile = _pl.service_account.Credentials.from_service_account_file

        # Create a temp fake JSON file path (content does not matter when faked)
        import tempfile
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as tf:
            tf.write("{}")
            temp_path = tf.name

        class FakeCreds:
            service_account_email = "fake@sa.gserviceaccount.com"

        def fake_from_file(_path):  # noqa: ANN001
            return FakeCreds()

        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = temp_path
        _pl.service_account.Credentials.from_service_account_file = fake_from_file  # type: ignore[assignment]
        creds = _pl.PubSubListener._load_credentials()
        assert getattr(creds, "service_account_email", None) == "fake@sa.gserviceaccount.com"

        # Now simulate invalid file path → expect None
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = temp_path + ".missing"
        _pl.service_account.Credentials.from_service_account_file = OriginalFromFile  # restore real; file won't exist
        creds2 = _pl.PubSubListener._load_credentials()
        assert creds2 is None
        return True
    except Exception:
        logger.exception("listener_load_credentials failed")
        return False
    finally:
        try:
            if original_env is None:
                os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
            else:
                os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = original_env
        except Exception:
            pass
        try:
            _pl.service_account.Credentials.from_service_account_file = OriginalFromFile  # type: ignore[name-defined]
        except Exception:
            pass

def main() -> None:
    ok_basic = test_pubsub_connection()
    print("\nResult (basic API):", "OK" if ok_basic else "FAILED")
    ok_listener = test_pubsub_listener_start()
    print("Result (listener start):", "OK" if ok_listener else "FAILED")

    # Unit-like tests
    ok_resolve = test_listener_resolve_subscription_path()
    print("Result (resolve path):", "OK" if ok_resolve else "FAILED")
    ok_ack = test_listener_on_message_ack()
    print("Result (on_message ack):", "OK" if ok_ack else "FAILED")
    ok_fake_start_stop = test_listener_start_stop_with_fakes()
    print("Result (fake start/stop):", "OK" if ok_fake_start_stop else "FAILED")
    ok_creds = test_listener_load_credentials()
    print("Result (load credentials):", "OK" if ok_creds else "FAILED")


if __name__ == "__main__":
    main()


