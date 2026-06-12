"""Windows toast notifications.

Surfaces run and automation events (FR-042). Messages must never contain secret
values (FR-026). Notifications are best-effort: a failure to show a toast (missing
library, headless session) never propagates to the controller.
"""

from __future__ import annotations

try:
    from win11toast import notify as _win_notify
except Exception:  # pragma: no cover - non-Windows / missing dependency
    _win_notify = None


# Human-readable titles per notification type.
_TITLES = {
    "automation_running": "Automation running",
    "automation_missed": "Automation missed",
    "run_completed": "Run completed",
    "run_failed": "Run failed",
    "system": "LM Studio Manager",
}


def notify(notification_type: str, message: str) -> None:
    """Show a Windows toast for an event (best-effort, never raises).

    ``message`` is assumed to be secret-free by the caller (FR-026).
    """
    title = _TITLES.get(notification_type, "LM Studio Manager")
    if _win_notify is None:
        return
    try:
        _win_notify(title, message, app_id="LM Studio Manager")
    except Exception:  # pragma: no cover - toast backends can fail in odd ways
        pass
