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


def test_secret_rename_and_value_update(temp_app_paths):
    """Renaming preserves the value (and owner); a new value replaces it; conflicts fail."""
    vault = SecretsVault(temp_app_paths.secrets_dir)
    vault.set("old", "v1", owner="user")

    # Rename only — value + owner preserved under the new ref.
    assert vault.rename("old", "new") is True
    assert not vault.has("old") and vault.has("new")
    assert vault.inject({"K": "new"}) == {"K": "v1"}
    assert vault.list_refs() == [{"ref_name": "new", "owner": "user"}]

    # Rename + replace value.
    assert vault.rename("new", "newer", "v2") is True
    assert vault.inject({"K": "newer"}) == {"K": "v2"}

    # Renaming a missing ref returns False; colliding with an existing ref returns False.
    assert vault.rename("ghost", "x") is False
    vault.set("other", "z", owner="user")
    assert vault.rename("newer", "other") is False


def _registry_with_vault(paths):
    """Registry wired to a real vault so secret references can resolve."""
    store = Store(paths.db_path)
    vault = SecretsVault(paths.secrets_dir)
    return CapabilityRegistry(paths, store, PathGate(paths, store), vault), store, vault


async def test_custom_tool_receives_injected_secret(temp_app_paths):
    """A tool declaring SECRETS gets resolved values via `_secrets`, not via params."""
    (temp_app_paths.tools / "needs_key.py").write_text(
        'NAME = "needs_key"\nDESCRIPTION = "Uses a secret"\n'
        'PARAMETERS = {"type": "object", "properties": {}}\n'
        'SECRETS = {"API_KEY": "my_ref"}\n'
        'def run(_secrets=None, **kw):\n    return "key=" + (_secrets or {}).get("API_KEY", "MISSING")\n',
        encoding="utf-8",
    )
    registry, store, vault = _registry_with_vault(temp_app_paths)
    vault.set("my_ref", "s3cr3t", owner="user")
    registry.discover()
    cap = store.list_capabilities(kind="tool")[0]
    store.update_capability(cap["id"], enabled=True, trust_confirmed=True)
    registry.discover()

    async def _no_consent(path, access):
        return False

    result = await registry.invoke_tool("needs_key", {}, consent=_no_consent)
    assert result.ok and result.output == "key=s3cr3t"
    # The secret name/value is never part of the tool's advertised parameters.
    spec = next(t for t in registry.enabled_tools() if t.name == "needs_key")
    assert "s3cr3t" not in json.dumps(spec.parameters)


def test_mcp_http_header_secret_resolved(temp_app_paths):
    """An HTTP MCP server header `${secret:REF}` resolves via the vault at connect time."""
    from lmstudioclaw.capabilities.mcp_client import McpServer

    registry, _store, vault = _registry_with_vault(temp_app_paths)
    vault.set("webiq_key", "abc123", owner="user")
    server = McpServer(
        name="WebIQ-MCP", url="https://api.example/mcp", type="http",
        headers={"x-apikey": "${secret:webiq_key}"},
    )
    live = registry._resolve_secrets(server)
    assert live.headers["x-apikey"] == "abc123"
    # The original server entry is untouched (no resolved value persisted).
    assert server.headers["x-apikey"] == "${secret:webiq_key}"


def test_flatten_taskgroup_error_surfaces_cause():
    """ExceptionGroup (anyio TaskGroup) is flattened to the concrete inner message."""
    from lmstudioclaw.capabilities.mcp_client import _flatten_error

    try:
        grp = ExceptionGroup("unhandled errors in a TaskGroup",
                             [ConnectionRefusedError("nope")])
    except TypeError:  # Python < 3.11 has no built-in ExceptionGroup
        return
    msg = _flatten_error(grp)
    assert "ConnectionRefusedError" in msg and "nope" in msg
    assert "TaskGroup" not in msg


def test_resolve_stdio_command_wraps_cmd_shim(monkeypatch):
    """On Windows an ``npx`` (npx.cmd) command is launched via ``cmd /c`` (WinError 193 fix)."""
    import lmstudioclaw.capabilities.mcp_client as mc

    monkeypatch.setattr(mc.os, "name", "nt", raising=False)
    monkeypatch.setattr(mc.shutil, "which", lambda c: r"C:\Program Files\nodejs\npx.cmd")
    cmd, args = mc._resolve_stdio_command("npx", ["@playwright/mcp@latest", "--extension"])
    assert cmd == "cmd"
    assert args[:2] == ["/c", r"C:\Program Files\nodejs\npx.cmd"]
    assert args[2:] == ["@playwright/mcp@latest", "--extension"]


def test_resolve_stdio_command_passthrough_exe(monkeypatch):
    """A real ``.exe`` (or non-Windows) command is passed through unchanged."""
    import lmstudioclaw.capabilities.mcp_client as mc

    monkeypatch.setattr(mc.os, "name", "nt", raising=False)
    monkeypatch.setattr(mc.shutil, "which", lambda c: r"C:\tools\server.exe")
    cmd, args = mc._resolve_stdio_command("server", ["--flag"])
    assert cmd == r"C:\tools\server.exe" and args == ["--flag"]


def test_capability_metadata_persists_mcp_tools(temp_app_paths):
    """An MCP capability row round-trips a ``metadata`` blob (the per-tool list)."""
    store = Store(temp_app_paths.db_path)
    store.upsert_capability({
        "kind": "mcp", "name": "webiq", "description": "MCP server 'webiq'",
        "status": "valid",
        "metadata": {"tools": [{"name": "browse", "description": "Fetch a page"}]},
    })
    row = store.get_capability_by_kind_name("mcp", "webiq")
    # list_capabilities decodes metadata JSON back into a dict.
    cap = next(c for c in store.list_capabilities("mcp") if c["id"] == row["id"])
    assert cap["metadata"]["tools"][0]["name"] == "browse"
    # A later upsert without metadata must not wipe the stored tools.
    store.upsert_capability({"kind": "mcp", "name": "webiq",
                             "description": "MCP server 'webiq'", "status": "valid"})
    cap2 = next(c for c in store.list_capabilities("mcp") if c["id"] == row["id"])
    assert cap2["metadata"]["tools"][0]["name"] == "browse"
