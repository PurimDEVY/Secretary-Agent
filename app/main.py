from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
import logging

from diagnostics.testdbconnection import test_gcp, test_db_connection, test_gemini_api
from diagnostics.testpubsubemail import main as run_pubsub_tests

async def index(request):
    return JSONResponse({"message": "Hello from secretary-agent"})

app = Starlette(routes=[Route("/", index)])


@app.on_event("startup")
async def startup():
    # Run diagnostics at app startup (works regardless of how uvicorn is launched)
    test_gcp()
    test_db_connection()
    test_gemini_api()
    try:
        # Call the diagnostics module's test runner once; do not retry on failure
        run_pubsub_tests()
    except Exception:
        logging.exception("Pub/Sub diagnostics failed")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=5000)