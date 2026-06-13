"""Unit tests for the consent-gated PowerShell tool (US2)."""

from __future__ import annotations

import sys

import pytest

from lmstudioclaw.capabilities import shell_tool
from lmstudioclaw.consent.path_gate import PathGate

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="PowerShell tool is Windows-only")


class _FakeStore:
    def active_grants(self, session_id=None):  # noqa: ANN001
        return []


def _gate(paths) -> PathGate:
    return PathGate(paths, _FakeStore())


async def _yes(_path, _access):
    return True


async def _no(_path, _access):
    return False


async def test_runs_in_workspace(temp_app_paths):
    gate = _gate(temp_app_paths)
    res = await shell_tool.powershell(gate, _yes, command="(Get-Location).Path")
    assert res.ok
    assert str(temp_app_paths.workspace) in res.output


async def test_nonzero_exit_surfaced(temp_app_paths):
    gate = _gate(temp_app_paths)
    res = await shell_tool.powershell(gate, _yes, command="exit 3")
    assert not res.ok and "3" in res.error


async def test_timeout(temp_app_paths, monkeypatch):
    gate = _gate(temp_app_paths)
    monkeypatch.setattr(shell_tool, "SHELL_TIMEOUT", 1)
    res = await shell_tool.powershell(gate, _yes, command="Start-Sleep -Seconds 5")
    assert not res.ok and "timed out" in res.error.lower()


async def test_output_truncated(temp_app_paths, monkeypatch):
    gate = _gate(temp_app_paths)
    monkeypatch.setattr(shell_tool, "_MAX_OUTPUT", 100)
    res = await shell_tool.powershell(gate, _yes, command="1..1000 | ForEach-Object { 'x' * 50 }")
    assert len(res.output) <= 100


async def test_cwd_outside_workspace_denied(temp_app_paths, tmp_path):
    gate = _gate(temp_app_paths)
    res = await shell_tool.powershell(gate, _no, command="Get-Location", cwd=str(tmp_path))
    assert not res.ok and "denied" in res.error.lower()
