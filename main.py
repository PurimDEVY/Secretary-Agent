from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

async def index(request):
    return JSONResponse({"message": "Hello from secretary-agent!"})

app = Starlette(routes=[Route("/", index)])

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=5000)