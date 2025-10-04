from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
import logging
import os

from diagnostics.testdbconnection import test_gcp, test_db_connection, test_gemini_api
from diagnostics.testpubsubemail import main as run_pubsub_tests
from infrastructure.pubsub_listener import PubSubListener
from service.email_service import EmailService
from service.db_service import DbService

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
            email_service = EmailService(gmail_client=None, db_service=db_service)

            def handler(event_payload, attributes):  # noqa: ANN001
                # Route to EmailService; expects dict payload when possible
                if isinstance(event_payload, str):
                    # ignore non-JSON for now; extend as needed
                    return
                email_service.handle_event(event_payload, attributes)

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
    listener = getattr(app.state, "pubsub_listener", None)
    if listener:
        try:
            listener.stop()
            logging.info("Pub/Sub listener stopped")
        except Exception:
            logging.exception("Error stopping Pub/Sub listener")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=5000)