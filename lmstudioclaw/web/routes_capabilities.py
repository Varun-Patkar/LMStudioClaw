"""Capabilities, secrets, and consent-grant REST routes.

Covers ``/api/capabilities`` (US5/US6), ``/api/secrets`` (US6, write-only values),
and ``/api/grants`` (US2). Secret values are never returned by any route (FR-026/FR-077).
"""

from __future__ import annotations

import json
import re

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
            "enabled": bool(c.get("enabled")),
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


# -- MCP import (paste JSON, auto-extract secrets) --------------------------

class McpImportIn(BaseModel):
    """Paste a block of standard MCP JSON to add one or more servers at once."""

    config: str


# Header/env keys whose values are treated as secrets and moved to the vault.
_SECRET_KEY_RE = re.compile(r"(token|secret|password|authorization|api[_-]?key|access[_-]?key|bearer)", re.I)


def _slug_ref(text: str) -> str:
    """Turn 'server name / KEY' into an UPPER_SNAKE secret reference."""
    ref = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").upper()
    return ref or "SECRET"


def _normalize_mcp(data: dict) -> dict:
    """Coerce assorted pasted shapes into ``{server_name: config}``.

    Accepts the standard ``{"mcpServers": {name: cfg}}`` wrapper, a bare
    ``{name: cfg}`` map, or a single ``{"name": .., "command"/"url": ..}`` object.
    """
    if not isinstance(data, dict):
        return {}
    if isinstance(data.get("mcpServers"), dict):
        return {k: v for k, v in data["mcpServers"].items() if isinstance(v, dict)}
    if data.get("name") and (data.get("command") or data.get("url")):
        name = str(data["name"])
        return {name: {k: v for k, v in data.items() if k != "name"}}
    # Otherwise assume the object itself maps names → configs.
    out = {k: v for k, v in data.items() if isinstance(v, dict) and (v.get("command") or v.get("url"))}
    return out


@router.post("/api/capabilities/mcp/import")
async def import_mcp(payload: McpImportIn, request: Request) -> dict:
    """Add MCP server(s) from pasted JSON; auto-move secret-looking values to the vault.

    Any ``env``/``headers`` value whose key looks like a credential (token, api key,
    authorization, …) is stored as a write-only secret and replaced inline with a
    ``${secret:REF}`` reference, so credentials never sit in ``mcp.json`` in the clear.
    """
    ctrl = _ctrl(request)
    try:
        data = json.loads(payload.config)
    except (json.JSONDecodeError, TypeError) as exc:
        raise HTTPException(422, f"Invalid JSON: {exc}")
    servers = _normalize_mcp(data)
    if not servers:
        raise HTTPException(422, "No MCP servers found in that JSON.")
    add = getattr(ctrl.registry, "add_mcp_server", None)
    if not callable(add):
        raise HTTPException(501, "MCP support not available")

    added: list[str] = []
    made_secrets: list[str] = []
    for name, cfg in servers.items():
        name = str(name).strip()
        if not name or ctrl.store.get_capability_by_kind_name("mcp", name):
            continue
        entry: dict = {"name": name}
        for key in ("command", "args", "env", "url", "type", "headers"):
            if key in cfg and cfg[key] is not None:
                entry[key] = cfg[key]
        if isinstance(entry.get("args"), str):
            entry["args"] = entry["args"].split()
        # Pull credential-looking values out into the vault.
        for field in ("env", "headers"):
            values = entry.get(field)
            if not isinstance(values, dict):
                continue
            for k, v in list(values.items()):
                if isinstance(v, str) and not v.startswith("${secret:") and _SECRET_KEY_RE.search(k):
                    ref = _slug_ref(f"{name}_{k}")
                    ctrl.vault.set(ref, v, owner="mcp")
                    values[k] = "${secret:" + ref + "}"
                    made_secrets.append(ref)
        if not (entry.get("command") or entry.get("url")):
            continue
        add(entry)
        added.append(name)

    if not added:
        raise HTTPException(409, "Those MCP server(s) already exist or were invalid.")
    await _maybe_async(getattr(ctrl.registry, "discover", lambda: None))
    return {"ok": True, "added": added, "secrets": made_secrets}


# -- Skills (create / template / import from URL) ---------------------------

_SKILL_TEMPLATE = """---
name: my-skill
description: When to use this skill — describe the trigger conditions clearly so the agent knows when to apply it.
---

# My Skill

Explain exactly what the agent should do when this skill applies. Include steps,
examples, and any constraints. If you add helper scripts to this skill's folder, refer
to them here by file name.
"""


def _name_from_content(content: str) -> str:
    """Best-effort skill name: YAML front-matter ``name`` or the first ``#`` heading."""
    m = re.search(r"^---\s*\n(.*?)\n---", content, re.S)
    if m:
        nm = re.search(r"^name:\s*(.+)$", m.group(1), re.M)
        if nm:
            return nm.group(1).strip().strip("'\"")
    heading = re.search(r"^#\s+(.+)$", content, re.M)
    return heading.group(1).strip() if heading else ""


def _slug(name: str) -> str:
    """Folder-safe slug for a skill name."""
    s = re.sub(r"[^a-zA-Z0-9._-]+", "-", (name or "").strip().lower()).strip("-")
    return s or "skill"


def _write_skill(ctrl, folder_name: str, content: str) -> str:
    """Write ``skills/<folder_name>/SKILL.md`` and return the folder name (raises on clash)."""
    dest = ctrl.paths.skills / folder_name
    if dest.exists():
        raise HTTPException(409, f"A skill folder named '{folder_name}' already exists.")
    try:
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "SKILL.md").write_text(content, encoding="utf-8")
    except OSError as exc:
        raise HTTPException(500, f"Could not write the skill: {exc}")
    return folder_name


@router.get("/api/capabilities/skill/template")
async def skill_template() -> dict:
    """Return a starter SKILL.md the user can download and fill in."""
    return {"filename": "SKILL.md", "content": _SKILL_TEMPLATE}


@router.get("/api/capabilities/skill/{cap_id}")
async def skill_detail(cap_id: str, request: Request) -> dict:
    """Return a skill's instructions (markdown) + the scripts it contains.

    Only script file names/paths are returned (never their contents) so the UI can
    render the SKILL.md as markdown and open each script in VS Code on click (FR-074).
    """
    from pathlib import Path

    from ..capabilities.skills import load_skill

    ctrl = _ctrl(request)
    cap = ctrl.store.get_capability(cap_id)
    if cap is None or cap.get("kind") != "skill":
        raise HTTPException(404, "Skill not found")
    folder = cap.get("source_path")
    if not folder or not Path(folder).exists():
        raise HTTPException(404, "Skill folder not found on disk")
    info = load_skill(Path(folder))
    scripts = [{"name": rel, "path": str(Path(folder) / rel)} for rel in info.scripts]
    return {"name": info.name, "path": str(folder),
            "markdown": info.instructions, "scripts": scripts}



class SkillIn(BaseModel):
    """Create a skill from explicit fields or raw uploaded SKILL.md content."""

    name: str | None = None
    description: str | None = None
    content: str


@router.post("/api/capabilities/skill")
async def create_skill(payload: SkillIn, request: Request) -> dict:
    """Create a skill folder + SKILL.md from a form (name/when-to-call/contents) or upload.

    If a name/description is given and the content has no YAML front-matter, a header is
    synthesized so the skill is immediately valid and discoverable.
    """
    ctrl = _ctrl(request)
    content = payload.content or ""
    name = (payload.name or "").strip()
    if not content.strip() and not name:
        raise HTTPException(422, "Provide skill content (or at least a name).")
    if not content.lstrip().startswith("---") and (name or (payload.description or "").strip()):
        body = content.strip() or f"# {name or 'My Skill'}\n\nDescribe what to do here."
        content = (
            f"---\nname: {name or 'skill'}\n"
            f"description: {(payload.description or '').strip()}\n---\n\n{body}\n"
        )
    folder = _slug(name or _name_from_content(content) or "skill")
    _write_skill(ctrl, folder, content)
    await _maybe_async(getattr(ctrl.registry, "discover", lambda: None))
    return {"ok": True, "name": folder}


class SkillUrlIn(BaseModel):
    """Import a skill from a URL that hosts a SKILL.md (or skill text)."""

    url: str


@router.post("/api/capabilities/skill/from-url")
async def skill_from_url(payload: SkillUrlIn, request: Request) -> dict:
    """Fetch a skill file from a URL and install it (detects SKILL.md-style content)."""
    ctrl = _ctrl(request)
    url = (payload.url or "").strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(422, "Provide a valid http(s) URL.")
    try:
        import httpx
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            text = resp.text
    except Exception as exc:  # noqa: BLE001 - surface any fetch error to the user
        raise HTTPException(400, f"Could not fetch the skill: {exc}")
    if not text.strip():
        raise HTTPException(422, "The URL returned an empty document.")
    derived = _name_from_content(text) or url.rstrip("/").rsplit("/", 1)[-1].rsplit(".", 1)[0]
    folder = _slug(derived or "skill")
    _write_skill(ctrl, folder, text)
    await _maybe_async(getattr(ctrl.registry, "discover", lambda: None))
    return {"ok": True, "name": folder}


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


class SecretUpdate(BaseModel):
    """Rename a secret and/or replace its value (both user-initiated; FR-078).

    ``new_ref`` renames the reference; ``value`` (when provided) replaces the stored
    value. Omitting ``value`` keeps the existing one. Values are never returned.
    """

    new_ref: str | None = None
    value: str | None = None


@router.patch("/api/secrets/{ref_name}")
async def update_secret(ref_name: str, payload: SecretUpdate, request: Request) -> dict:
    """Rename a secret and/or update its value (write-only)."""
    vault = _ctrl(request).vault
    new_ref = (payload.new_ref or ref_name).strip()
    if not new_ref:
        raise HTTPException(422, "A reference name is required.")
    if not vault.has(ref_name):
        raise HTTPException(404, f"No secret named '{ref_name}'.")
    if new_ref == ref_name:
        # Value-only update: a value is required since the name is unchanged.
        if not payload.value:
            raise HTTPException(422, "A new value is required to update the secret.")
        vault.set(ref_name, payload.value, owner=_owner_of(vault, ref_name))
        return {"ok": True, "ref_name": ref_name}
    if not vault.rename(ref_name, new_ref, payload.value):
        raise HTTPException(409, f"A secret named '{new_ref}' already exists.")
    # Keep mcp.json references in sync so a rename doesn't strand a now-missing
    # ${secret:OLD} reference (which would re-prompt for the old name).
    _rewrite_secret_refs(_ctrl(request).paths.mcp_json, ref_name, new_ref)
    return {"ok": True, "ref_name": new_ref}


def _rewrite_secret_refs(mcp_json, old_ref: str, new_ref: str) -> None:
    """Rewrite ``${secret:OLD}`` → ``${secret:NEW}`` in ``mcp.json`` (best-effort)."""
    try:
        text = mcp_json.read_text(encoding="utf-8")
    except OSError:
        return
    updated = text.replace("${secret:" + old_ref + "}", "${secret:" + new_ref + "}")
    if updated != text:
        try:
            mcp_json.write_text(updated, encoding="utf-8")
        except OSError:
            pass


def _owner_of(vault, ref_name: str) -> str:
    """Return the stored owner for a secret (defaults to 'user')."""
    for r in vault.list_refs():
        if r["ref_name"] == ref_name:
            return r.get("owner", "user")
    return "user"


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
