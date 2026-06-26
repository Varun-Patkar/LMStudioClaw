"""Windows "launch on login" autostart via a Startup-folder shortcut.

The `startup_launch` setting only persisted a flag before — nothing ever registered
the app with Windows, so the in-app toggle and Task Manager's Startup tab could
disagree. This module makes the toggle real and *reconcilable*: it creates/removes a
``LMStudioClaw.lnk`` shortcut in the per-user Startup folder (the same
``shell:startup`` location the README suggests) and can report the true on-disk state.

Everything here is best-effort and Windows-only: failures return ``False`` rather than
raising, so a hiccup never crashes settings persistence.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# CREATE_NO_WINDOW keeps the helper PowerShell process from flashing a console when
# the controller runs under pythonw.
_NO_WINDOW = 0x08000000

_SHORTCUT_NAME = "LMStudioClaw.lnk"


def _startup_dir() -> Path:
    """Return the current user's Windows Startup folder (shell:startup)."""
    appdata = os.environ.get("APPDATA", "")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def shortcut_path() -> Path:
    """Full path of the autostart shortcut we manage."""
    return _startup_dir() / _SHORTCUT_NAME


def _pythonw() -> str:
    """Best path to ``pythonw.exe`` so login launch shows no console window."""
    exe = Path(sys.executable)
    candidate = exe.with_name("pythonw.exe")
    return str(candidate if candidate.exists() else exe)


def _run_powershell(script: str) -> bool:
    """Run a PowerShell snippet quietly; return True on a clean exit."""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=20, creationflags=_NO_WINDOW,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def is_enabled() -> bool:
    """Return whether the autostart shortcut currently exists on disk."""
    try:
        return shortcut_path().exists()
    except OSError:
        return False


def enable() -> bool:
    """Create the Startup-folder shortcut. Returns True on success (best-effort)."""
    try:
        target = shortcut_path()
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False

    # Launch the controller windowless via ``pythonw -m lmstudioclaw.cli``. WindowStyle 7
    # = minimized; the app lives in the tray regardless.
    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}'); "
        "$s.TargetPath = '{py}'; "
        "$s.Arguments = '-m lmstudioclaw.cli'; "
        "$s.WorkingDirectory = '{cwd}'; "
        "$s.WindowStyle = 7; "
        "$s.Description = 'LMStudioClaw controller'; "
        "$s.Save()"
    ).format(
        lnk=str(shortcut_path()).replace("'", "''"),
        py=_pythonw().replace("'", "''"),
        cwd=str(Path.home()).replace("'", "''"),
    )
    if _run_powershell(script):
        return True
    # If the shortcut somehow exists despite a non-zero exit, treat as enabled.
    return is_enabled()


def disable() -> bool:
    """Remove the Startup-folder shortcut. Returns True if it is gone afterwards."""
    try:
        shortcut_path().unlink(missing_ok=True)
        return True
    except OSError:
        return not is_enabled()


def apply(enabled: bool) -> bool:
    """Enable or disable autostart to match ``enabled``; return the resulting state."""
    if enabled:
        enable()
    else:
        disable()
    return is_enabled()
