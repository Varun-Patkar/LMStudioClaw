"""Tiny helper launched by a Windows Scheduled Task to fire one automation.

Invoked as ``pythonw -m lmstudioclaw.taskrunner <automation_id>`` at an automation's
scheduled time (see :mod:`lmstudioclaw.automations.tasksched`). It bridges the gap
between Task Scheduler and the (possibly closed) controller:

* If the controller is **already running**, ask it over its local HTTP API to run the
  automation now — no second instance is started.
* If the controller is **closed**, start it (``pythonw -m lmstudioclaw.cli
  --run-automation <id>``); the freshly started app runs that automation once it's
  ready.

This only ever happens while the PC is on and the user is signed in (the task runs in
the interactive session). It is intentionally dependency-free (stdlib only) and
best-effort: any failure exits quietly.
"""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.request
from pathlib import Path

# Detached/no-window flags so the launched controller has no console and outlives us.
_NO_WINDOW = 0x08000000
_DETACHED = 0x00000008


def _runtime_file() -> Path:
    """Path to the controller's runtime marker (written while it is running)."""
    from .config.paths import resolve_paths

    return resolve_paths().app_data / "runtime.json"


def _running_url() -> str | None:
    """Return the live controller's base URL if its runtime marker is present."""
    try:
        data = json.loads(_runtime_file().read_text(encoding="utf-8"))
        url = data.get("url")
        return url if isinstance(url, str) and url else None
    except (OSError, ValueError):
        return None


def _trigger_running(url: str, automation_id: str) -> bool:
    """Ask an already-running controller to run the automation now (True on success)."""
    endpoint = f"{url.rstrip('/')}/api/automations/{automation_id}/run"
    try:
        req = urllib.request.Request(endpoint, data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=8) as resp:  # noqa: S310 - localhost only
            return 200 <= resp.status < 300
    except Exception:
        return False


def _launch_controller(automation_id: str) -> None:
    """Start the controller and ask it to run the automation once ready (best-effort)."""
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    exe = str(pythonw if pythonw.exists() else sys.executable)
    try:
        subprocess.Popen(
            [exe, "-m", "lmstudioclaw.cli", "--run-automation", automation_id],
            creationflags=_NO_WINDOW | _DETACHED, close_fds=True,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def main(argv: list[str] | None = None) -> int:
    """Entry point: run the given automation, starting the app if needed."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        return 2
    automation_id = argv[0]
    url = _running_url()
    if url and _trigger_running(url, automation_id):
        return 0
    # Not running (or unreachable) — start it and hand off the automation id.
    _launch_controller(automation_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
