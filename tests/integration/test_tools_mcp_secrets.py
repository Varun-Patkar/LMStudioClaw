"""Integration test for custom tools, MCP config, and secret isolation
(SC-008, SC-021, SC-022)."""

from __future__ import annotations

import json

from lmstudioclaw.capabilities.registry import CapabilityRegistry
from lmstudioclaw.consent.path_gate import Access, DecisionKind, PathGate
from lmstudioclaw.secrets.vault import SecretsVault
from lmstudioclaw.sessions.store import Store


def _registry(paths):
    store = Store(paths.db_path)
    return CapabilityRegistry(paths, store, PathGate(paths, store)), store


def test_custom_tool_trust_gate(temp_app_paths):
    # Drop a custom tool that returns a fixed string.
    (temp_app_paths.tools / "echo.py").write_text(
        'NAME = "echo"\nDESCRIPTION = "Echo"\n'
        'PARAMETERS = {"type": "object", "properties": {"text": {"type": "string"}}}\n'
        'def run(text=""):\n    return "echo:" + text\n',
        encoding="utf-8",
    )
    registry, store = _registry(temp_app_paths)
    registry.discover()

    cap = store.list_capabilities(kind="tool")[0]
    assert cap["status"] == "valid"
    # Enabled but not trusted -> not offered.
    store.update_capability(cap["id"], enabled=True)
    registry.discover()
    assert "echo" not in [t.name for t in registry.enabled_tools()]

    # Trust confirmed -> now offered.
    store.update_capability(cap["id"], trust_confirmed=True)
    registry.discover()
    assert "echo" in [t.name for t in registry.enabled_tools()]


async def test_custom_tool_invokes(temp_app_paths):
    (temp_app_paths.tools / "adder.py").write_text(
        'NAME = "adder"\nDESCRIPTION = "Adds"\n'
        'PARAMETERS = {"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}}}\n'
        'def run(a=0, b=0):\n    return a + b\n',
        encoding="utf-8",
    )
    registry, store = _registry(temp_app_paths)
    registry.discover()
    cap = store.list_capabilities(kind="tool")[0]
    store.update_capability(cap["id"], enabled=True, trust_confirmed=True)
    registry.discover()

    async def _no_consent(path, access):
        return False

    result = await registry.invoke_tool("adder", {"a": 2, "b": 3}, consent=_no_consent)
    assert result.ok and result.output == "5"


def test_add_mcp_server_writes_config_and_row(temp_app_paths):
    registry, store = _registry(temp_app_paths)
    registry.add_mcp_server({"name": "files", "command": "npx", "args": ["-y", "server"]})
    config = json.loads(temp_app_paths.mcp_json.read_text(encoding="utf-8"))
    assert "files" in config["mcpServers"]
    assert config["mcpServers"]["files"]["command"] == "npx"
    assert any(c["name"] == "files" and c["kind"] == "mcp" for c in store.list_capabilities("mcp"))


def test_remove_mcp_server_clears_config_and_row(temp_app_paths):
    registry, store = _registry(temp_app_paths)
    registry.add_mcp_server({"name": "files", "command": "npx", "args": ["-y", "server"]})
    # Remove via the API path: clears both mcp.json and the capability row.
    assert registry.remove_mcp_server("files") is True
    config = json.loads(temp_app_paths.mcp_json.read_text(encoding="utf-8"))
    assert "files" not in config["mcpServers"]
    assert not any(c["name"] == "files" for c in store.list_capabilities("mcp"))


def test_manual_mcp_removal_pruned_on_rescan(temp_app_paths):
    """Editing mcp.json by hand to drop a server prunes its stale DB row on rescan."""
    registry, store = _registry(temp_app_paths)
    registry.add_mcp_server({"name": "files", "command": "npx", "args": ["-y", "server"]})
    assert any(c["name"] == "files" for c in store.list_capabilities("mcp"))
    # Simulate a manual edit that empties mcp.json.
    temp_app_paths.mcp_json.write_text('{\n  "mcpServers": {}\n}\n', encoding="utf-8")
    registry.discover()
    assert not any(c["name"] == "files" for c in store.list_capabilities("mcp"))


def test_sync_mcp_rows_follows_json_both_ways(temp_app_paths):
    """mcp.json is the source of truth: sync prunes removed + adds new, no network."""
    registry, store = _registry(temp_app_paths)
    # A server added directly to mcp.json appears after a sync (added to DB).
    temp_app_paths.mcp_json.write_text(
        '{\n  "mcpServers": { "added": { "command": "npx", "args": [] } }\n}\n', encoding="utf-8")
    registry.sync_mcp_rows()
    assert any(c["name"] == "added" for c in store.list_capabilities("mcp"))
    # Removing it from the file prunes the DB row on the next sync.
    temp_app_paths.mcp_json.write_text('{\n  "mcpServers": {}\n}\n', encoding="utf-8")
    registry.sync_mcp_rows()
    assert not any(c["name"] == "added" for c in store.list_capabilities("mcp"))


def test_secret_isolated_from_agent(temp_app_paths):
    vault = SecretsVault(temp_app_paths.secrets_dir)
    vault.set("api_key", "super-secret", owner="mcp")

    # Listing returns ref + owner only, never the value (FR-026).
    refs = vault.list_refs()
    assert refs == [{"ref_name": "api_key", "owner": "mcp"}]
    assert all("value" not in r for r in refs)

    # No agent-accessible read path exists on the vault.
    assert not hasattr(vault, "get_value")

    # Runtime injection resolves the value for trusted connection building only.
    injected = vault.inject({"X-Api-Key": "api_key"})
    assert injected == {"X-Api-Key": "super-secret"}

    # The consent gate hard-denies the secrets directory regardless of grants.
    store = Store(temp_app_paths.db_path)
    gate = PathGate(temp_app_paths, store)
    decision = gate.authorize(temp_app_paths.secrets_dir / "secrets.json", Access.READ)
    assert decision.kind == DecisionKind.DENY
