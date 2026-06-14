"""Isolated secrets vault.

Secrets live in a JSON file under ``%APPDATA%/LMStudioClaw/secrets/`` — **outside**
any agent-accessible path and on the consent gate's hard deny-list (FR-076/FR-077).

Security rules enforced here:

* Only the user (via the secrets REST endpoints) may ``set``/``delete`` values (FR-078).
* The agent has **no** path to read a value: there is intentionally no ``get_value``
  surfaced to orchestrator/registry code. Only :meth:`inject` is available, and it is
  used solely at connection-build time by trusted runtime code.
* Listing returns reference names + owners only — never values (FR-026).

The file is plaintext on a single-user machine (accepted in clarify session 3), but
its isolation + deny-listing prevents the agent from reaching it.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any


class SecretsVault:
    """File-backed secret store with user-only writes and no agent read path."""

    def __init__(self, secrets_dir: Path) -> None:
        """Initialize the vault, creating the isolated directory if needed."""
        self._dir = secrets_dir
        self._file = secrets_dir / "secrets.json"
        try:
            secrets_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    def _read(self) -> dict[str, dict[str, Any]]:
        """Read the raw vault contents (internal only)."""
        if not self._file.exists():
            return {}
        try:
            data = json.loads(self._file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _write(self, data: dict[str, dict[str, Any]]) -> None:
        """Write the vault file with best-effort restrictive permissions."""
        try:
            self._file.parent.mkdir(parents=True, exist_ok=True)
            self._file.write_text(json.dumps(data, indent=2), encoding="utf-8")
            # Best-effort: restrict to the owner where the OS supports it.
            try:
                os.chmod(self._file, stat.S_IRUSR | stat.S_IWUSR)
            except OSError:
                pass
        except OSError:
            pass

    # -- user-only mutations (FR-078) --------------------------------------

    def set(self, ref_name: str, value: str, owner: str = "mcp") -> None:
        """Set/replace a secret value. **User-initiated only.**"""
        data = self._read()
        data[ref_name] = {"value": value, "owner": owner}
        self._write(data)

    def delete(self, ref_name: str) -> None:
        """Remove a secret by reference name."""
        data = self._read()
        if ref_name in data:
            del data[ref_name]
            self._write(data)

    def rename(self, old_ref: str, new_ref: str, value: str | None = None) -> bool:
        """Rename a secret (and optionally replace its value). **User-initiated only.**

        Returns ``False`` if ``old_ref`` does not exist, or if ``new_ref`` already
        exists and differs from ``old_ref`` (caller surfaces a conflict). When
        ``value`` is ``None`` the existing value is preserved; otherwise it is
        replaced. The owner is preserved.
        """
        data = self._read()
        if old_ref not in data:
            return False
        if new_ref != old_ref and new_ref in data:
            return False
        entry = dict(data.pop(old_ref))
        if value is not None:
            entry["value"] = value
        data[new_ref] = entry
        self._write(data)
        return True

    # -- safe metadata (no values, FR-026) ---------------------------------

    def list_refs(self) -> list[dict[str, str]]:
        """List secret reference names + owners only — never the values."""
        return [
            {"ref_name": ref, "owner": meta.get("owner", "mcp")}
            for ref, meta in self._read().items()
        ]

    def has(self, ref_name: str) -> bool:
        """Return whether a secret with this reference name exists."""
        return ref_name in self._read()

    # -- runtime-only injection (never serialized to agent/UI/logs) --------

    def inject(self, mapping: dict[str, str]) -> dict[str, str]:
        """Resolve ``{placeholder: ref_name}`` into ``{placeholder: value}``.

        Used at connection-build time by trusted runtime code only (e.g. setting an
        MCP server's API key header). The returned values must never be logged,
        echoed to the UI, or placed into agent context (FR-077). Unknown references
        are dropped silently so a missing secret cannot leak as a placeholder.
        """
        data = self._read()
        resolved: dict[str, str] = {}
        for placeholder, ref in mapping.items():
            entry = data.get(ref)
            if entry and "value" in entry:
                resolved[placeholder] = entry["value"]
        return resolved
