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
    """A parsed MCP server entry from ``mcp.json``."""

    name: str
    command: str | None = None
    args: list[str] = field(default_factory=list)
    url: str | None = None
    env: dict[str, str] = field(default_factory=dict)


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
    if entry.get("url"):
        server["url"] = entry["url"]
    data["mcpServers"][name] = server
    try:
        mcp_json.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass


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
        except Exception as exc:  # pragma: no cover - depends on live servers
            result["error"] = exc
        finally:
            loop.close()

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join(timeout=30)
    if "error" in result:
        raise result["error"]
    return result.get("value")


async def _with_session(server: McpServer, action):
    """Open a short-lived MCP session to ``server`` and run ``action(session)``."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    if not server.command:
        raise RuntimeError("Only stdio MCP servers (command-based) are supported here.")
    params = StdioServerParameters(command=server.command, args=server.args, env=server.env or None)
    async with stdio_client(params) as (read, write):
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
