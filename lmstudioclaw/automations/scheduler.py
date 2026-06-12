"""Automation scheduler.

Event-driven (Constitution V): instead of busy-polling, the scheduler sleeps until
the nearest automation's next fire time, then enqueues a session and recomputes. A
refresh event wakes it early when automations change. On startup it detects missed
runs (a fire time elapsed while the app was off) and reports them (FR-030/FR-031).

Supports two schedule types (FR-063):

* **daily** — fires on selected weekdays at a time-of-day.
* **interval** — fires every X minutes/hours/days.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, time as dtime, timedelta

# Map interval units to timedelta kwargs.
_UNIT_DELTA = {
    "minutes": lambda v: timedelta(minutes=v),
    "hours": lambda v: timedelta(hours=v),
    "days": lambda v: timedelta(days=v),
}


def _parse_dt(value: str | None) -> datetime | None:
    """Parse an ISO timestamp, dropping tz for naive local comparison."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).replace(tzinfo=None)
    except ValueError:
        return None


def next_fire(automation: dict, now: datetime) -> datetime | None:
    """Compute the next fire time for an automation strictly after ``now``.

    Returns None if the automation is malformed (no valid schedule).
    """
    if automation.get("schedule_type") == "daily":
        return _next_daily(automation, now)
    if automation.get("schedule_type") == "interval":
        return _next_interval(automation, now)
    return None


def _next_daily(automation: dict, now: datetime) -> datetime | None:
    """Next occurrence on one of ``daily_days`` (Mon=0..Sun=6) at ``daily_time``."""
    days = automation.get("daily_days") or []
    time_str = automation.get("daily_time")
    if not days or not time_str:
        return None
    try:
        hh, mm = (int(p) for p in time_str.split(":")[:2])
        target_time = dtime(hour=hh, minute=mm)
    except (ValueError, TypeError):
        return None
    # Search up to 8 days ahead for the next matching weekday/time.
    for offset in range(0, 8):
        candidate_date = (now + timedelta(days=offset)).date()
        candidate = datetime.combine(candidate_date, target_time)
        if candidate.weekday() in days and candidate > now:
            return candidate
    return None


def _next_interval(automation: dict, now: datetime) -> datetime | None:
    """Next fire for an interval schedule: base + N×unit, advanced past ``now``."""
    unit = automation.get("interval_unit")
    value = automation.get("interval_value") or 0
    if unit not in _UNIT_DELTA or value <= 0:
        return None
    delta = _UNIT_DELTA[unit](value)
    base = _parse_dt(automation.get("last_run_at")) or _parse_dt(automation.get("created_at")) or now
    candidate = base + delta
    while candidate <= now:
        candidate += delta
    return candidate


class Scheduler:
    """Drives automations on time, with event-driven sleep (no busy poll)."""

    def __init__(self, store, enqueue: Callable[[dict], str]) -> None:
        """Wire to the store and a callback that enqueues a fired automation."""
        self._store = store
        self._enqueue = enqueue
        self._wakeup = asyncio.Event()
        self._stopped = False

    def refresh(self) -> None:
        """Wake the scheduler to recompute after automations change."""
        self._wakeup.set()

    def stop(self) -> None:
        """Stop the scheduler loop."""
        self._stopped = True
        self._wakeup.set()

    def report_missed(self, notify: Callable[[str, str], None]) -> list[dict]:
        """Detect and report fires missed while the app was off (FR-030/FR-031)."""
        now = datetime.now()
        missed = self.detect_missed(now)
        for automation in missed:
            msg = f"Automation '{automation['name']}' missed its scheduled run."
            notify("automation_missed", msg)
            self._store.add_notification(
                type="automation_missed", message=msg, related_automation_id=automation["id"]
            )
            self._store.update_automation(automation["id"], last_run_result="missed")
        return missed

    def detect_missed(self, now: datetime) -> list[dict]:
        """Return enabled automations whose recorded ``next_run_at`` already elapsed."""
        missed = []
        for automation in self._store.list_automations():
            if not automation.get("enabled"):
                continue
            expected = _parse_dt(automation.get("next_run_at"))
            last_run = _parse_dt(automation.get("last_run_at"))
            if expected and expected < now and (last_run is None or last_run < expected):
                missed.append(automation)
        return missed

    async def run(self) -> None:
        """Main loop: sleep until the nearest fire, enqueue it, recompute."""
        # Seed next_run_at for all automations on startup.
        self._recompute_all()
        while not self._stopped:
            automation, fire_at = self._nearest()
            now = datetime.now()
            if automation is None or fire_at is None:
                # Nothing scheduled — wait for a refresh.
                self._wakeup.clear()
                await self._wakeup.wait()
                continue
            delay = max(0.0, (fire_at - now).total_seconds())
            self._wakeup.clear()
            try:
                await asyncio.wait_for(self._wakeup.wait(), timeout=delay)
                continue  # woken early by refresh -> recompute
            except asyncio.TimeoutError:
                pass  # time to fire
            if self._stopped:
                break
            self._fire(automation)

    def _fire(self, automation: dict) -> None:
        """Enqueue a fired automation and stamp its run bookkeeping."""
        fired_at = datetime.now()
        try:
            self._enqueue(automation)
        except Exception:
            pass  # best-effort; controller records its own failures
        nxt = next_fire({**automation, "last_run_at": fired_at.isoformat()}, fired_at)
        self._store.update_automation(
            automation["id"], last_run_at=fired_at.isoformat(),
            next_run_at=nxt.isoformat() if nxt else None,
        )

    def _recompute_all(self) -> None:
        """Recompute and persist ``next_run_at`` for every enabled automation."""
        now = datetime.now()
        for automation in self._store.list_automations():
            if not automation.get("enabled"):
                continue
            nxt = next_fire(automation, now)
            self._store.update_automation(
                automation["id"], next_run_at=nxt.isoformat() if nxt else None
            )

    def _nearest(self) -> tuple[dict | None, datetime | None]:
        """Return the enabled automation with the soonest next fire time."""
        now = datetime.now()
        best: tuple[dict | None, datetime | None] = (None, None)
        for automation in self._store.list_automations():
            if not automation.get("enabled"):
                continue
            nxt = next_fire(automation, now)
            if nxt is None:
                continue
            if best[1] is None or nxt < best[1]:
                best = (automation, nxt)
        return best
