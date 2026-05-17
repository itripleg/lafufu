"""FastAPI app factory. `nats_publish` is injected so tests can verify without a real broker."""

from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .routers import settings as settings_router
from .routers import snapshot as snapshot_router

STATIC_PATH = Path(__file__).parent.parent / "static"


def create_app(*, engine, nats_publish: Callable[[str, dict], None]) -> FastAPI:
    app = FastAPI(title="lafufu control", version="0.1.0")
    app.state.engine = engine
    app.state.nats_publish = nats_publish

    app.include_router(settings_router.router, prefix="/api/settings", tags=["settings"])
    app.include_router(snapshot_router.router, prefix="/api/state", tags=["state"])

    if STATIC_PATH.exists():
        app.mount("/", StaticFiles(directory=str(STATIC_PATH), html=True), name="spa")

    return app
