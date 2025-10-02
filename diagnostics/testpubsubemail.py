import json
import logging
import os

from dotenv import load_dotenv
from google.api_core.exceptions import NotFound, PermissionDenied, Unauthenticated
from google.cloud import pubsub_v1
from google.oauth2 import service_account


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
    # gj = os.getenv("GCP_SERVICE_ACCOUNT_JSON")
    # if gj:
    #     # 1) Try inline JSON directly
    #     try:
    #         info = json.loads(gj)
    #         logger.info("Using credentials from GCP_SERVICE_ACCOUNT_JSON (inline JSON)")
    #         return service_account.Credentials.from_service_account_info(info)
    #     except Exception:
    #         logger.debug("GCP_SERVICE_ACCOUNT_JSON is not valid inline JSON; will try as path/base64.")

    #     # 2) If the value is a path to a file, read it
    #     try:
    #         if os.path.exists(gj):
    #             logger.info("Using credentials file at path from GCP_SERVICE_ACCOUNT_JSON: %s", gj)
    #             return service_account.Credentials.from_service_account_file(gj)
    #     except Exception:
    #         logger.exception("Failed to read credentials file referenced by GCP_SERVICE_ACCOUNT_JSON")

    #     # 3) Try base64-encoded JSON
    #     try:
    #         import base64
    #         decoded = base64.b64decode(gj).decode("utf-8")
    #         info = json.loads(decoded)
    #         logger.info("Using credentials from base64-encoded GCP_SERVICE_ACCOUNT_JSON")
    #         return service_account.Credentials.from_service_account_info(info)
    #     except Exception:
    #         logger.exception("Invalid GCP_SERVICE_ACCOUNT_JSON (neither JSON, file path, nor base64 JSON)")

    # 4) Try explicit file-based env vars
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


def main() -> None:
    ok = test_pubsub_connection()
    print("\nResult:", "OK" if ok else "FAILED")


if __name__ == "__main__":
    main()


