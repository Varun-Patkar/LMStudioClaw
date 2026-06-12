"""Shared pytest fixtures for the agent-runtime test suite.

Provides an isolated temporary Documents/app-data layout so tests never touch the
real user folders or secrets vault.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from lmstudioclaw.config.paths import AppPaths, bootstrap, resolve_paths


@pytest.fixture()
def temp_app_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[AppPaths]:
    """Resolve and bootstrap an isolated path layout under a temp directory."""
    docs = tmp_path / "Documents"
    appdata = tmp_path / "AppData"
    docs.mkdir(parents=True, exist_ok=True)
    appdata.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("LMSTUDIOCLAW_DOCUMENTS", str(docs))
    monkeypatch.setenv("APPDATA", str(appdata))
    paths, _warnings = bootstrap(resolve_paths())
    yield paths


@pytest.fixture(autouse=True)
def _no_real_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Guard against accidental writes to the developer's real home directory."""
    # Only redirect when a test hasn't set up its own paths explicitly.
    if "LMSTUDIOCLAW_DOCUMENTS" not in os.environ:
        monkeypatch.setenv("LMSTUDIOCLAW_DOCUMENTS", str(tmp_path / "Documents"))
