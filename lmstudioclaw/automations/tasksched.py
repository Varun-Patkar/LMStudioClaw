"""Windows Task Scheduler integration for automations (opt-in).

The in-app scheduler only fires automations while the controller is running. This
module registers a per-automation Windows Scheduled Task (via ``schtasks.exe``) so a
due automation also fires when the app is **closed**: the task launches a tiny helper
(:mod:`lmstudioclaw.taskrunner`) which either tells the already-running app to run the
automation, or starts the app and asks it to run that automation once it's ready.

Design choices (confirmed with the user):

* **Opt-in** — controlled by the ``use_task_scheduler`` setting; nothing touches the
  system Task Scheduler unless the user turns it on.
* **Signed-in only** — tasks run in the interactive logon session (no stored password,
  ``/RL LIMITED``). They naturally do nothing while the PC is off and never wake it.

Everything here is best-effort and Windows-only: a failure returns ``False`` (and is
swallowed by callers) rather than crashing automation management.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Keep the helper schtasks process from flashing a console under pythonw.
_NO_WINDOW = 0x08000000

# Task Scheduler folder/prefix so all our tasks are grouped and easy to find/remove.
_TASK_PREFIX = "LMStudioClaw"

# schtasks weekday tokens indexed by Python's Monday=0..Sunday=6.
_WEEKDAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]


def _pythonw() -> str:
    """Best path to ``pythonw.exe`` so the launched helper shows no console."""
    exe = Path(sys.executable)
    candidate = exe.with_name("pythonw.exe")
    return str(candidate if candidate.exists() else exe)


def task_name(automation_id: str) -> str:
    r"""Return the Task Scheduler name (``LMStudioClaw\auto_<id>``)."""
    return f"{_TASK_PREFIX}\\auto_{automation_id}"


def _run(args: list[str]) -> bool:
    """Run a ``schtasks`` command quietly; return True on a clean exit (best-effort)."""
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=20, creationflags=_NO_WINDOW,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _trigger_args(automation: dict) -> list[str] | None:
    """Build the schedule portion of the schtasks command for an automation.

    Returns ``None`` for a malformed schedule (so we skip registering it).
    """
    stype = automation.get("schedule_type")
    if stype == "daily":
        days = automation.get("daily_days") or []
        time_str = automation.get("daily_time")
        if not days or not time_str:
            return None
        try:
            hh, mm = (int(p) for p in str(time_str).split(":")[:2])
        except (ValueError, TypeError):
            return None
        tokens = ",".join(_WEEKDAYS[d] for d in sorted(set(days)) if 0 <= d < 7)
        if not tokens:
            return None
        return ["/SC", "WEEKLY", "/D", tokens, "/ST", f"{hh:02d}:{mm:02d}"]
    if stype == "interval":
        unit = automation.get("interval_unit")
        value = int(automation.get("interval_value") or 0)
        if value <= 0:
            return None
        if unit == "minutes":
            return ["/SC", "MINUTE", "/MO", str(value)]
        if unit == "hours":
            return ["/SC", "HOURLY", "/MO", str(value)]
        if unit == "days":
            return ["/SC", "DAILY", "/MO", str(value)]
    return None


def register(automation: dict) -> bool:
    """Create/replace the Windows task for one automation. Returns True on success."""
    schedule = _trigger_args(automation)
    if schedule is None:
        return False
    # The action: launch the helper for this automation id. schtasks takes the whole
    # command (program + args) as a single quoted /TR string.
    command = f'"{_pythonw()}" -m lmstudioclaw.taskrunner {automation["id"]}'
    args = [
        "schtasks", "/Create", "/F",
        "/TN", task_name(automation["id"]),
        "/TR", command,
        "/RL", "LIMITED",      # run with the user's normal privileges (no elevation)
        *schedule,
    ]
    return _run(args)


def unregister(automation_id: str) -> bool:
    """Delete the Windows task for one automation (True if gone afterwards)."""
    return _run(["schtasks", "/Delete", "/F", "/TN", task_name(automation_id)])


def sync(automations: list[dict], *, enabled: bool) -> None:
    """Reconcile Windows tasks with the current automations (best-effort).

    When ``enabled`` is False, every managed task is removed. Otherwise a task is
    registered for each enabled automation and removed for disabled/deleted ones.
    """
    for automation in automations:
        aid = automation.get("id")
        if not aid:
            continue
        if enabled and automation.get("enabled"):
            register(automation)
        else:
            unregister(aid)
