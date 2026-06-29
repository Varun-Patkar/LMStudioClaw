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
    brain_dir: Path      # base/memory/brain  (per-node detail markdown)
    graph_db: Path       # base/graph.db  (agent graph memory: nodes + edges)
    logs_dir: Path       # base/logs  (detailed per-session JSON logs + HTML viewer)
    mcp_json: Path       # base/mcp.json
    app_data: Path       # %APPDATA%/LMStudioClaw  (controller state: db, settings)
    secrets_dir: Path    # %APPDATA%/LMStudioClaw/secrets  (isolated; deny-listed)
    db_path: Path        # app_data/state.db
    settings_path: Path  # app_data/settings.json
    app_root: Path       # the installed/cloned application directory (deny-listed)

    @property
    def deny_list(self) -> tuple[Path, ...]:
        """Canonical paths the consent gate must always refuse (FR-077).

        Covers (a) the isolated secrets directory, (b) the controller's internal
        application-data directory, (c) the detailed session **logs** (the audit
        trail — only the controller/webapp may write them, never the agent, so it
        cannot tamper with or read its own prompt-injection record), and (d) the
        application's own installed/cloned code (so the agent can never modify the
        program it runs inside).
        """
        return (
            _canon(self.secrets_dir),
            _canon(self.app_data),
            _canon(self.logs_dir),
            _canon(self.app_root),
        )


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
    # The application's own code directory, deny-listed so the agent can never modify
    # the program it runs inside. For a source checkout (``pyproject.toml`` at the
    # repo root) deny the whole clone — it also holds the frontend source and built UI;
    # for a site-packages install deny just the ``lmstudioclaw`` package directory so we
    # don't over-broadly block all of site-packages.
    _pkg_dir = Path(__file__).resolve().parents[1]       # .../lmstudioclaw
    _clone_root = Path(__file__).resolve().parents[2]    # repo root / site-packages
    app_root = _clone_root if (_clone_root / "pyproject.toml").exists() else _pkg_dir
    return AppPaths(
        base=_canon(base),
        skills=_canon(base / "skills"),
        tools=_canon(base / "tools"),
        workspace=_canon(base / "workspace"),
        memory=_canon(base / "memory"),
        brain_dir=_canon(base / "memory" / "brain"),
        graph_db=_canon(base / "graph.db"),
        logs_dir=_canon(base / "logs"),
        mcp_json=_canon(base / "mcp.json"),
        app_data=_canon(app_data),
        secrets_dir=_canon(secrets_dir),
        db_path=_canon(app_data / "state.db"),
        settings_path=_canon(app_data / "settings.json"),
        app_root=_canon(app_root),
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
        paths.brain_dir,
        paths.logs_dir,
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
