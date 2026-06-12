"""Capabilities, secrets, and consent-grant REST routes.

Covers ``/api/capabilities`` (US5/US6), ``/api/secrets`` (US6, write-only values),
and ``/api/grants`` (US2). Secret values are never returned by any route (FR-026/FR-077).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..consent.path_gate import Access

router = APIRouter(tags=["capabilities"])


def _ctrl(request: Request):
    """Return the controller from app state."""
    return request.app.state.controller


# -- Capabilities -----------------------------------------------------------

@router.get("/api/capabilities")
async def list_capabilities(request: Request) -> list[dict]:
    """List skills, tools, and MCP servers with status/enabled/trust."""
    return _ctrl(request).store.list_capabilities()


@router.post("/api/capabilities/refresh")
async def refresh_capabilities(request: Request) -> dict:
    """Re-scan skills, tools, and ``mcp.json`` (delegates to the registry)."""
    ctrl = _ctrl(request)
    discover = getattr(ctrl.registry, "discover", None)
    if callable(discover):
        await _maybe_async(discover)
    return {"ok": True, "capabilities": ctrl.store.list_capabilities()}


class CapabilityPatch(BaseModel):
    """Enable/disable or confirm trust for a capability."""

    enabled: bool | None = None
    trust_confirmed: bool | None = None


@router.patch("/api/capabilities/{cap_id}")
async def patch_capability(cap_id: str, payload: CapabilityPatch, request: Request) -> dict:
    """Enable/disable a capability; for tools, require trust before enabling (FR-015)."""
    ctrl = _ctrl(request)
    cap = ctrl.store.get_capability(cap_id)
    if cap is None:
        raise HTTPException(404, "Capability not found")
    fields = payload.model_dump(exclude_none=True)
    # A tool cannot be enabled until trust is confirmed (FR-015).
    if fields.get("enabled") and cap["kind"] == "tool":
        trust = fields.get("trust_confirmed", cap.get("trust_confirmed"))
        if not trust:
            raise HTTPException(409, "Custom tools require trust confirmation before enabling.")
    ctrl.store.update_capability(cap_id, **fields)
    # Re-sync the in-memory registry after a state change.
    discover = getattr(ctrl.registry, "discover", None)
    if callable(discover):
        await _maybe_async(discover)
    return {"ok": True}


class McpIn(BaseModel):
    """Add an MCP server entry to ``mcp.json``."""

    name: str
    command: str | None = None
    args: list[str] | None = None
    url: str | None = None
    secret_refs: list[str] | None = None


@router.post("/api/capabilities/mcp")
async def add_mcp(payload: McpIn, request: Request) -> dict:
    """Add an MCP server entry (secret values come only via the secrets endpoint)."""
    ctrl = _ctrl(request)
    add = getattr(ctrl.registry, "add_mcp_server", None)
    if not callable(add):
        raise HTTPException(501, "MCP support not available")
    add(payload.model_dump(exclude_none=True))
    return {"ok": True}


# -- Secrets (user-only; write-only values) ---------------------------------

@router.get("/api/secrets")
async def list_secrets(request: Request) -> list[dict]:
    """List secret reference names + owners only — never values (FR-026)."""
    return _ctrl(request).vault.list_refs()


class SecretIn(BaseModel):
    """Write-only secret payload."""

    value: str
    owner: str = "mcp"


@router.put("/api/secrets/{ref_name}")
async def set_secret(ref_name: str, payload: SecretIn, request: Request) -> dict:
    """Set/replace a secret value (write-only; stored in the isolated vault, FR-078)."""
    _ctrl(request).vault.set(ref_name, payload.value, owner=payload.owner)
    return {"ok": True}


@router.delete("/api/secrets/{ref_name}")
async def delete_secret(ref_name: str, request: Request) -> dict:
    """Delete a secret by reference name."""
    _ctrl(request).vault.delete(ref_name)
    return {"ok": True}


# -- Consent grants ---------------------------------------------------------

@router.get("/api/grants")
async def list_grants(request: Request) -> list[dict]:
    """List active grants (path, scope, access)."""
    return _ctrl(request).store.active_grants()


class GrantResponse(BaseModel):
    """A user's decision on a pending consent request."""

    request_id: str
    session_id: str
    path: str
    decision: str  # session | permanent | deny
    access: str = "read"


@router.post("/api/grants")
async def respond_grant(payload: GrantResponse, request: Request) -> dict:
    """Respond to a pending consent request: persist a grant and resolve the run."""
    ctrl = _ctrl(request)
    access = Access.READ_WRITE if payload.access == "read_write" else Access.READ
    granted = payload.decision in ("session", "permanent")
    if granted:
        ctrl.store.add_grant(
            path=payload.path, scope=payload.decision, access=access.value,
            session_id=payload.session_id if payload.decision == "session" else None,
        )
    control = ctrl.hub.control(payload.session_id)
    if control is not None:
        control.resolve_consent(payload.request_id, granted)
    return {"ok": True, "granted": granted}


@router.delete("/api/grants/{grant_id}")
async def revoke_grant(grant_id: str, request: Request) -> dict:
    """Revoke a grant; applies to subsequent checks (FR-023)."""
    _ctrl(request).store.revoke_grant(grant_id)
    return {"ok": True}


async def _maybe_async(fn) -> None:
    """Call ``fn`` and await it if it returns an awaitable."""
    import inspect

    result = fn()
    if inspect.isawaitable(result):
        await result
