"""Read-only REST routes for the detailed per-session JSON logs.

Lets the control panel list available session logs and fetch a full log document
(the same JSON written to ``Documents/LMStudioClaw/logs/<id>.json``). The standalone
HTML viewer in that folder reads the files directly; these endpoints expose the same
data to the in-app UI when it is served.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..sessions import logbook

router = APIRouter(tags=["logs"])


def _ctrl(request: Request):
    """Return the controller from app state."""
    return request.app.state.controller


@router.get("/api/logs")
async def list_logs(request: Request) -> dict:
    """Return a newest-first manifest of available session logs."""
    ctrl = _ctrl(request)
    return {"logs": logbook.list_logs(ctrl.paths.logs_dir)}


@router.get("/api/logs/{session_id}")
async def get_log(session_id: str, request: Request) -> dict:
    """Return the full detailed JSON log for a session."""
    ctrl = _ctrl(request)
    doc = logbook.read_log(ctrl.paths.logs_dir, session_id)
    if doc is None:
        raise HTTPException(404, "Log not found")
    return doc
