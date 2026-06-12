"""Session and queue REST routes (US1 / US3).

Start manual sessions (queued when one is active), inspect session detail with turns,
grants, and compression events, stop a turn or session, and manage the FIFO queue.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(tags=["sessions"])


def _ctrl(request: Request):
    """Return the controller from app state."""
    return request.app.state.controller


class SessionStart(BaseModel):
    """Payload to start a manual session."""

    model: str | None = None
    persona_id: str | None = None


@router.post("/api/sessions")
async def start_session(payload: SessionStart, request: Request) -> dict:
    """Start a manual session; if one is active it is queued (FR-008)."""
    session_id, position = _ctrl(request).start_manual_session(
        model=payload.model, persona_id=payload.persona_id
    )
    return {"session_id": session_id, "queue_position": position}


@router.get("/api/sessions")
async def list_sessions(
    request: Request, status: str | None = None, trigger: str | None = None, limit: int = 100
) -> list[dict]:
    """List sessions, optionally filtered by status/trigger (US3)."""
    return _ctrl(request).store.list_sessions(status=status, trigger=trigger, limit=limit)


@router.get("/api/sessions/{session_id}")
async def get_session(session_id: str, request: Request) -> dict:
    """Return a session with its turns, grants, and compression events (US3)."""
    ctrl = _ctrl(request)
    session = ctrl.store.get_session(session_id)
    if session is None:
        raise HTTPException(404, "Session not found")
    return {
        **session,
        "turns": ctrl.store.list_turns(session_id),
        "grants": ctrl.store.active_grants(session_id=session_id),
        "compression_events": ctrl.store.list_compression_events(session_id),
    }


class StopIn(BaseModel):
    """Stop scope: a single turn or the whole session."""

    scope: str = "turn"  # turn | session


@router.post("/api/sessions/{session_id}/stop")
async def stop_session(session_id: str, payload: StopIn, request: Request) -> dict:
    """Stop the current turn or end the session (FR-005/FR-059)."""
    control = _ctrl(request).hub.control(session_id)
    if control is None:
        raise HTTPException(404, "No active control channel for this session")
    control.stop(payload.scope)
    return {"ok": True}


@router.get("/api/queue")
async def view_queue(request: Request) -> list[dict]:
    """View the FIFO queue (active + waiting items, FR-008)."""
    return _ctrl(request).queue.snapshot()


@router.delete("/api/queue/{session_id}")
async def cancel_queued(session_id: str, request: Request) -> dict:
    """Cancel a queued (not-yet-started) item (FR-008)."""
    ctrl = _ctrl(request)
    if not ctrl.queue.cancel(session_id):
        raise HTTPException(409, "Item not found or already started")
    ctrl.store.update_session(session_id, status="stopped")
    return {"ok": True}
