"""Unit tests for the `parallel` meta-tool (US2)."""

from __future__ import annotations

import pytest

from lmstudioclaw.capabilities import parallel_tool
from lmstudioclaw.capabilities.registry import CapabilityRegistry
from lmstudioclaw.consent.path_gate import PathGate


class _FakeStore:
    def active_grants(self, session_id=None):  # noqa: ANN001
        return []


def _registry(paths) -> CapabilityRegistry:
    gate = PathGate(paths, _FakeStore())
    return CapabilityRegistry(paths, _FakeStore(), gate)


async def _yes(_path, _access):
    return True


async def test_runs_independent_calls(temp_app_paths):
    reg = _registry(temp_app_paths)
    ws = temp_app_paths.workspace
    (ws / "a.txt").write_text("alpha", encoding="utf-8")
    (ws / "b.txt").write_text("beta", encoding="utf-8")
    res = await parallel_tool.run_parallel(reg, _yes, calls=[
        {"tool": "read_file", "arguments": {"path": str(ws / "a.txt")}},
        {"tool": "read_file", "arguments": {"path": str(ws / "b.txt")}},
    ])
    assert res.ok
    assert "alpha" in res.output and "beta" in res.output


async def test_requires_two_calls(temp_app_paths):
    reg = _registry(temp_app_paths)
    res = await parallel_tool.run_parallel(reg, _yes, calls=[
        {"tool": "list_dir", "arguments": {"path": str(temp_app_paths.workspace)}},
    ])
    assert not res.ok and "at least 2" in res.error


async def test_rejects_same_target_mutations(temp_app_paths):
    reg = _registry(temp_app_paths)
    target = str(temp_app_paths.workspace / "x.txt")
    res = await parallel_tool.run_parallel(reg, _yes, calls=[
        {"tool": "write_file", "arguments": {"path": target, "content": "1"}},
        {"tool": "edit", "arguments": {"path": target, "old_string": "1", "new_string": "2"}},
    ])
    assert not res.ok and "same target" in res.error.lower()
