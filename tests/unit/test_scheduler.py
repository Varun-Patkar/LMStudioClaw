"""Unit tests for scheduler next_fire + missed-run detection (SC-006, SC-015)."""

from __future__ import annotations

from datetime import datetime, timedelta

from lmstudioclaw.automations.scheduler import Scheduler, next_fire
from lmstudioclaw.sessions.store import Store


def test_interval_next_fire_advances_past_now():
    now = datetime(2026, 6, 12, 10, 0, 0)
    automation = {
        "schedule_type": "interval", "interval_unit": "hours", "interval_value": 6,
        "created_at": "2026-06-12T00:00:00",
    }
    nxt = next_fire(automation, now)
    assert nxt is not None and nxt > now
    # 00:00 + 6h steps -> first fire strictly after 10:00 is 12:00.
    assert nxt == datetime(2026, 6, 12, 12, 0, 0)


def test_interval_uses_last_run():
    now = datetime(2026, 6, 12, 10, 0, 0)
    automation = {
        "schedule_type": "interval", "interval_unit": "minutes", "interval_value": 30,
        "last_run_at": "2026-06-12T09:45:00",
    }
    nxt = next_fire(automation, now)
    assert nxt == datetime(2026, 6, 12, 10, 15, 0)


def test_daily_next_fire_picks_weekday():
    # 2026-06-12 is a Friday (weekday 4).
    now = datetime(2026, 6, 12, 10, 0, 0)
    automation = {
        "schedule_type": "daily", "daily_days": [0], "daily_time": "08:00",  # Monday
    }
    nxt = next_fire(automation, now)
    # Next Monday at 08:00 is 2026-06-15.
    assert nxt == datetime(2026, 6, 15, 8, 0, 0)
    assert nxt.weekday() == 0


def test_daily_same_day_later_time():
    now = datetime(2026, 6, 12, 6, 0, 0)  # Friday 06:00
    automation = {"schedule_type": "daily", "daily_days": [4], "daily_time": "09:00"}
    nxt = next_fire(automation, now)
    assert nxt == datetime(2026, 6, 12, 9, 0, 0)


def test_malformed_schedule_returns_none():
    assert next_fire({"schedule_type": "interval", "interval_value": 0}, datetime.now()) is None
    assert next_fire({"schedule_type": "daily"}, datetime.now()) is None


def test_detect_missed(temp_app_paths):
    store = Store(temp_app_paths.db_path)
    aid = store.create_automation({
        "name": "m", "task": "t", "schedule_type": "interval",
        "interval_unit": "hours", "interval_value": 1, "enabled": True,
    })
    # Set next_run_at in the past with no last run -> missed.
    past = (datetime.now() - timedelta(hours=2)).isoformat()
    store.update_automation(aid, next_run_at=past)

    sched = Scheduler(store, lambda a: "sid")
    missed = sched.detect_missed(datetime.now())
    assert any(a["id"] == aid for a in missed)


def test_report_missed_notifies(temp_app_paths):
    store = Store(temp_app_paths.db_path)
    aid = store.create_automation({
        "name": "m", "task": "t", "schedule_type": "interval",
        "interval_unit": "hours", "interval_value": 1, "enabled": True,
    })
    store.update_automation(aid, next_run_at=(datetime.now() - timedelta(hours=2)).isoformat())

    events: list[tuple[str, str]] = []
    sched = Scheduler(store, lambda a: "sid")
    sched.report_missed(lambda t, m: events.append((t, m)))
    assert any(t == "automation_missed" for t, _ in events)
    assert store.get_automation(aid)["last_run_result"] == "missed"
