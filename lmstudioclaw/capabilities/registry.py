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

from ..consent.path_gate import Access, PathGate

# A consent callback: given (path, access) it returns True if access is granted.
ConsentFn = Callable[[str, Access], Awaitable[bool]]

# Per-tool-call timeout in seconds (FR-018).
TOOL_TIMEOUT = 60


def _exec_script(path: str, args: list, env: dict | None = None) -> tuple[bool, str, str | None]:
    """Run a referenced skill script via subprocess with a timeout (blocking).

    Returns ``(ok, stdout, error)``. Python scripts run under the current
    interpreter; other files run directly. Output is truncated to keep tool results
    bounded. ``env`` (when given) fully replaces the child environment — callers pass
    a copy of ``os.environ`` plus any injected secrets so values stay out of the
    agent's context.
    """
    import subprocess
    import sys

    cmd = [sys.executable, path, *map(str, args)] if path.endswith(".py") else [path, *map(str, args)]
    try:
        proc = subprocess.run(  # noqa: S603 - running a user-provided skill script
            cmd, capture_output=True, text=True, timeout=TOOL_TIMEOUT, env=env,
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
    """Outcome of a tool invocation.

    ``meta`` carries optional structured details for the UI (e.g. file-change info
    like the action and before/after content for a diff view). It is display-only —
    it never enters the model context, which only sees ``output``.
    """

    ok: bool
    output: str
    error: str | None = None
    meta: dict[str, Any] | None = None


@dataclass
class SkillDoc:
    """An enabled skill's injectable instructions."""

    name: str
    instructions: str


class CapabilityRegistry:
    """Holds and dispatches capabilities; routes file I/O through consent."""

    def __init__(self, paths, store, path_gate: PathGate, vault=None) -> None:
        """Wire the registry to app paths, the store, the consent gate, and the vault.

        ``vault`` (optional) lets MCP ``env``/``headers`` values reference a stored
        secret as ``${secret:REF_NAME}``; the value is resolved at connect time only
        and never logged or surfaced (FR-077).
        """
        self._paths = paths
        self._store = store
        self._gate = path_gate
        self._vault = vault
        # Extra (non-built-in) tools registered by skills/tools/mcp modules.
        self._extra_tools: dict[str, ToolSpec] = {}
        self._skills: list[SkillDoc] = []
        # Map of skill name -> {script_name: absolute_path} for the script runner.
        self._skill_scripts: dict[str, dict[str, str]] = {}
        # Map of skill name -> declared secret refs ({ENV_VAR: ref} or [ref, ...]).
        self._skill_secrets: dict[str, Any] = {}

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
                if getattr(info, "secrets", None):
                    self._skill_secrets[info.name] = info.secrets
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
        """Wrap a loaded custom tool module as a consent-agnostic ToolSpec.

        If the module declares ``SECRETS`` (a list of ref names or ``{ENV_VAR: ref}``),
        the resolved values are passed as a reserved ``_secrets`` keyword (a
        ``{name: value}`` dict) at call time. They are never part of the tool's
        ``PARAMETERS``/model context, so the agent cannot see them (FR-077). Passing a
        kwarg (rather than mutating ``os.environ``) keeps this safe under the
        ``parallel`` tool, which may run several tools concurrently.
        """
        secrets_decl = getattr(mod, "secrets", None)

        async def _handler(*, consent: ConsentFn = None, **kwargs) -> ToolResult:
            try:
                if secrets_decl:
                    resolved = self._secret_env(secrets_decl)
                    if resolved:
                        kwargs["_secrets"] = resolved
                result = mod.run(**kwargs)
                if asyncio.iscoroutine(result):
                    result = await result
                return ToolResult(True, str(result))
            except Exception as exc:  # capture any exception from user code (FR-018)
                return ToolResult(False, "", error=f"Tool '{mod.name}' raised: {exc}")

        return ToolSpec(mod.name, mod.description or mod.name, mod.parameters, _handler)

    def _scan_mcp(self) -> None:
        """Connect enabled MCP servers and register their tools (FR-013/FR-017).

        Also **prunes** capability rows for MCP servers no longer present in
        ``mcp.json`` so manual edits (or deletions) stay in sync with the UI.
        """
        from .mcp_client import list_tools, read_mcp_config

        servers = read_mcp_config(self._paths.mcp_json)
        present = {s.name for s in servers}
        # Drop DB rows for MCP servers that have been removed from mcp.json.
        for row in self._store.list_capabilities(kind="mcp"):
            if row["name"] not in present:
                self._store.delete_capability(row["id"])

        for server in servers:
            cap_id = self._store.upsert_capability({
                "kind": "mcp", "name": server.name, "source_path": None,
                "description": f"MCP server '{server.name}'",
            })
            cap = self._store.get_capability(cap_id)
            if not (cap and cap["enabled"]):
                continue
            try:
                tools = list_tools(self._resolve_secrets(server))
            except Exception as exc:
                self._store.update_capability(cap_id, status="connect_failed",
                                              description=f"Connect failed: {exc}")
                continue
            self._store.update_capability(cap_id, status="valid")
            # Persist the tool list (name + description) so the run-config UI can offer
            # per-tool granularity + hover descriptions without re-connecting (FR-030a).
            tool_meta = [{"name": t.get("name", ""), "description": t.get("description", "")}
                         for t in tools]
            self._store.upsert_capability({
                "kind": "mcp", "name": server.name, "source_path": None,
                "description": f"MCP server '{server.name}'", "status": "valid",
                "metadata": {"tools": tool_meta},
            })
            for tool in tools:
                self.register_tool(self._wrap_mcp_tool(server, tool))

    def _resolve_secrets(self, server):
        """Return a copy of ``server`` with ``${secret:REF}`` env/headers resolved.

        Only trusted runtime code calls this; resolved values are passed straight to
        the MCP client and never logged or returned to the agent/UI.
        """
        import dataclasses
        import re

        pattern = re.compile(r"^\$\{secret:([^}]+)\}$")

        def _sub(mapping: dict[str, str]) -> dict[str, str]:
            if not mapping or self._vault is None:
                return mapping
            out = dict(mapping)
            for key, val in mapping.items():
                match = pattern.match(str(val).strip())
                if match:
                    resolved = self._vault.inject({key: match.group(1)})
                    if key in resolved:
                        out[key] = resolved[key]
            return out

        return dataclasses.replace(server, env=_sub(server.env), headers=_sub(server.headers))

    def _secret_env(self, refs) -> dict[str, str]:
        """Resolve declared secret refs to a ``{ENV_VAR: value}`` mapping via the vault.

        ``refs`` is either a list of ref names (each becomes its own env-var name) or a
        dict ``{ENV_VAR: ref_name}``. Values are fetched only at call time and never
        enter the model context, tool parameters, or logs (FR-077). Unknown refs are
        dropped silently so a missing secret cannot leak as a placeholder. Used by both
        custom tools and skill scripts so any capability can consume a secret the agent
        cannot read.
        """
        if not refs or self._vault is None:
            return {}
        mapping = refs if isinstance(refs, dict) else {str(r): str(r) for r in refs}
        return self._vault.inject({str(k): str(v) for k, v in mapping.items()})

    def _wrap_mcp_tool(self, server, tool: dict) -> ToolSpec:
        """Wrap an MCP tool as a ToolSpec dispatched over a short-lived session."""
        from .mcp_client import call_tool

        # Namespace the tool to avoid collisions between servers.
        tool_name = f"{server.name}__{tool['name']}"

        async def _handler(*, consent: ConsentFn = None, **kwargs) -> ToolResult:
            # Attach display-only meta (server/tool + input/output) so the UI can show
            # an expandable input→output panel for MCP calls. ``meta`` never re-enters
            # the model context — only ``output`` does.
            base_meta = {"action": "mcp", "server": server.name, "tool": tool["name"],
                         "input": kwargs}
            try:
                live = self._resolve_secrets(server)
                output = await asyncio.to_thread(call_tool, live, tool["name"], kwargs)
                return ToolResult(True, output, meta={**base_meta, "output": output})
            except Exception as exc:  # pragma: no cover - depends on live server
                err = f"MCP tool '{tool_name}' failed: {exc}"
                return ToolResult(False, "", error=err, meta={**base_meta, "output": err})

        return ToolSpec(tool_name, tool.get("description", ""), tool.get("parameters", {}), _handler)

    def add_mcp_server(self, entry: dict) -> str:
        """Add an MCP server to ``mcp.json`` and register a capability row (FR-079)."""
        from .mcp_client import add_server_to_config

        add_server_to_config(self._paths.mcp_json, entry)
        return self._store.upsert_capability({
            "kind": "mcp", "name": entry["name"], "source_path": None,
            "description": f"MCP server '{entry['name']}'",
        })

    def remove_mcp_server(self, name: str) -> bool:
        """Remove an MCP server from ``mcp.json`` and its capability row. Returns success."""
        from .mcp_client import remove_server_from_config

        removed = remove_server_from_config(self._paths.mcp_json, name)
        cap = self._store.get_capability_by_kind_name("mcp", name)
        if cap:
            self._store.delete_capability(cap["id"])
        return removed or bool(cap)

    def sync_mcp_rows(self) -> None:
        """Reconcile MCP capability rows to ``mcp.json`` (the source of truth).

        Fast and network-free: it does **not** connect to any server. Rows whose
        server is missing from ``mcp.json`` are deleted; servers present in the file
        but missing a row are inserted. Safe to call on every capabilities read so the
        UI always reflects manual edits to ``mcp.json`` immediately.
        """
        from .mcp_client import read_mcp_config

        present = {s.name for s in read_mcp_config(self._paths.mcp_json)}
        existing = {row["name"]: row for row in self._store.list_capabilities(kind="mcp")}
        # Prune rows for servers no longer in the file.
        for name, row in existing.items():
            if name not in present:
                self._store.delete_capability(row["id"])
        # Add rows for servers newly present in the file.
        for name in present:
            if name not in existing:
                self._store.upsert_capability({
                    "kind": "mcp", "name": name, "source_path": None,
                    "description": f"MCP server '{name}'",
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
        self._skill_secrets.clear()

    def _script_runner_tool(self) -> ToolSpec:
        """Build a tool that runs a referenced script from an enabled skill (FR-012)."""
        async def _run(*, skill: str, script: str, args: list | None = None,
                       consent: ConsentFn = None) -> ToolResult:
            scripts = self._skill_scripts.get(skill, {})
            path = scripts.get(script)
            if path is None:
                return ToolResult(False, "", error=f"Unknown script '{script}' for skill '{skill}'")
            # Inject any secrets the skill declared as env vars for the child process,
            # so the script can authenticate without the value ever reaching the agent.
            env = None
            secret_env = self._secret_env(self._skill_secrets.get(skill))
            if secret_env:
                import os
                env = {**os.environ, **secret_env}
            try:
                completed = await asyncio.to_thread(_exec_script, path, args or [], env)
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

    def mcp_server_names(self) -> list[str]:
        """Return the names of MCP servers whose tools are currently registered.

        MCP tools are namespaced ``"{server}__{tool}"`` (see :meth:`_wrap_mcp_tool`),
        so the server is recoverable from the tool-name prefix. Used to scope a
        per-run MCP selection (FR-030).
        """
        names: set[str] = set()
        for name in self._extra_tools:
            if "__" in name:
                names.add(name.split("__", 1)[0])
        return sorted(names)

    def effective_tools(self, run_config=None) -> tuple[list[ToolSpec], list[str]]:
        """Resolve the per-run effective toolset (most-granular-wins, FR-030a).

        Resolution order, applied over the globally-enabled tools without ever
        mutating global state (FR-028):

        1. **MCP selection** (``run_config.mcp_selection``) decides which MCP servers
           are active; MCP tools from de-selected servers are dropped (FR-030). A
           ``None`` selection keeps all globally-enabled servers; ``[]`` drops all.
        2. **Per-tool overrides** apply on top: a tool whose override is ``False`` is
           removed (even if globally enabled); an override of ``True`` keeps a tool
           that is present. Overrides referencing unknown tools, and selections
           referencing unknown servers, are collected as warnings rather than failing
           the run (FR-033).

        Returns ``(tools, warnings)``. With no ``run_config`` this is exactly the
        globally-enabled toolset and no warnings (FR-032).
        """
        tools = self.enabled_tools()
        if run_config is None:
            return tools, []

        warnings: list[str] = []
        known_servers = set(self.mcp_server_names())

        # 1. MCP selection — scope active servers for this run only.
        selection = getattr(run_config, "mcp_selection", None)
        if selection is not None:
            selected = set(selection)
            for missing in selected - known_servers:
                warnings.append(f"Unknown MCP server '{missing}' in run config; ignored.")
            kept: list[ToolSpec] = []
            for tool in tools:
                if "__" in tool.name:
                    server = tool.name.split("__", 1)[0]
                    if server in known_servers and server not in selected:
                        continue  # de-selected MCP server → drop its tools for this run
                kept.append(tool)
            tools = kept

        # 2. Per-tool overrides — most-granular-wins on top of the selection.
        overrides = getattr(run_config, "tool_overrides", None) or {}
        present = {t.name for t in tools}
        for name, enabled in overrides.items():
            if name not in present and enabled:
                warnings.append(f"Unknown tool '{name}' enabled in run config; ignored.")
        tools = [t for t in tools if overrides.get(t.name, True)]
        return tools, warnings

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
        """Construct the default agent toolset (FR-008).

        Reuses the existing descriptive names (``read_file``/``list_dir``/
        ``write_file``) and adds ``edit``/``grep``/``find``/``powershell`` and the
        ``parallel`` meta-tool. File-accessing tools route through the consent gate;
        the handlers live in ``file_tools``/``shell_tool``/``parallel_tool`` to keep
        this module within the modularity limit. Descriptions instruct the agent to
        read the relevant section before editing (FR-016).
        """
        from . import file_tools, parallel_tool, shell_tool

        gate = self._gate

        def fs(fn):
            """Wrap a file_tools coroutine into a ``(consent, **args)`` handler."""
            async def _h(*, consent: ConsentFn = None, **kwargs) -> ToolResult:
                return await fn(gate, consent, **kwargs)
            return _h

        async def _parallel(*, consent: ConsentFn = None, **kwargs) -> ToolResult:
            return await parallel_tool.run_parallel(self, consent, **kwargs)

        path_only = {"type": "object",
                     "properties": {"path": {"type": "string", "description": "Target file or folder path"}},
                     "required": ["path"]}
        read_param = {"type": "object", "properties": {
            "path": {"type": "string", "description": "File to read"},
            "start_line": {"type": "integer", "description": "Optional 1-based start line (inclusive)"},
            "end_line": {"type": "integer", "description": "Optional 1-based end line (inclusive)"},
        }, "required": ["path"]}
        write_param = {"type": "object", "properties": {
            "path": {"type": "string", "description": "Target file path"},
            "content": {"type": "string", "description": "Full text content to write"},
        }, "required": ["path", "content"]}
        edit_param = {"type": "object", "properties": {
            "path": {"type": "string", "description": "File to edit (read it first to place the edit)"},
            "old_string": {"type": "string", "description": "Exact text to replace (must be unique). Exact-string mode."},
            "new_string": {"type": "string", "description": "Replacement text for old_string."},
            "start_line": {"type": "integer", "description": "1-based start line for line-range mode."},
            "end_line": {"type": "integer", "description": "1-based end line (inclusive) for line-range mode."},
            "new_content": {"type": "string", "description": "Replacement content for the line range."},
        }, "required": ["path"]}
        grep_param = {"type": "object", "properties": {
            "pattern": {"type": "string", "description": "Regex to search for"},
            "path": {"type": "string", "description": "Base directory (default: workspace)"},
            "glob": {"type": "string", "description": "Optional glob to limit files, e.g. **/*.py"},
        }, "required": ["pattern"]}
        find_param = {"type": "object", "properties": {
            "glob": {"type": "string", "description": "Glob to match, e.g. **/*.md"},
            "path": {"type": "string", "description": "Base directory (default: workspace)"},
        }, "required": ["glob"]}
        shell_param = {"type": "object", "properties": {
            "command": {"type": "string", "description": "PowerShell command to run"},
            "cwd": {"type": "string", "description": "Working directory (default: workspace)"},
        }, "required": ["command"]}
        parallel_param = {"type": "object", "properties": {
            "calls": {"type": "array", "description": "Two or more INDEPENDENT sub-tool-calls to run concurrently",
                      "items": {"type": "object", "properties": {
                          "tool": {"type": "string"},
                          "arguments": {"type": "object"}},
                          "required": ["tool", "arguments"]}},
        }, "required": ["calls"]}

        return [
            ToolSpec("read_file", "Read a UTF-8 text file, optionally a specific line range.",
                     read_param, fs(file_tools.read_file)),
            ToolSpec("list_dir", "List the entries of a directory.", path_only, fs(file_tools.list_dir)),
            ToolSpec("write_file", "Create or overwrite a file (creates parent folders).",
                     write_param, fs(file_tools.write_file)),
            ToolSpec("edit", "Precise in-place edit: exact-string find/replace (unique) OR line-range "
                             "replace. Read the file first so the target is correct.",
                     edit_param, fs(file_tools.edit)),
            ToolSpec("grep", "Search file contents for a regex pattern; returns file:line matches.",
                     grep_param, fs(file_tools.grep)),
            ToolSpec("find", "Find files by glob; returns matching paths.", find_param, fs(file_tools.find)),
            ToolSpec("powershell", "Run a PowerShell command (starts in the workspace; consent-gated; "
                                   "bounded by a timeout).", shell_param, fs(shell_tool.powershell)),
            ToolSpec("parallel", "Run two or more INDEPENDENT tool calls concurrently. Do not use it for "
                                 "concurrent operations on the same file.", parallel_param, _parallel),
        ]

