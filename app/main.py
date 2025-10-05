from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
import logging
import os

from diagnostics.testdbconnection import test_gcp, test_db_connection, test_gemini_api
from diagnostics.testpubsubemail import main as run_pubsub_tests
from infrastructure.pubsub_listener import PubSubListener
from service.email_service import EmailService
from service.gmail_client import GmailApiClient
from service.db_service import DbService
from service.gmail_watch_service import GmailWatchService

async def index(request):
    return JSONResponse({"message": "Hello from secretary-agent"})

app = Starlette(routes=[Route("/", index)])


@app.on_event("startup")
async def startup():
    # Flags to control startup behaviors (accept only 'true'/'false')
    run_diagnostics = os.getenv("RUN_STARTUP_DIAGNOSTICS", "true").strip().lower() == "true"
    run_listener = os.getenv("RUN_PUBSUB_LISTENER", "true").strip().lower() == "true"
    logging.info(
        "Startup flags: RUN_STARTUP_DIAGNOSTICS=%s, RUN_PUBSUB_LISTENER=%s",
        run_diagnostics,
        run_listener,
    )

    if run_diagnostics:
        try:
        # Run diagnostics at app startup (works regardless of how uvicorn is launched)
            test_gcp()
            test_db_connection()
            test_gemini_api()
            run_pubsub_tests()
        except Exception:
            logging.exception("diagnostics failed")
    else:
        logging.info("Startup diagnostics disabled via RUN_STARTUP_DIAGNOSTICS")

    if run_listener:
        try:
            # Compose dependencies for message handling
            db_service = DbService()

            # Prepare Gmail clients per configured users (comma-separated)
            gmail_tokens_dir = os.getenv("GMAIL_TOKENS_DIR")
            user_emails = os.getenv("GMAIL_WATCH_USERS").strip()
            logging.info("Gmail tokens dir: %s", gmail_tokens_dir)
            logging.info("Configured GMAIL_WATCH_USERS raw: %s", user_emails or "<empty>")
            gmail_clients_by_email: dict[str, GmailApiClient] = {}
            if user_emails:
                for email in [e.strip() for e in user_emails.split(",") if e.strip()]:
                    try:
                        gmail_clients_by_email[email] = GmailApiClient.from_email_and_dir(email, gmail_tokens_dir)
                    except Exception:  # noqa: BLE001
                        logging.exception("Failed to initialize Gmail client for %s", email)
            if gmail_clients_by_email:
                logging.info("Initialized %d Gmail client(s): %s", len(gmail_clients_by_email), ", ".join(sorted(gmail_clients_by_email.keys())))
            else:
                logging.info("No Gmail clients initialized from GMAIL_WATCH_USERS")

            # Fallback: if only one user is configured, pass that client to service for direct usage
            single_gmail_client = None
            if len(gmail_clients_by_email) == 1:
                single_gmail_client = next(iter(gmail_clients_by_email.values()))
                logging.info("Using single Gmail client optimization for the only configured user")

            email_service = EmailService(gmail_client=single_gmail_client, db_service=db_service)
            logging.info("EmailService initialized (single_gmail_client=%s)", bool(single_gmail_client))


            # Initialize Gmail Watch Service for automatic renewal
            project_id = os.getenv("GCP_PROJECT_ID")
            topic_name = os.getenv("PUBSUB_TOPIC_NAME")
            
            if project_id and topic_name:
                watch_service = GmailWatchService(
                    tokens_dir=gmail_tokens_dir,
                    project_id=project_id, 
                    topic_name=topic_name
                )
                logging.info("GmailWatchService created (project_id=%s, topic=%s, tokens_dir=%s)", project_id, topic_name, gmail_tokens_dir)
                
                # Setup initial watches for all users
                results = watch_service.setup_all_watches()
                for email, success in results.items():
                    if success:
                        logging.info(f"✅ Gmail watch setup for {email}")
                    else:
                        logging.error(f"❌ Failed to setup Gmail watch for {email}")
                logging.info("Gmail watch setup summary: %d/%d succeeded", sum(1 for v in results.values() if v), len(results))
                
                # Start automatic renewal (check every hour)
                watch_service.start_automatic_renewal(check_interval_hours=1)
                app.state.gmail_watch_service = watch_service
                logging.info("Gmail Watch Service started with automatic renewal")
            else:
                logging.warning("GCP_PROJECT_ID or PUBSUB_TOPIC_NAME not set - Gmail watch renewal disabled")


            def handler(event_payload, attributes):  # noqa: ANN001
                # Gmail push sends attributes with emailAddress and historyId; payload isn't JSON
                # Normalize attribute keys to lowercase to avoid case-sensitivity issues across publishers/clients
                logging.info(f"handler: event_payload={event_payload} attributes={attributes}")
                email_address = None
                history_id = None
                if isinstance(attributes, dict):
                    try:
                        _attrs = {str(k).lower(): v for k, v in attributes.items()}
                        logging.info("handler: normalized attributes=%s", _attrs)
                        email_address = _attrs.get("emailaddress") or _attrs.get("email_address")
                        history_id = _attrs.get("historyid") or _attrs.get("history_id")
                        # Fallback to canonical-case if available
                        if not email_address:
                            email_address = attributes.get("emailAddress")
                        if not history_id:
                            history_id = attributes.get("historyId")
                        logging.info("handler: resolved email_address=%s history_id=%s", email_address, history_id)
                    except Exception:  # noqa: BLE001
                        logging.exception("Failed to normalize Pub/Sub attributes")

                # Handle direct JSON with message_id if provided (non-Gmail sources)
                if isinstance(event_payload, dict) and (event_payload.get("message_id") or event_payload.get("gmailMessageId")):
                    email_service.handle_event(event_payload, attributes)
                    return

                # Fallback path: some environments deliver Gmail fields inside payload JSON
                if (not email_address or not history_id) and isinstance(event_payload, dict):
                    try:
                        email_address = email_address or event_payload.get("emailAddress") or event_payload.get("email_address")
                        hid_val = event_payload.get("historyId") or event_payload.get("history_id")
                        if hid_val is not None:
                            history_id = str(hid_val)
                        logging.info("handler: fallback from event_payload → email_address=%s history_id=%s", email_address, history_id)
                    except Exception:
                        logging.exception("handler: failed extracting fallback email/history from payload")

                if not email_address or not history_id:
                    logging.info(
                        "Gmail push notification received without expected attributes: %s",
                        attributes,
                    )
                else:
                    logging.info("Gmail push notification received: email=%s historyId=%s", email_address, history_id)

                # List history to find new message IDs and process them
                try:
                    if not email_address or not history_id:
                        return
                    gmail_client = gmail_clients_by_email.get(email_address)
                    if not gmail_client:
                        logging.warning("No Gmail client for email %s; ensure token exists and GMAIL_WATCH_USERS includes it", email_address)
                        return

                    # Load last historyId from tokens dir
                    tokens_dir = os.getenv("GMAIL_TOKENS_DIR", "secrets/tokens")
                    state_file = os.path.join(tokens_dir, f"{email_address}.state.json")
                    logging.info("handler: state_file path=%s", state_file)
                    import json as _json
                    last_history_id = None
                    try:
                        with open(state_file, "r", encoding="utf-8") as f:
                            state = _json.load(f)
                            last_history_id = str(state.get("watchResponse", {}).get("historyId") or state.get("lastHistoryId"))
                            logging.info("handler: loaded last_history_id from state=%s", last_history_id)
                    except Exception:
                        last_history_id = None
                        logging.info("handler: no existing state file or failed to parse; will fallback to push historyId")

                    # Fallback: if no stored history, start from current push history to avoid backfill storm
                    start_hid = last_history_id or str(history_id)
                    # Time the history call for observability
                    import time as _t
                    _t0 = _t.time()
                    message_ids, new_last_hid = gmail_client.list_history_since(start_hid)
                    _elapsed = (_t.time() - _t0) * 1000.0
                    logging.info(
                        "History scan for %s from %s → %d new messages (new_last_hid=%s) in %.1fms",
                        email_address,
                        start_hid,
                        len(message_ids),
                        new_last_hid,
                        _elapsed,
                    )

                    # Update state with new lastHistoryId
                    try:
                        new_state = {"emailAddress": email_address, "lastHistoryId": new_last_hid}
                        with open(state_file, "w", encoding="utf-8") as f:
                            _json.dump(new_state, f)
                        logging.info("handler: wrote lastHistoryId=%s to state file", new_last_hid)
                    except Exception:
                        logging.exception("Failed to update state for %s", email_address)

                    # Process messages via EmailService if we have a single gmail client injected
                    if message_ids:
                        logging.info("Processing %d message(s) for %s", len(message_ids), email_address)
                        for mid in message_ids:
                            logging.info("Dispatching EmailService for message_id=%s (email=%s)", mid, email_address)
                            payload = {"message_id": mid}
                            # Use the per-email Gmail client so we can fetch the message and log preview
                            per_email_service = EmailService(gmail_client=gmail_client, db_service=db_service)
                            per_email_service.handle_event(payload, attributes)
                    else:
                        logging.info("No new messages to process for %s (start_hid=%s)", email_address, start_hid)
                except Exception:
                    logging.exception("Failed to process Gmail push notification")

            listener = PubSubListener(message_handler=handler)
            listener.start()
            app.state.pubsub_listener = listener
            logging.info("Pub/Sub listener started")
        except Exception:
            logging.exception("Failed to start Pub/Sub listener")
    else:
        logging.info("Pub/Sub listener disabled via RUN_PUBSUB_LISTENER")


@app.on_event("shutdown")
async def shutdown():
    # Stop Pub/Sub listener
    listener = getattr(app.state, "pubsub_listener", None)
    if listener:
        try:
            listener.stop()
            logging.info("Pub/Sub listener stopped")
        except Exception:
            logging.exception("Error stopping Pub/Sub listener")
    
    # Stop Gmail watch service
    watch_service = getattr(app.state, "gmail_watch_service", None)
    if watch_service:
        try:
            watch_service.stop_automatic_renewal()
            logging.info("Gmail Watch Service stopped")
        except Exception:
            logging.exception("Error stopping Gmail Watch Service")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=5000, log_level="debug")