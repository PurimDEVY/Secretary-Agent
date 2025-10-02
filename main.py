from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from diagnostics.testdbconnection import test_gcp, test_db_connection, test_gemini_api
from diagnostics.testpubsubemail import test_pubsub_connection

async def index(request):
    return JSONResponse({"message": "Hello from secretary-agent cd test change!"})

app = Starlette(routes=[Route("/", index)])


@app.on_event("startup")
async def startup():
    # Run diagnostics at app startup (works regardless of how uvicorn is launched)
    test_gcp()
    test_db_connection()
    test_gemini_api()
    test_pubsub_connection()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=5000)