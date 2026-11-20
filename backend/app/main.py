from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api.routes import router
from .db import init_db
from .logging_setup import setup_logging

FRONTEND_DIR = Path(os.environ.get("FRONTEND_DIR", Path(__file__).resolve().parents[2] / "frontend"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    init_db()
    yield


PUBLIC_MODE = os.environ.get("PUBLIC_MODE", "").lower() in {"1", "true", "yes"}

# u javnom načinu nema API dokumentacije ni administracije
app = FastAPI(title="Cijene BoLaNI", version="0.2.0", lifespan=lifespan,
              docs_url=None if PUBLIC_MODE else "/docs",
              redoc_url=None, openapi_url=None if PUBLIC_MODE else "/openapi.json")
app.include_router(router)


@app.get("/healthz")
def healthz():
    return {"ok": True}


if FRONTEND_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(FRONTEND_DIR / "index.html")
