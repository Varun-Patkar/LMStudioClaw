"""Unified capability registry — the agent's tool/skill/MCP surface.

This module is the single place the orchestrator asks "what can the agent do?" and
"run this tool". It offers:

* **built-in filesystem tools** (read/write/list) that ALWAYS route through the
  consent gate (FR-016/FR-020);
* **custom python tools** and **MCP tools** (wired in later phases) dispatched with a
  per-call timeout (FR-018);
* **skill instructions** injected into the system prompt within the token budget
  (FR-012/FR-067).

Plug-and-play (Constitution IV): adding/removing a skill/tool/MCP only touches its
files plus a capability row — no other module changes.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..consent.path_gate import Access, DecisionKind, PathGate

# A consent callback: given (path, access) it returns True if access is granted.
ConsentFn = Callable[[str, Access], Awaitable[bool]]

# Per-tool-call timeout in seconds (FR-018).
TOOL_TIMEOUT = 60


def _exec_script(path: str, args: list) -> tuple[bool, str, str | None]:
    """Run a referenced skill script via subprocess with a timeout (blocking).

    Returns ``(ok, stdout, error)``. Python scripts run under the current
    interpreter; other files run directly. Output is truncated to keep tool results
    bounded.
    """
    import subprocess
    import sys

    cmd = [sys.executable, path, *map(str, args)] if path.endswith(".py") else [path, *map(str, args)]
    try:
        proc = subprocess.run(  # noqa: S603 - running a user-provided skill script
            cmd, capture_output=True, text=True, timeout=TOOL_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return False, "", f"Script timed out after {TOOL_TIMEOUT}s"
    except OSError as exc:
        return False, "", f"Could not run script: {exc}"
    out = (proc.stdout or "")[:4000]
    if proc.returncode != 0:
        return False, out, (proc.stderr or "")[:1000] or f"exit code {proc.returncode}"
    return True, out, None


@dataclass
class ToolSpec:
    """A callable tool offered to the model (OpenAI function-calling shape)."""

    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., Awaitable["ToolResult"]]

    def to_openai(self) -> dict:
        """Render as an OpenAI ``tools`` entry."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class ToolResult:
    """Outcome of a tool invocation."""

    ok: bool
    output: str
    error: str | None = None


@dataclass
class SkillDoc:
    """An enabled skill's injectable instructions."""

    name: str
    instructions: str


class CapabilityRegistry:
    """Holds and dispatches capabilities; routes file I/O through consent."""

    def __init__(self, paths, store, path_gate: PathGate) -> None:
        """Wire the registry to app paths, the store, and the consent gate."""
        self._paths = paths
        self._store = store
        self._gate = path_gate
        # Extra (non-built-in) tools registered by skills/tools/mcp modules.
        self._extra_tools: dict[str, ToolSpec] = {}
        self._skills: list[SkillDoc] = []
        # Map of skill name -> {script_name: absolute_path} for the script runner.
        self._skill_scripts: dict[str, dict[str, str]] = {}

    # -- discovery / registration ------------------------------------------

    def discover(self) -> None:
        """Re-scan skills (and, in later phases, tools/MCP) and register enabled ones.

        Capability rows are upserted into the store so the UI reflects validity and
        enabled/trust state; only enabled, valid skills are offered to the agent
        (FR-011/FR-012/FR-017).
        """
        self.reset_extras()
        self._discover_skills()
        self._discover_tools()
        self._discover_mcp()

    def _discover_skills(self) -> None:
        """Scan the skills folder, upsert rows, and register enabled valid skills."""
        from .skills import discover_skills

        for info in discover_skills(self._paths.skills):
            cap_id = self._store.upsert_capability({
                "kind": "skill", "name": info.name,
                "source_path": info.source_path, "description": info.description,
                "status": "valid" if info.valid else "invalid",
            })
            cap = self._store.get_capability(cap_id)
            if info.valid and cap and cap["enabled"]:
                self.register_skill(SkillDoc(info.name, info.instructions))
                if info.scripts:
                    self._skill_scripts[info.name] = {
                        script: str(Path(info.source_path) / script)
                        for script in info.scripts
                    }
        if self._skill_scripts:
            self.register_tool(self._script_runner_tool())

    def _discover_tools(self) -> None:
        """Scan custom tools (implemented in US6)."""
        scan = getattr(self, "_scan_custom_tools", None)
        if callable(scan):
            scan()

    def _discover_mcp(self) -> None:
        """Connect MCP servers (implemented in US6)."""
        scan = getattr(self, "_scan_mcp", None)
        if callable(scan):
            scan()

    def _scan_custom_tools(self) -> None:
        """Load custom Python tools; offer only enabled + trust-confirmed ones (FR-015)."""
        from .tools import discover_tools

        for mod in discover_tools(self._paths.tools):
            cap_id = self._store.upsert_capability({
                "kind": "tool", "name": mod.name, "source_path": mod.source_path,
                "description": mod.description,
                "status": "valid" if mod.valid else "invalid",
            })
            cap = self._store.get_capability(cap_id)
            if mod.valid and cap and cap["enabled"] and cap["trust_confirmed"]:
                self.register_tool(self._wrap_custom_tool(mod))

    def _wrap_custom_tool(self, mod) -> ToolSpec:
        """Wrap a loaded custom tool module as a consent-agnostic ToolSpec."""
        async def _handler(*, consent: ConsentFn = None, **kwargs) -> ToolResult:
            try:
                result = mod.run(**kwargs)
                if asyncio.iscoroutine(result):
                    result = await result
                return ToolResult(True, str(result))
            except Exception as exc:  # capture any exception from user code (FR-018)
                return ToolResult(False, "", error=f"Tool '{mod.name}' raised: {exc}")

        return ToolSpec(mod.name, mod.description or mod.name, mod.parameters, _handler)

    def _scan_mcp(self) -> None:
        """Connect enabled MCP servers and register their tools (FR-013/FR-017)."""
        from .mcp_client import list_tools, read_mcp_config

        for server in read_mcp_config(self._paths.mcp_json):
            cap_id = self._store.upsert_capability({
                "kind": "mcp", "name": server.name, "source_path": None,
                "description": f"MCP server '{server.name}'",
            })
            cap = self._store.get_capability(cap_id)
            if not (cap and cap["enabled"]):
                continue
            try:
                tools = list_tools(server)
            except Exception as exc:
                self._store.update_capability(cap_id, status="connect_failed",
                                              description=f"Connect failed: {exc}")
                continue
            self._store.update_capability(cap_id, status="valid")
            for tool in tools:
                self.register_tool(self._wrap_mcp_tool(server, tool))

    def _wrap_mcp_tool(self, server, tool: dict) -> ToolSpec:
        """Wrap an MCP tool as a ToolSpec dispatched over a short-lived session."""
        from .mcp_client import call_tool

        # Namespace the tool to avoid collisions between servers.
        tool_name = f"{server.name}__{tool['name']}"

        async def _handler(*, consent: ConsentFn = None, **kwargs) -> ToolResult:
            try:
                output = await asyncio.to_thread(call_tool, server, tool["name"], kwargs)
                return ToolResult(True, output)
            except Exception as exc:  # pragma: no cover - depends on live server
                return ToolResult(False, "", error=f"MCP tool '{tool_name}' failed: {exc}")

        return ToolSpec(tool_name, tool.get("description", ""), tool.get("parameters", {}), _handler)

    def add_mcp_server(self, entry: dict) -> str:
        """Add an MCP server to ``mcp.json`` and register a capability row (FR-079)."""
        from .mcp_client import add_server_to_config

        add_server_to_config(self._paths.mcp_json, entry)
        return self._store.upsert_capability({
            "kind": "mcp", "name": entry["name"], "source_path": None,
            "description": f"MCP server '{entry['name']}'",
        })

    def register_tool(self, spec: ToolSpec) -> None:
        """Register an external tool (custom python / MCP) by name."""
        self._extra_tools[spec.name] = spec

    def register_skill(self, skill: SkillDoc) -> None:
        """Register an enabled skill's instructions for injection."""
        self._skills.append(skill)

    def reset_extras(self) -> None:
        """Clear externally registered tools/skills before a re-scan."""
        self._extra_tools.clear()
        self._skills.clear()
        self._skill_scripts.clear()

    def _script_runner_tool(self) -> ToolSpec:
        """Build a tool that runs a referenced script from an enabled skill (FR-012)."""
        async def _run(*, skill: str, script: str, args: list | None = None,
                       consent: ConsentFn = None) -> ToolResult:
            scripts = self._skill_scripts.get(skill, {})
            path = scripts.get(script)
            if path is None:
                return ToolResult(False, "", error=f"Unknown script '{script}' for skill '{skill}'")
            try:
                completed = await asyncio.to_thread(_exec_script, path, args or [])
            except Exception as exc:  # pragma: no cover - subprocess edge cases
                return ToolResult(False, "", error=f"Script failed: {exc}")
            return ToolResult(completed[0], completed[1], error=completed[2])

        return ToolSpec(
            "run_skill_script",
            "Run a script referenced by an enabled skill.",
            {"type": "object", "properties": {
                "skill": {"type": "string", "description": "The skill name"},
                "script": {"type": "string", "description": "The script file name"},
                "args": {"type": "array", "items": {"type": "string"},
                         "description": "Optional command-line arguments"},
            }, "required": ["skill", "script"]},
            _run,
        )

    # -- surfaces offered to the engine ------------------------------------

    def enabled_tools(self) -> list[ToolSpec]:
        """Return all callable tools: built-in filesystem + registered extras."""
        return [*self._builtin_tools(), *self._extra_tools.values()]

    def enabled_skills(self) -> list[SkillDoc]:
        """Return enabled skill docs for system-prompt injection."""
        return list(self._skills)

    # -- dispatch -----------------------------------------------------------

    async def invoke_tool(
        self, name: str, args: dict[str, Any], *, consent: ConsentFn
    ) -> ToolResult:
        """Dispatch a tool call by name with a per-call timeout (FR-018)."""
        tool = next((t for t in self.enabled_tools() if t.name == name), None)
        if tool is None:
            return ToolResult(False, "", error=f"Unknown tool: {name}")
        try:
            return await asyncio.wait_for(tool.handler(consent=consent, **args), TOOL_TIMEOUT)
        except asyncio.TimeoutError:
            return ToolResult(False, "", error=f"Tool '{name}' timed out after {TOOL_TIMEOUT}s")
        except TypeError as exc:
            return ToolResult(False, "", error=f"Bad arguments for '{name}': {exc}")
        except Exception as exc:  # pragma: no cover - defensive; tools may raise anything
            return ToolResult(False, "", error=f"Tool '{name}' failed: {exc}")

    # -- built-in filesystem tools (always consent-gated) ------------------

    def _builtin_tools(self) -> list[ToolSpec]:
        """Construct the built-in filesystem tool specs."""
        path_param = {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Target file or folder path"}},
            "required": ["path"],
        }
        write_param = {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Target file path"},
                "content": {"type": "string", "description": "Text content to write"},
            },
            "required": ["path", "content"],
        }
        return [
            ToolSpec("read_file", "Read a UTF-8 text file.", path_param, self._read_file),
            ToolSpec("list_dir", "List the entries of a directory.", path_param, self._list_dir),
            ToolSpec("write_file", "Write UTF-8 text to a file (creates parents).", write_param, self._write_file),
        ]

    async def _ensure(self, path: str, access: Access, consent: ConsentFn) -> Path | str:
        """Authorize a path via the gate (prompting if needed). Returns Path or error str."""
        decision = self._gate.authorize(path, access)
        if decision.kind == DecisionKind.ALLOW:
            return Path(decision.path)
        if decision.kind == DecisionKind.DENY:
            return f"Access denied: {decision.reason}"
        # NEEDS_CONSENT — ask the user via the engine-supplied callback.
        granted = await consent(decision.path, access)
        if not granted:
            return "Access denied by user."
        # Re-authorize now that a grant may exist.
        recheck = self._gate.authorize(path, access)
        if recheck.kind == DecisionKind.ALLOW:
            return Path(recheck.path)
        return "Access denied: still not permitted after consent."

    async def _read_file(self, *, path: str, consent: ConsentFn) -> ToolResult:
        """Read a text file after consent authorization."""
        resolved = await self._ensure(path, Access.READ, consent)
        if isinstance(resolved, str):
            return ToolResult(False, "", error=resolved)
        try:
            return ToolResult(True, resolved.read_text(encoding="utf-8", errors="replace"))
        except OSError as exc:
            return ToolResult(False, "", error=f"Read failed: {exc}")

    async def _list_dir(self, *, path: str, consent: ConsentFn) -> ToolResult:
        """List a directory after consent authorization."""
        resolved = await self._ensure(path, Access.READ, consent)
        if isinstance(resolved, str):
            return ToolResult(False, "", error=resolved)
        try:
            entries = sorted(
                f"{p.name}/" if p.is_dir() else p.name for p in resolved.iterdir()
            )
            return ToolResult(True, "\n".join(entries) or "(empty)")
        except OSError as exc:
            return ToolResult(False, "", error=f"List failed: {exc}")

    async def _write_file(self, *, path: str, content: str, consent: ConsentFn) -> ToolResult:
        """Write a text file after read-write consent authorization."""
        resolved = await self._ensure(path, Access.READ_WRITE, consent)
        if isinstance(resolved, str):
            return ToolResult(False, "", error=resolved)
        try:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_text(content, encoding="utf-8")
            return ToolResult(True, f"Wrote {len(content)} chars to {resolved}")
        except OSError as exc:
            return ToolResult(False, "", error=f"Write failed: {exc}")
