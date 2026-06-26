"""Unit tests for the Windows autostart (Startup-folder shortcut) helper.

The real `enable()` shells out to PowerShell to create a `.lnk`; here we patch that
helper so the test stays OS-independent and just verifies the create/remove/reconcile
logic around the Startup folder.
"""

from __future__ import annotations

from pathlib import Path

from lmstudioclaw.config import autostart


def _redirect_startup(monkeypatch, tmp_path: Path) -> Path:
    """Point the Startup folder at a temp dir and make enable() 'create' the shortcut."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    startup = tmp_path / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"

    def fake_ps(_script: str) -> bool:
        # Simulate the WScript.Shell shortcut creation by touching the .lnk file.
        startup.mkdir(parents=True, exist_ok=True)
        (startup / "LMStudioClaw.lnk").write_text("shortcut", encoding="utf-8")
        return True

    monkeypatch.setattr(autostart, "_run_powershell", fake_ps)
    return startup


def test_disabled_by_default(monkeypatch, tmp_path):
    """With no shortcut present, autostart reports disabled."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert autostart.is_enabled() is False


def test_enable_creates_shortcut(monkeypatch, tmp_path):
    """enable() creates the Startup shortcut and is_enabled() then reports True."""
    startup = _redirect_startup(monkeypatch, tmp_path)
    assert autostart.enable() is True
    assert (startup / "LMStudioClaw.lnk").exists()
    assert autostart.is_enabled() is True


def test_disable_removes_shortcut(monkeypatch, tmp_path):
    """disable() removes the shortcut; disabling when already absent is still fine."""
    _redirect_startup(monkeypatch, tmp_path)
    autostart.enable()
    assert autostart.disable() is True
    assert autostart.is_enabled() is False
    # Idempotent: removing again does not raise.
    assert autostart.disable() is True


def test_apply_reconciles_state(monkeypatch, tmp_path):
    """apply() returns the resulting real state for both directions."""
    _redirect_startup(monkeypatch, tmp_path)
    assert autostart.apply(True) is True
    assert autostart.apply(False) is False
