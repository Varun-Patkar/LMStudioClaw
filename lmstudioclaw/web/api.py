"""FastAPI application factory.

Builds the localhost-only control-panel app: mounts the SPA static assets, registers
the session WebSocket route, and includes the REST routers (added per user story).
All request/response bodies are validated with Pydantic at the boundary; no endpoint
ever returns a secret value (FR-026/FR-077).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..app import Controller, lifespan
from .ws import session_ws_endpoint, status_ws_endpoint

_STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app(controller: Controller | None = None) -> FastAPI:
    """Create the FastAPI app, attaching a Controller to ``app.state``."""
    app = FastAPI(title="LMStudioClaw", lifespan=lifespan)
    app.state.controller = controller or Controller()

    # REST routers (registered as each user story lands).
    from .routes_sessions import router as sessions_router
    from .routes_automations import router as automations_router
    from .routes_capabilities import router as capabilities_router
    from .routes_settings import router as settings_router

    app.include_router(sessions_router)
    app.include_router(automations_router)
    app.include_router(capabilities_router)
    app.include_router(settings_router)

    @app.websocket("/ws/sessions/{session_id}")
    async def _ws(websocket: WebSocket, session_id: str) -> None:
        """Per-session live channel (streaming + steer/queue/stop/consent)."""
        await session_ws_endpoint(websocket, session_id, app.state.controller.hub)

    @app.websocket("/ws/status")
    async def _ws_status(websocket: WebSocket) -> None:
        """App-wide live-status channel (model/run/queue) for the SPA shell."""
        await status_ws_endpoint(websocket, app.state.controller.status)

    @app.get("/api/health")
    async def _health() -> JSONResponse:
        """Lightweight liveness probe returning bootstrap warnings, if any."""
        ctrl: Controller = app.state.controller
        return JSONResponse({"ok": True, "warnings": ctrl.bootstrap_warnings})

    # Mount the SPA last so API routes take precedence.
    if _STATIC_DIR.exists():
        app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="static")
    else:  # pragma: no cover - static assets always shipped, defensive only
        @app.get("/")
        async def _index() -> FileResponse | JSONResponse:
            return JSONResponse({"ok": True, "message": "Control panel assets missing."})

    return app
