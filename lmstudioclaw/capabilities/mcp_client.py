"""MCP (Model Context Protocol) client integration.

Connects to MCP servers declared in the Documents ``mcp.json`` via the official
``mcp`` Python SDK, discovers their tools, and invokes them. Connection failures are
reported (not fatal) so a bad server entry never crashes discovery (FR-013/FR-017).

Because capability discovery runs synchronously (and sometimes from within an async
context), each MCP interaction is executed in a **fresh thread with its own event
loop** — this avoids "event loop already running" errors and keeps short-lived MCP
sessions isolated.
"""

from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class McpServer:
    """A parsed MCP server entry from ``mcp.json``.

    Two transports are supported, matching the standard MCP client config format:

    * **stdio** — launched via ``command``/``args``/``env`` (no/``"stdio"`` ``type``).
    * **HTTP** — reached over ``url`` with optional auth ``headers`` (``type`` is
      ``"http"`` for Streamable HTTP or ``"sse"`` for the legacy SSE transport).

    ``headers`` carries auth keys (e.g. ``{"Authorization": "Bearer <token>"}``).
    """

    name: str
    command: str | None = None
    args: list[str] = field(default_factory=list)
    url: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    type: str | None = None
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def transport(self) -> str:
        """Resolve the effective transport: ``"stdio"``, ``"http"`` or ``"sse"``.

        Honours an explicit ``type`` when given; otherwise infers from the fields
        present (a ``url`` implies HTTP, a ``command`` implies stdio).
        """
        explicit = (self.type or "").strip().lower()
        if explicit in ("stdio", "http", "streamable-http", "streamable_http", "sse"):
            if explicit in ("streamable-http", "streamable_http"):
                return "http"
            return explicit
        if self.url:
            return "http"
        return "stdio"


def read_mcp_config(mcp_json: Path) -> list[McpServer]:
    """Parse ``mcp.json`` into a list of :class:`McpServer` (best-effort)."""
    if not mcp_json.exists():
        return []
    try:
        data = json.loads(mcp_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    servers = data.get("mcpServers", {})
    out: list[McpServer] = []
    if isinstance(servers, dict):
        for name, entry in servers.items():
            if not isinstance(entry, dict):
                continue
            out.append(McpServer(
                name=name, command=entry.get("command"),
                args=list(entry.get("args", []) or []),
                url=entry.get("url"), env=dict(entry.get("env", {}) or {}),
                type=entry.get("type"),
                headers=dict(entry.get("headers", {}) or {}),
            ))
    return out


def add_server_to_config(mcp_json: Path, entry: dict) -> None:
    """Add/replace a server entry in ``mcp.json`` (used by UI and agent, FR-079)."""
    data: dict[str, Any] = {"mcpServers": {}}
    if mcp_json.exists():
        try:
            data = json.loads(mcp_json.read_text(encoding="utf-8")) or {"mcpServers": {}}
        except (OSError, json.JSONDecodeError):
            data = {"mcpServers": {}}
    data.setdefault("mcpServers", {})
    name = entry["name"]
    server: dict[str, Any] = {}
    if entry.get("command"):
        server["command"] = entry["command"]
        server["args"] = entry.get("args", []) or []
        if entry.get("env"):
            server["env"] = entry["env"]
    if entry.get("url"):
        # HTTP transport: record the standard ``type``/``url``/``headers`` keys.
        server["type"] = (entry.get("type") or "http").strip().lower() or "http"
        server["url"] = entry["url"]
        if entry.get("headers"):
            server["headers"] = entry["headers"]
    data["mcpServers"][name] = server
    try:
        mcp_json.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass


def remove_server_from_config(mcp_json: Path, name: str) -> bool:
    """Remove a server entry from ``mcp.json`` by name. Returns True if it existed."""
    if not mcp_json.exists():
        return False
    try:
        data = json.loads(mcp_json.read_text(encoding="utf-8")) or {}
    except (OSError, json.JSONDecodeError):
        return False
    servers = data.get("mcpServers", {})
    if not isinstance(servers, dict) or name not in servers:
        return False
    servers.pop(name, None)
    try:
        mcp_json.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        return False
    return True


def _flatten_error(exc: BaseException) -> str:
    """Produce a readable message, unwrapping anyio/TaskGroup ``ExceptionGroup``s.

    The MCP SDK runs transports inside anyio task groups, so a connect failure surfaces
    as ``unhandled errors in a TaskGroup (1 sub-exception)`` which hides the real cause
    (bad URL, auth rejected, connection refused, …). This recurses into grouped
    exceptions and returns the innermost concrete messages instead.
    """
    inner = getattr(exc, "exceptions", None)
    if inner:
        parts = [_flatten_error(sub) for sub in inner]
        # De-duplicate while preserving order so repeated causes don't spam the UI.
        seen: dict[str, None] = {}
        for part in parts:
            seen.setdefault(part, None)
        return "; ".join(seen)
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def _run_isolated(coro_factory):
    """Run an async coroutine in a fresh thread with its own event loop.

    Returns the coroutine result, or raises the captured exception. This keeps MCP
    sessions short-lived and avoids interfering with the controller's event loop.
    """
    result: dict[str, Any] = {}

    def _worker():
        loop = asyncio.new_event_loop()
        try:
            result["value"] = loop.run_until_complete(coro_factory())
        except BaseException as exc:  # pragma: no cover - depends on live servers
            result["error"] = exc
        finally:
            loop.close()

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join(timeout=30)
    if "error" in result:
        # Surface a flattened, human-readable message rather than the opaque
        # ExceptionGroup wrapper the MCP SDK raises.
        raise RuntimeError(_flatten_error(result["error"]))
    return result.get("value")


async def _with_session(server: McpServer, action):
    """Open a short-lived MCP session to ``server`` and run ``action(session)``.

    Routes to the stdio, Streamable HTTP, or SSE client transport based on the
    server's resolved :attr:`McpServer.transport`. HTTP transports forward the
    configured auth ``headers`` (e.g. an ``Authorization`` bearer key).
    """
    from mcp import ClientSession

    transport = server.transport
    if transport == "stdio":
        from mcp import StdioServerParameters
        from mcp.client.stdio import stdio_client

        if not server.command:
            raise RuntimeError("stdio MCP server is missing a 'command'.")
        params = StdioServerParameters(
            command=server.command, args=server.args, env=server.env or None)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await action(session)

    if not server.url:
        raise RuntimeError("HTTP MCP server is missing a 'url'.")
    headers = server.headers or None
    if transport == "sse":
        from mcp.client.sse import sse_client

        async with sse_client(server.url, headers=headers) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await action(session)

    # Default HTTP transport: Streamable HTTP (yields a 3-tuple).
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(server.url, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await action(session)


def list_tools(server: McpServer) -> list[dict]:
    """List a server's tools as plain dicts (name/description/parameters)."""
    async def _action(session):
        listing = await session.list_tools()
        return [
            {"name": t.name, "description": t.description or "",
             "parameters": t.inputSchema or {"type": "object", "properties": {}}}
            for t in listing.tools
        ]

    return _run_isolated(lambda: _with_session(server, _action)) or []


def call_tool(server: McpServer, tool_name: str, args: dict) -> str:
    """Call a tool on a server and return its textual result."""
    async def _action(session):
        result = await session.call_tool(tool_name, arguments=args)
        parts = []
        for item in getattr(result, "content", []) or []:
            text = getattr(item, "text", None)
            if text:
                parts.append(text)
        return "\n".join(parts) if parts else "(no textual content)"

    return _run_isolated(lambda: _with_session(server, _action)) or ""
