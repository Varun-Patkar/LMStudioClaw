"""Trigger REST routes — standalone heartbeat conditions (feature: heartbeat).

A *trigger* is a reusable, user-authored Python ``def trigger() -> bool`` generated from
a natural-language prompt. Triggers live on their own (this router) so they can be
authored, tested, and inspected independently; a heartbeat automation simply references
one by id. Every saved-trigger evaluation is recorded for Power-Automate-style history.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/api/triggers", tags=["triggers"])


class TriggerIn(BaseModel):
    """Create payload for a standalone trigger."""

    name: str
    prompt: str | None = None
    code: str


class TriggerPatch(BaseModel):
    """Partial update for a trigger (any subset)."""

    name: str | None = None
    prompt: str | None = None
    code: str | None = None


class GenerateIn(BaseModel):
    """Request to generate trigger code from a natural-language prompt."""

    prompt: str
    model: str | None = None


class TestIn(BaseModel):
    """Request to evaluate candidate code once without persisting (stateless)."""

    code: str
    state: dict | None = None


def _ctrl(request: Request):
    """Return the controller from app state."""
    return request.app.state.controller


@router.get("")
async def list_triggers(request: Request) -> list[dict]:
    """List all saved triggers (newest first) with decoded state + last result."""
    return _ctrl(request).store.list_triggers()


@router.post("")
async def create_trigger(payload: TriggerIn, request: Request) -> dict:
    """Save a new trigger definition."""
    ctrl = _ctrl(request)
    if not payload.name.strip():
        raise HTTPException(422, "Trigger name is required.")
    if not payload.code.strip():
        raise HTTPException(422, "Trigger code is required (generate and test it first).")
    tid = ctrl.store.create_trigger(
        name=payload.name.strip(), prompt=payload.prompt, code=payload.code
    )
    return {"id": tid}


@router.get("/{trigger_id}")
async def get_trigger(trigger_id: str, request: Request) -> dict:
    """Return a single trigger (with state + last result)."""
    trigger = _ctrl(request).store.get_trigger(trigger_id)
    if trigger is None:
        raise HTTPException(404, "Trigger not found")
    return trigger


@router.patch("/{trigger_id}")
async def update_trigger(trigger_id: str, payload: TriggerPatch, request: Request) -> dict:
    """Edit a trigger's name/prompt/code."""
    ctrl = _ctrl(request)
    if ctrl.store.get_trigger(trigger_id) is None:
        raise HTTPException(404, "Trigger not found")
    fields = {k: v for k, v in payload.model_dump().items() if v is not None}
    if fields:
        ctrl.store.update_trigger(trigger_id, **fields)
    return {"ok": True}


@router.delete("/{trigger_id}")
async def delete_trigger(trigger_id: str, request: Request) -> dict:
    """Delete a trigger and its run history."""
    _ctrl(request).store.delete_trigger(trigger_id)
    return {"ok": True}


@router.post("/generate")
async def generate(payload: GenerateIn, request: Request) -> dict:
    """Generate a ``trigger()`` function from a prompt (loads/unloads a model)."""
    ctrl = _ctrl(request)
    if not payload.prompt.strip():
        raise HTTPException(422, "A prompt is required.")
    try:
        code = await ctrl.generate_trigger_code(payload.prompt, payload.model)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))
    except Exception as exc:
        raise HTTPException(502, f"Code generation failed: {exc}")
    return {"code": code}


@router.post("/test")
async def test(payload: TestIn, request: Request) -> dict:
    """Run candidate code once (stateless) and return result, stdout, errors, state."""
    from ..automations import trigger as trigger_mod
    from ..app import TRIGGER_TIMEOUT

    if not payload.code.strip():
        raise HTTPException(422, "Trigger code is required.")
    result = await asyncio.to_thread(
        trigger_mod.evaluate, payload.code, payload.state or {}, timeout=TRIGGER_TIMEOUT
    )
    return {
        "value": result.value, "error": result.error, "stdout": result.stdout,
        "state": result.state, "timed_out": result.timed_out,
    }


@router.post("/{trigger_id}/run")
async def run_saved(trigger_id: str, request: Request) -> dict:
    """Evaluate a saved trigger now (persists state + records run history)."""
    ctrl = _ctrl(request)
    trigger = ctrl.store.get_trigger(trigger_id)
    if trigger is None:
        raise HTTPException(404, "Trigger not found")
    fired = await asyncio.to_thread(ctrl.evaluate_trigger, trigger)
    updated = ctrl.store.get_trigger(trigger_id) or {}
    return {
        "value": fired, "error": updated.get("last_error"), "state": updated.get("state"),
    }


@router.get("/{trigger_id}/history")
async def history(trigger_id: str, request: Request) -> list[dict]:
    """Return recent evaluation runs for a trigger (newest first)."""
    ctrl = _ctrl(request)
    if ctrl.store.get_trigger(trigger_id) is None:
        raise HTTPException(404, "Trigger not found")
    return ctrl.store.list_trigger_runs(trigger_id)
