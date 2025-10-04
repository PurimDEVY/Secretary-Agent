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

    if run_diagnostics:
        # Run diagnostics at app startup (works regardless of how uvicorn is launched)
        test_gcp()
        test_db_connection()
        test_gemini_api()
        try:
            # Call the diagnostics module's test runner once; do not retry on failure
            run_pubsub_tests()
        except Exception:
            logging.exception("Pub/Sub diagnostics failed")
    else:
        logging.info("Startup diagnostics disabled via RUN_STARTUP_DIAGNOSTICS")

    if run_listener:
        try:
            # Compose dependencies for message handling
            db_service = DbService()

            # Prepare Gmail clients per configured users (comma-separated)
            tokens_dir = os.getenv("GMAIL_TOKENS_DIR", "secrets/tokens")
            user_emails = os.getenv("GMAIL_WATCH_USERS", "").strip()
            gmail_clients_by_email: dict[str, GmailApiClient] = {}
            if user_emails:
                for email in [e.strip() for e in user_emails.split(",") if e.strip()]:
                    try:
                        gmail_clients_by_email[email] = GmailApiClient.from_email_and_dir(email, tokens_dir)
                    except Exception:  # noqa: BLE001
                        logging.exception("Failed to initialize Gmail client for %s", email)

            # Fallback: if only one user is configured, pass that client to service for direct usage
            single_gmail_client = None
            if len(gmail_clients_by_email) == 1:
                single_gmail_client = next(iter(gmail_clients_by_email.values()))

            email_service = EmailService(gmail_client=single_gmail_client, db_service=db_service)

            # Initialize Gmail Watch Service for automatic renewal
            project_id = os.getenv("GCP_PROJECT_ID")
            topic_name = os.getenv("PUBSUB_TOPIC_NAME")
            
            if project_id and topic_name:
                watch_service = GmailWatchService(
                    tokens_dir=tokens_dir,
                    project_id=project_id, 
                    topic_name=topic_name
                )
                
                # Setup initial watches for all users
                results = watch_service.setup_all_watches()
                for email, success in results.items():
                    if success:
                        logging.info(f"✅ Gmail watch setup for {email}")
                    else:
                        logging.error(f"❌ Failed to setup Gmail watch for {email}")
                
                # Start automatic renewal (check every hour)
                watch_service.start_automatic_renewal(check_interval_hours=1)
                app.state.gmail_watch_service = watch_service
                logging.info("Gmail Watch Service started with automatic renewal")
            else:
                logging.warning("GCP_PROJECT_ID or PUBSUB_TOPIC_NAME not set - Gmail watch renewal disabled")

            def handler(event_payload, attributes):  # noqa: ANN001
                # Gmail push sends attributes with emailAddress and historyId; payload isn't JSON
                email_address = attributes.get("emailAddress") if isinstance(attributes, dict) else None
                history_id = attributes.get("historyId") if isinstance(attributes, dict) else None

                # Handle direct JSON with message_id if provided (non-Gmail sources)
                if isinstance(event_payload, dict) and (event_payload.get("message_id") or event_payload.get("gmailMessageId")):
                    email_service.handle_event(event_payload, attributes)
                    return

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
                    import json as _json
                    last_history_id = None
                    try:
                        with open(state_file, "r", encoding="utf-8") as f:
                            state = _json.load(f)
                            last_history_id = str(state.get("watchResponse", {}).get("historyId") or state.get("lastHistoryId"))
                    except Exception:
                        last_history_id = None

                    # Fallback: if no stored history, start from current push history to avoid backfill storm
                    start_hid = last_history_id or str(history_id)
                    message_ids, new_last_hid = gmail_client.list_history_since(start_hid)

                    # Update state with new lastHistoryId
                    try:
                        new_state = {"emailAddress": email_address, "lastHistoryId": new_last_hid}
                        with open(state_file, "w", encoding="utf-8") as f:
                            _json.dump(new_state, f)
                    except Exception:
                        logging.exception("Failed to update state for %s", email_address)

                    # Process messages via EmailService if we have a single gmail client injected
                    if message_ids:
                        for mid in message_ids:
                            payload = {"message_id": mid}
                            email_service.handle_event(payload, attributes)
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
    uvicorn.run("main:app", host="0.0.0.0", port=5000)