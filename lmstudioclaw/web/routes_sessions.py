"""Session and queue REST routes (US1 / US3).

Start manual sessions (queued when one is active), inspect session detail with turns,
grants, and compression events, stop a turn or session, and manage the FIFO queue.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..capabilities.run_config import RunConfig
from ..sessions import logbook

router = APIRouter(tags=["sessions"])


def _ctrl(request: Request):
    """Return the controller from app state."""
    return request.app.state.controller


class RunConfigIn(BaseModel):
    """Per-run configuration (model + tool overrides + MCP selection) for a run.

    All fields are optional; an absent block (or absent fields) means "use global
    defaults" (FR-032). Validated at the boundary; the controller coerces it into a
    :class:`~lmstudioclaw.capabilities.run_config.RunConfig`.
    """

    model: str | None = None
    tool_overrides: dict[str, bool] = Field(default_factory=dict)
    mcp_selection: list[str] | None = None


class SessionStart(BaseModel):
    """Payload to start a manual session."""

    model: str | None = None
    persona_id: str | None = None
    run_config: RunConfigIn | None = None
    initial_message: str | None = None


@router.post("/api/sessions")
async def start_session(payload: SessionStart, request: Request) -> dict:
    """Start a manual session; if one is active it is queued (FR-008/FR-026)."""
    rc = RunConfig.from_dict(payload.run_config.model_dump()) if payload.run_config else None
    session_id, position = _ctrl(request).start_manual_session(
        model=payload.model, persona_id=payload.persona_id, run_config=rc,
        initial_message=payload.initial_message,
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


@router.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str, request: Request) -> dict:
    """Delete a non-active session and its transcript (US3)."""
    ctrl = _ctrl(request)
    session = ctrl.store.get_session(session_id)
    if session is None:
        raise HTTPException(404, "Session not found")
    if session.get("status") in ("loading", "active"):
        raise HTTPException(409, "Cannot delete an active session; end it first.")
    ctrl.store.delete_session(session_id)
    # Also remove the detailed audit log file for this session (best-effort).
    logbook.delete_log(ctrl.paths.logs_dir, session_id)
    return {"ok": True}


@router.post("/api/sessions/{session_id}/restart")
async def restart_session(session_id: str, payload: SessionStart, request: Request) -> dict:
    """Continue a finished session in a fresh run, carrying its transcript forward.

    The prior conversation is resumed (turns copied + seeded as engine history) so the
    agent continues with full context. The body may override the model / run config;
    when omitted, the original session's saved run config is reused (FR-026/FR-030b).
    """
    ctrl = _ctrl(request)
    prior = ctrl.store.get_session(session_id)
    if prior is None:
        raise HTTPException(404, "Session not found")
    rc_dict = payload.run_config.model_dump() if payload.run_config else None
    if rc_dict is None and prior.get("run_config"):
        import json as _json
        try:
            rc_dict = _json.loads(prior["run_config"])
        except (TypeError, ValueError):
            rc_dict = None
    rc = RunConfig.from_dict(rc_dict)
    new_id, position = ctrl.start_manual_session(
        model=payload.model, persona_id=payload.persona_id, run_config=rc,
        resume_session_id=session_id,
    )
    return {"session_id": new_id, "queue_position": position}


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
    """View the FIFO queue with type/label for the run/queue surface (FR-008/FR-023)."""
    ctrl = _ctrl(request)
    out: list[dict] = []
    active = ctrl._active_run_info()
    if active is not None:
        out.append({**active, "state": "active"})
    out.extend(ctrl._queue_items())
    return out


@router.delete("/api/queue/{session_id}")
async def cancel_queued(session_id: str, request: Request) -> dict:
    """Cancel a queued (not-yet-started) item (FR-008/FR-025)."""
    ctrl = _ctrl(request)
    if not ctrl.queue.cancel(session_id):
        raise HTTPException(409, "Item not found or already started")
    ctrl.store.update_session(session_id, status="stopped")
    ctrl._schedule_status()
    return {"ok": True}


# -- Session output files ("served" deliverables) ---------------------------

# Extensions previewed inline in the Output panel (everything else is download-only).
_PREVIEW_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"}


@router.get("/api/sessions/{session_id}/outputs")
async def list_session_outputs(session_id: str, request: Request) -> dict:
    """List files the session produced in its output folder (name/size/kind/path).

    Returned recursively so nested deliverables still show up. Never returns file
    contents — the UI fetches each file from the download endpoint on demand.
    """
    ctrl = _ctrl(request)
    out_dir = ctrl.session_output_dir(session_id)
    files: list[dict] = []
    if out_dir.exists():
        for path in sorted(out_dir.rglob("*")):
            if not path.is_file():
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            rel = path.relative_to(out_dir).as_posix()
            files.append({
                "name": rel,
                "size": stat.st_size,
                "modified": stat.st_mtime,
                "is_image": path.suffix.lower() in _PREVIEW_IMAGE_EXTS,
                "path": str(path),
            })
    return {"dir": str(out_dir), "files": files}


@router.get("/api/sessions/{session_id}/outputs/{filename:path}")
async def download_session_output(session_id: str, filename: str, request: Request):
    """Serve one output file. Path-traversal-safe: the target must stay in the folder."""
    from starlette.responses import FileResponse

    ctrl = _ctrl(request)
    out_dir = ctrl.session_output_dir(session_id).resolve()
    target = (out_dir / filename).resolve()
    # Reject anything that escapes the session's output folder.
    if out_dir not in target.parents and target != out_dir:
        raise HTTPException(403, "Path outside the session output folder")
    if not target.is_file():
        raise HTTPException(404, "File not found")
    return FileResponse(str(target), filename=target.name)

