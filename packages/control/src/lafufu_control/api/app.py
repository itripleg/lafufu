"""FastAPI app factory. `nats_publish` is injected so tests can verify without a real broker."""

from collections.abc import Callable
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .auth import require_auth
from .auth import router as auth_router
from .routers import agent as agent_router
from .routers import animator as animator_router
from .routers import printer as printer_router
from .routers import settings as settings_router
from .routers import snapshot as snapshot_router
from .routers import system as system_router

STATIC_PATH = Path(__file__).parent.parent / "static"


def create_app(
    *,
    engine,
    nats_publish: Callable[[str, dict], None],
    api_token: str = "",
) -> FastAPI:
    """Build the control app.

    ``api_token`` enables optional shared-token auth — when empty (the default)
    the auth layer is inert. See ``auth.py`` for the model.
    """
    app = FastAPI(title="lafufu control", version="0.1.0")
    app.state.engine = engine
    app.state.nats_publish = nats_publish
    app.state.api_token = api_token

    # Every data/command router is guarded. The static SPA, /api/auth/login and
    # the SPA fallback stay public so an unauthorized browser can still load the
    # page and reach the lock screen.
    guarded = [Depends(require_auth)]
    app.include_router(auth_router, prefix="/api/auth", tags=["auth"])
    app.include_router(
        settings_router.router, prefix="/api/settings", tags=["settings"], dependencies=guarded
    )
    app.include_router(
        snapshot_router.router, prefix="/api/state", tags=["state"], dependencies=guarded
    )
    app.include_router(
        system_router.router, prefix="/api/system", tags=["system"], dependencies=guarded
    )
    app.include_router(
        animator_router.router, prefix="/api/animator", tags=["animator"], dependencies=guarded
    )
    app.include_router(
        agent_router.router, prefix="/api/agent", tags=["agent"], dependencies=guarded
    )
    app.include_router(
        printer_router.router, prefix="/api/printer", tags=["printer"], dependencies=guarded
    )

    if STATIC_PATH.exists():
        # Serve hashed Vite assets directly
        app.mount("/assets", StaticFiles(directory=str(STATIC_PATH / "assets")), name="assets")

        index_file = STATIC_PATH / "index.html"

        # SPA fallback: any non-API GET that isn't a real asset returns index.html
        # so SolidJS client-side router takes over for /face, /admin, /admin/xyz, etc.
        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_fallback(full_path: str):
            # Reject API/WS paths (shouldn't reach here, but defensive)
            if full_path.startswith(("api/", "ws")):
                raise HTTPException(404)
            # If a real file exists at the path, serve it (e.g. favicon.ico)
            candidate = STATIC_PATH / full_path
            if candidate.is_file():
                return FileResponse(str(candidate))
            return FileResponse(str(index_file))

    return app
