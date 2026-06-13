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
    """List skills, tools, and MCP servers with status/enabled/trust.

    MCP rows are reconciled to ``mcp.json`` (the source of truth) on every read, so a
    manual edit to the file is reflected immediately without a forced rescan.
    """
    ctrl = _ctrl(request)
    _sync = getattr(ctrl.registry, "sync_mcp_rows", None)
    if callable(_sync):
        _sync()
    return ctrl.store.list_capabilities()


@router.get("/api/tools")
async def list_tools(request: Request) -> dict:
    """List tool names available for per-run overrides + MCP servers/tools (US4).

    Returns the default built-in tools (always present), any registered custom tools,
    and the MCP servers — each with the tools it exposes (name + description) so the
    run-config UI can offer per-server **and** per-tool granularity with hover
    descriptions. No discovery side effects are forced (tool lists are read from the
    capability rows persisted on the last successful connect).
    """
    ctrl = _ctrl(request)
    _sync = getattr(ctrl.registry, "sync_mcp_rows", None)
    if callable(_sync):
        _sync()
    builtins = [{"name": t.name, "description": t.description}
                for t in ctrl.registry._builtin_tools()]
    extras = [c for c in ctrl.store.list_capabilities() if c.get("kind") in ("tool", "mcp")]
    # Only genuine custom Python tools belong in the per-run tool list; MCP servers are
    # toggled separately under "MCP servers for this run" (avoid listing them twice).
    custom_tools = [{"name": c["name"], "kind": c["kind"]}
                    for c in extras if c.get("kind") == "tool"]
    # MCP servers, each with the tools discovered on the last successful connect. The
    # tool's per-run id is the namespaced "{server}__{tool}" used by tool_overrides.
    mcp = []
    for c in sorted((c for c in extras if c.get("kind") == "mcp"), key=lambda x: x["name"]):
        meta = c.get("metadata") or {}
        tools = meta.get("tools") or []
        mcp.append({
            "name": c["name"],
            "description": c.get("description") or "",
            "status": c.get("status"),
            "tools": [{"id": f"{c['name']}__{t.get('name', '')}",
                       "name": t.get("name", ""),
                       "description": t.get("description", "")} for t in tools],
        })
    return {
        "builtin": builtins,
        "tools": custom_tools,
        # Backward-compatible flat list of server names plus the richer per-server detail.
        "mcp_servers": [m["name"] for m in mcp],
        "mcp": mcp,
    }


@router.get("/api/capabilities/mcp-config-path")
async def mcp_config_path(request: Request) -> dict:
    """Return the absolute ``mcp.json`` path so the UI can open it in VS Code (FR-074).

    Lets the user fix a malformed/failed MCP entry directly when a server is stuck in
    ``connect_failed`` rather than only via the add-form.
    """
    ctrl = _ctrl(request)
    return {"path": str(ctrl.paths.mcp_json)}


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
    """Add an MCP server entry to ``mcp.json``.

    Supports both transports in the standard MCP config format: stdio
    (``command``/``args``/``env``) and HTTP (``type``/``url``/``headers``). Auth
    keys for HTTP servers travel in ``headers`` (e.g. an ``Authorization`` bearer).
    """

    name: str
    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None
    url: str | None = None
    type: str | None = None
    headers: dict[str, str] | None = None
    secret_refs: list[str] | None = None


@router.post("/api/capabilities/mcp")
async def add_mcp(payload: McpIn, request: Request) -> dict:
    """Add an MCP server entry (secret values come only via the secrets endpoint)."""
    ctrl = _ctrl(request)
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(422, "Server name is required.")
    if not (payload.command or payload.url):
        raise HTTPException(422, "Provide a command (stdio) or a URL for the MCP server.")
    if ctrl.store.get_capability_by_kind_name("mcp", name):
        raise HTTPException(409, f"An MCP server named '{name}' already exists.")
    add = getattr(ctrl.registry, "add_mcp_server", None)
    if not callable(add):
        raise HTTPException(501, "MCP support not available")
    entry = payload.model_dump(exclude_none=True)
    entry["name"] = name
    add(entry)
    return {"ok": True}


@router.delete("/api/capabilities/mcp/{name}")
async def delete_mcp(name: str, request: Request) -> dict:
    """Remove an MCP server from ``mcp.json`` and drop its capability row."""
    ctrl = _ctrl(request)
    remove = getattr(ctrl.registry, "remove_mcp_server", None)
    if not callable(remove):
        raise HTTPException(501, "MCP support not available")
    if not remove(name):
        raise HTTPException(404, "MCP server not found")
    # Re-scan so the DB, mcp.json, and in-memory registry are fully reconciled.
    discover = getattr(ctrl.registry, "discover", None)
    if callable(discover):
        await _maybe_async(discover)
    return {"ok": True}


# -- Secrets (user-only; write-only values) ---------------------------------

@router.get("/api/secrets")
async def list_secrets(request: Request) -> list[dict]:
    """List secret reference names + owners only — never values (FR-026)."""
    return _ctrl(request).vault.list_refs()


@router.get("/api/secrets/missing")
async def missing_secrets(request: Request) -> dict:
    """List secret refs used in ``mcp.json`` that are not yet stored in the vault.

    Lets the UI prompt the user to fill in any ``${secret:REF}`` an MCP server needs
    (e.g. right after the agent adds a server), so the value can be set without the
    agent ever seeing it. Values are never returned — only the missing ref names.
    """
    import re

    ctrl = _ctrl(request)
    refs: set[str] = set()
    try:
        text = ctrl.paths.mcp_json.read_text(encoding="utf-8")
        refs = set(re.findall(r"\$\{secret:([^}]+)\}", text))
    except OSError:
        refs = set()
    have = {r["ref_name"] for r in ctrl.vault.list_refs()}
    return {"missing": sorted(refs - have)}


class SecretIn(BaseModel):
    """Write-only secret payload."""

    value: str
    owner: str = "mcp"


@router.put("/api/secrets/{ref_name}")
async def set_secret(ref_name: str, payload: SecretIn, request: Request) -> dict:
    """Set/replace a secret value (write-only; stored in the isolated vault, FR-078)."""
    if not ref_name.strip():
        raise HTTPException(422, "A reference name is required.")
    if not payload.value:
        raise HTTPException(422, "A secret value is required.")
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
