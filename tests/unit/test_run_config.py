"""Unit tests for per-run configuration resolution (US4): precedence, defaults, immutability."""

from __future__ import annotations

from lmstudioclaw.capabilities.registry import CapabilityRegistry, ToolResult, ToolSpec
from lmstudioclaw.capabilities.run_config import RunConfig
from lmstudioclaw.consent.path_gate import PathGate


class _FakeStore:
    def active_grants(self, session_id=None):  # noqa: ANN001
        return []


def _registry(paths) -> CapabilityRegistry:
    gate = PathGate(paths, _FakeStore())
    return CapabilityRegistry(paths, _FakeStore(), gate)


async def _noop_handler(*, consent=None, **kwargs):  # noqa: ANN001
    return ToolResult(True, "")


def _fake_tool(name: str) -> ToolSpec:
    return ToolSpec(name, name, {"type": "object", "properties": {}}, _noop_handler)


def test_from_dict_roundtrip():
    cfg = RunConfig.from_dict({"model": "m", "tool_overrides": {"grep": False}, "mcp_selection": ["s1"]})
    assert cfg.model == "m" and cfg.tool_overrides == {"grep": False} and cfg.mcp_selection == ["s1"]
    assert RunConfig.from_dict(None) is None
    assert RunConfig.from_dict({}) is None


def test_defaults_when_no_config(temp_app_paths):
    reg = _registry(temp_app_paths)
    tools, warnings = reg.effective_tools(None)
    names = {t.name for t in tools}
    assert {"read_file", "edit", "powershell", "parallel"} <= names
    assert warnings == []


def test_disable_global_tool(temp_app_paths):
    reg = _registry(temp_app_paths)
    cfg = RunConfig(tool_overrides={"powershell": False})
    tools, _ = reg.effective_tools(cfg)
    names = {t.name for t in tools}
    assert "powershell" not in names and "read_file" in names
    # Global toolset is unchanged.
    assert "powershell" in {t.name for t in reg.enabled_tools()}


def test_mcp_selection_filters_servers(temp_app_paths):
    reg = _registry(temp_app_paths)
    reg.register_tool(_fake_tool("srvA__toolX"))
    reg.register_tool(_fake_tool("srvB__toolY"))
    cfg = RunConfig(mcp_selection=["srvA"])
    tools, _ = reg.effective_tools(cfg)
    names = {t.name for t in tools}
    assert "srvA__toolX" in names and "srvB__toolY" not in names


def test_precedence_keep_server_drop_one_tool(temp_app_paths):
    reg = _registry(temp_app_paths)
    reg.register_tool(_fake_tool("srvA__toolX"))
    reg.register_tool(_fake_tool("srvA__toolZ"))
    cfg = RunConfig(mcp_selection=["srvA"], tool_overrides={"srvA__toolZ": False})
    tools, _ = reg.effective_tools(cfg)
    names = {t.name for t in tools}
    assert "srvA__toolX" in names and "srvA__toolZ" not in names


def test_unknown_refs_warn_not_fail(temp_app_paths):
    reg = _registry(temp_app_paths)
    cfg = RunConfig(tool_overrides={"ghost_tool": True}, mcp_selection=["no_such_server"])
    tools, warnings = reg.effective_tools(cfg)
    assert tools  # run still proceeds with valid tools
    assert any("ghost_tool" in w for w in warnings)
    assert any("no_such_server" in w for w in warnings)
