"""The `parallel` meta-tool (feature 002).

The agent calls ``parallel`` with a list of **two or more independent** sub-tool-calls
to run them concurrently (clarification Q2). Each sub-call is dispatched through the
registry's normal ``invoke_tool`` path so consent gating and per-call timeouts apply
uniformly; results are gathered with :func:`asyncio.gather` and returned indexed by
position.

It is for *independent* operations only. Concurrent operations against the **same
target** (e.g., two edits to one file) are unsafe and unsupported — an obvious
duplicate write/edit-target pair is rejected before anything runs.
"""

from __future__ import annotations

import asyncio

from .registry import ToolResult

# Tools that mutate a file target; two calls to the same path among these conflict.
_MUTATING = {"write_file", "edit"}


def _conflicting_targets(calls: list[dict]) -> str | None:
    """Return an error if two mutating sub-calls target the same path, else None."""
    seen: set[str] = set()
    for call in calls:
        if call.get("tool") in _MUTATING:
            target = (call.get("arguments") or {}).get("path")
            if target is not None:
                if target in seen:
                    return f"parallel rejects concurrent mutating calls on the same target: {target}"
                seen.add(target)
    return None


async def run_parallel(registry, consent, *, calls: list[dict]) -> ToolResult:
    """Run >=2 independent sub-tool-calls concurrently and return combined results."""
    if not isinstance(calls, list) or len(calls) < 2:
        return ToolResult(False, "", error="parallel requires a list of at least 2 sub-tool-calls.")
    conflict = _conflicting_targets(calls)
    if conflict is not None:
        return ToolResult(False, "", error=conflict)

    async def _one(call: dict) -> ToolResult:
        name = call.get("tool")
        args = call.get("arguments") or {}
        if not name or name == "parallel":
            return ToolResult(False, "", error=f"Invalid sub-tool: {name!r}")
        return await registry.invoke_tool(name, args, consent=consent)

    results = await asyncio.gather(*[_one(c) for c in calls], return_exceptions=True)
    lines: list[str] = []
    all_ok = True
    for i, (call, res) in enumerate(zip(calls, results)):
        label = call.get("tool", "?")
        if isinstance(res, Exception):
            all_ok = False
            lines.append(f"[{i}] {label}: ERROR {res}")
        elif res.ok:
            lines.append(f"[{i}] {label}: {res.output}")
        else:
            all_ok = False
            lines.append(f"[{i}] {label}: ERROR {res.error}")
    return ToolResult(all_ok, "\n".join(lines))
