"""Application settings: load/save with safe defaults.

Settings are a file-backed singleton (JSON under ``%APPDATA%``). Secret values are
**never** stored here — only a reference name to the vault (FR-026/FR-076). All I/O
is best-effort so a missing/locked file never crashes the controller (Constitution II).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Settings:
    """User-configurable runtime settings with safe defaults (FR-044–FR-052)."""

    theme: str = "system"                      # dark | light | system
    default_model: str | None = None
    startup_launch: bool = False               # launch on login, start minimized
    notifications: dict[str, bool] = field(
        default_factory=lambda: {
            "automation_running": True,
            "automation_missed": True,
            "run_completed": True,
            "run_failed": True,
            "system": True,
        }
    )
    web_port: int = 8765                        # fallback applied if taken (FR-055)
    lmstudio_base_url: str = "http://localhost:1234"
    lmstudio_api_key_ref: str = "lmstudio_api_key"  # vault reference name, not a value
    idle_unload: bool = True
    session_idle_timeout: int = 600             # seconds before idle unload (FR-002)
    compression_threshold: float = 0.90         # fraction of context (FR-061)
    max_run_duration: int = 3600                # seconds hard cap per run (FR-062)
    retention_days: int = 90                    # session history window (FR-051)
    summarize_mcp_outputs: bool = True          # condense large MCP results before context

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict for JSON persistence."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Settings":
        """Build settings from a dict, ignoring unknown keys and keeping defaults."""
        known = {f for f in cls.__dataclass_fields__}
        clean = {k: v for k, v in (data or {}).items() if k in known}
        base = cls()
        for k, v in clean.items():
            setattr(base, k, v)
        # Clamp the compression threshold into a sane range.
        base.compression_threshold = min(0.99, max(0.5, float(base.compression_threshold)))
        return base


def load_settings(path: Path) -> Settings:
    """Load settings from ``path``; return defaults if missing or unreadable."""
    if not path.exists():
        return Settings()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return Settings.from_dict(data)
    except (OSError, json.JSONDecodeError, ValueError):
        return Settings()


def save_settings(path: Path, settings: Settings) -> None:
    """Persist settings to ``path`` (best-effort; never raises)."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(settings.to_dict(), indent=2), encoding="utf-8")
    except OSError:
        pass
