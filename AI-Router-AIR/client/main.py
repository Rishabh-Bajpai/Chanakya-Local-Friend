from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from client.config import settings
import uvicorn

app = FastAPI(title=settings.PROJECT_NAME, version=settings.VERSION)

# Templates
templates = Jinja2Templates(directory="client/templates")

# Mount Static
app.mount("/static", StaticFiles(directory="client/static"), name="static")


@app.get("/")
async def client_root(request: Request):
    return templates.TemplateResponse(
        request,
        "client/index.html",
        {"air_server_url": settings.AIR_SERVER_URL},
    )


if __name__ == "__main__":
    uvicorn.run("client.main:app", host="0.0.0.0", port=settings.PORT, reload=True)
