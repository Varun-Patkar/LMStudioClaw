"""Unit tests for the Windows Task Scheduler schedule-string builder.

Only the pure ``_trigger_args`` mapping is exercised — actually shelling out to
``schtasks`` is a system side effect and is intentionally not invoked here.
"""

from __future__ import annotations

from lmstudioclaw.automations import tasksched


def test_daily_trigger_maps_weekdays_and_time():
    """A daily schedule becomes a WEEKLY schtasks trigger on the right days/time."""
    args = tasksched._trigger_args({
        "schedule_type": "daily", "daily_days": [0, 2, 4], "daily_time": "09:05",
    })
    assert args == ["/SC", "WEEKLY", "/D", "MON,WED,FRI", "/ST", "09:05"]


def test_interval_units_map_to_schtasks_modes():
    """Each interval unit maps to the matching schtasks /SC mode and /MO multiplier."""
    assert tasksched._trigger_args(
        {"schedule_type": "interval", "interval_unit": "minutes", "interval_value": 30}
    ) == ["/SC", "MINUTE", "/MO", "30"]
    assert tasksched._trigger_args(
        {"schedule_type": "interval", "interval_unit": "hours", "interval_value": 2}
    ) == ["/SC", "HOURLY", "/MO", "2"]
    assert tasksched._trigger_args(
        {"schedule_type": "interval", "interval_unit": "days", "interval_value": 1}
    ) == ["/SC", "DAILY", "/MO", "1"]


def test_malformed_schedules_return_none():
    """Missing/invalid schedule fields yield None so no task is registered."""
    assert tasksched._trigger_args({"schedule_type": "daily", "daily_days": []}) is None
    assert tasksched._trigger_args(
        {"schedule_type": "interval", "interval_unit": "minutes", "interval_value": 0}
    ) is None
    assert tasksched._trigger_args({"schedule_type": "bogus"}) is None


def test_task_name_is_namespaced():
    """Tasks live under an LMStudioClaw folder, keyed by automation id."""
    assert tasksched.task_name("abc123") == "LMStudioClaw\\auto_abc123"
