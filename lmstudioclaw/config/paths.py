"""Filesystem path resolution and first-run bootstrap.

Resolves the agent's Documents working area (``Documents\\LMStudioClaw\\``) and the
**isolated** secrets directory under ``%APPDATA%`` that lives *outside* any
agent-accessible path (FR-053, FR-076).

The Documents layout (FR-053):

    Documents/LMStudioClaw/
        skills/        # SKILL.md skill folders
        tools/         # custom python tools
        workspace/     # agent read/write playground (default-allowed folder)
        memory/        # durable agent learnings
        mcp.json       # MCP server configuration

Secrets live separately at ``%APPDATA%/LMStudioClaw/secrets/`` and are on the consent
gate's hard deny-list so the agent can never reach them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# The app/folder name confirmed during specification.
APP_DIR_NAME = "LMStudioClaw"


def _documents_root() -> Path:
    """Resolve the current user's Documents folder on Windows.

    Falls back to ``~/Documents`` if the registry/known-folder lookup is unavailable
    (e.g. on non-Windows during tests).
    """
    # Honour an explicit override (useful for tests/sandboxes).
    override = os.environ.get("LMSTUDIOCLAW_DOCUMENTS")
    if override:
        return Path(override)
    return Path.home() / "Documents"


def _appdata_root() -> Path:
    """Resolve the per-user roaming application-data root (``%APPDATA%``)."""
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata)
    # Non-Windows / test fallback.
    return Path.home() / ".config"


@dataclass(frozen=True)
class AppPaths:
    """Resolved absolute paths for the agent runtime.

    All paths are absolute. ``secrets_dir`` is intentionally located outside
    ``base`` so it can never fall under an agent folder grant (FR-076/FR-077).
    """

    base: Path           # Documents/LMStudioClaw
    skills: Path         # base/skills
    tools: Path          # base/tools
    workspace: Path      # base/workspace (default-allowed)
    memory: Path         # base/memory
    mcp_json: Path       # base/mcp.json
    app_data: Path       # %APPDATA%/LMStudioClaw  (controller state: db, settings)
    secrets_dir: Path    # %APPDATA%/LMStudioClaw/secrets  (isolated; deny-listed)
    db_path: Path        # app_data/state.db
    settings_path: Path  # app_data/settings.json

    @property
    def deny_list(self) -> tuple[Path, ...]:
        """Canonical paths the consent gate must always refuse (FR-077).

        Covers the isolated secrets directory and the controller's internal
        application-data directory.
        """
        return (_canon(self.secrets_dir), _canon(self.app_data))


def _canon(path: Path) -> Path:
    """Return a canonical absolute path (resolves ``..`` and symlinks if present).

    Uses ``strict=False`` so non-existent paths still canonicalize (the consent
    gate must reason about paths before they are created).
    """
    try:
        return path.resolve(strict=False)
    except OSError:
        return path.absolute()


def resolve_paths() -> AppPaths:
    """Compute all runtime paths without creating anything on disk."""
    base = _documents_root() / APP_DIR_NAME
    app_data = _appdata_root() / APP_DIR_NAME
    secrets_dir = app_data / "secrets"
    return AppPaths(
        base=_canon(base),
        skills=_canon(base / "skills"),
        tools=_canon(base / "tools"),
        workspace=_canon(base / "workspace"),
        memory=_canon(base / "memory"),
        mcp_json=_canon(base / "mcp.json"),
        app_data=_canon(app_data),
        secrets_dir=_canon(secrets_dir),
        db_path=_canon(app_data / "state.db"),
        settings_path=_canon(app_data / "settings.json"),
    )


def bootstrap(paths: AppPaths | None = None) -> tuple[AppPaths, list[str]]:
    """Create the folder layout on first run; warn (not crash) if uncreatable.

    Best-effort per Constitution II / FR-053: any directory that cannot be created
    is reported as a warning string rather than raising, so the controller can still
    start and surface the problem in the UI (SC-009).

    Returns the resolved :class:`AppPaths` and a list of human-readable warnings
    (empty when everything was created successfully).
    """
    paths = paths or resolve_paths()
    warnings: list[str] = []

    # Directories that must exist for the runtime to function.
    required_dirs = [
        paths.base,
        paths.skills,
        paths.tools,
        paths.workspace,
        paths.memory,
        paths.app_data,
        paths.secrets_dir,
    ]
    for directory in required_dirs:
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            warnings.append(f"Could not create directory '{directory}': {exc}")

    # Seed an empty MCP config so users/agents have a file to edit.
    if not paths.mcp_json.exists():
        try:
            paths.mcp_json.write_text('{\n  "mcpServers": {}\n}\n', encoding="utf-8")
        except OSError as exc:
            warnings.append(f"Could not create '{paths.mcp_json}': {exc}")

    return paths, warnings
