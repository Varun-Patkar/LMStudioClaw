"""Settings, model management, persona, and file-open REST routes (US7)."""

from __future__ import annotations

import shutil
import subprocess

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..model.catalog import list_models
from ..model.context_prefs import set_context_pref

router = APIRouter(tags=["settings"])


def _ctrl(request: Request):
    """Return the controller from app state."""
    return request.app.state.controller


# -- Settings ---------------------------------------------------------------

@router.get("/api/settings")
async def get_settings(request: Request) -> dict:
    """Return current settings (no secret values)."""
    return _ctrl(request).settings.to_dict()


@router.patch("/api/settings")
async def patch_settings(payload: dict, request: Request) -> dict:
    """Update settings fields and persist them."""
    ctrl = _ctrl(request)
    for key, value in payload.items():
        if hasattr(ctrl.settings, key):
            setattr(ctrl.settings, key, value)
    ctrl.save()
    return ctrl.settings.to_dict()


# -- Models (Advanced → Model Management) -----------------------------------

@router.get("/api/models")
async def get_models(request: Request) -> dict:
    """Discover LM Studio models in one call (no polling, FR-045)."""
    ctrl = _ctrl(request)
    models, connected = list_models(ctrl.http)
    return {
        "connected": connected,
        "models": [
            {
                "key": m.key, "display_name": m.display_name,
                "max_context_length": m.max_context_length,
                "quantization": m.quantization, "size_bytes": m.size_bytes,
                "capabilities": m.capabilities, "is_loaded": m.is_loaded,
            }
            for m in models
        ],
    }


class ContextPref(BaseModel):
    """Per-model context-length preference."""

    model_key: str
    context_length: int


@router.post("/api/models/context-pref")
async def set_model_context(payload: ContextPref, request: Request) -> dict:
    """Set a per-model context length (Advanced → Model Management, FR-046)."""
    ctrl = _ctrl(request)
    models, _ = list_models(ctrl.http)
    model = next((m for m in models if m.key == payload.model_key), None)
    if model is None:
        raise HTTPException(404, "Model not found")
    try:
        applied = set_context_pref(
            {"key": model.key, "max_context_length": model.max_context_length},
            payload.context_length,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"applied": applied}


class ModelOp(BaseModel):
    """A manual model load/unload/warmup request."""

    model_key: str | None = None
    context_length: int | None = None
    instance_id: str | None = None


@router.post("/api/models/load")
async def load_model(payload: ModelOp, request: Request) -> dict:
    """Manually load a model (Advanced)."""
    ctrl = _ctrl(request)
    if not payload.model_key:
        raise HTTPException(422, "model_key required")
    loaded = await ctrl.lifecycle.load(payload.model_key, payload.context_length)
    return {"instance_id": loaded.instance_id, "key": loaded.key}


@router.post("/api/models/unload")
async def unload_model(request: Request) -> dict:
    """Manually unload the current model (Advanced)."""
    await _ctrl(request).lifecycle.unload_all()
    return {"ok": True}


@router.post("/api/models/warmup")
async def warmup_model(payload: ModelOp, request: Request) -> dict:
    """Warm up a loaded model (Advanced)."""
    ctrl = _ctrl(request)
    current = await ctrl.lifecycle.current()
    if current is None:
        raise HTTPException(409, "No model is loaded")
    text = await ctrl.lifecycle.warmup(current.key)
    return {"ok": True, "sample": text}


# -- Personas ---------------------------------------------------------------

@router.get("/api/personas")
async def list_personas(request: Request) -> list[dict]:
    """List personas (default flagged)."""
    return _ctrl(request).store.list_personas()


class PersonaIn(BaseModel):
    """Create/update payload for a persona."""

    name: str
    instructions: str


@router.post("/api/personas")
async def create_persona(payload: PersonaIn, request: Request) -> dict:
    """Create a new persona."""
    pid = _ctrl(request).store.create_persona(payload.name, payload.instructions)
    return {"id": pid}


@router.patch("/api/personas/{persona_id}")
async def update_persona(persona_id: str, payload: dict, request: Request) -> dict:
    """Edit/rename a persona (including the default, FR-072)."""
    ctrl = _ctrl(request)
    if ctrl.store.get_persona(persona_id) is None:
        raise HTTPException(404, "Persona not found")
    allowed = {k: v for k, v in payload.items() if k in ("name", "instructions")}
    ctrl.store.update_persona(persona_id, **allowed)
    return {"ok": True}


@router.delete("/api/personas/{persona_id}")
async def delete_persona(persona_id: str, request: Request) -> dict:
    """Delete a persona (the default cannot be deleted, FR-075)."""
    if not _ctrl(request).store.delete_persona(persona_id):
        raise HTTPException(409, "Cannot delete the default persona.")
    return {"ok": True}


# -- File open --------------------------------------------------------------

class OpenIn(BaseModel):
    """A path to open in VS Code."""

    path: str


@router.post("/api/open-in-vscode")
async def open_in_vscode(payload: OpenIn) -> dict:
    """Open a referenced path in VS Code; clear error if unavailable (FR-074)."""
    code = shutil.which("code") or shutil.which("code.cmd")
    if code is None:
        raise HTTPException(501, "VS Code ('code' command) is not available on PATH.")
    try:
        subprocess.Popen([code, payload.path])  # noqa: S603 - launching the user's editor
    except OSError as exc:
        raise HTTPException(500, f"Failed to launch VS Code: {exc}") from exc
    return {"ok": True}
