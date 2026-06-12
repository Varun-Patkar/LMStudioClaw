"""Integration test for session history recording (SC-011).

Verifies two runs are recorded with the correct trigger, model, status, and
transcript turns, and that list/detail retrieval returns the expected metadata.
"""

from __future__ import annotations

from lmstudioclaw.sessions.store import Store


def test_two_runs_recorded_with_metadata(temp_app_paths):
    store = Store(temp_app_paths.db_path)

    # Run 1: a manual session that completes.
    s1 = store.create_session(trigger_type="manual", model_key="model-a", context_length=4096)
    store.update_session(s1, status="loading")
    store.update_session(s1, status="active")
    store.add_turn(s1, role="user", content="hello")
    store.add_turn(s1, role="assistant", content="hi there")
    store.update_session(s1, status="completed")

    # Run 2: an automation-triggered session that fails.
    aid = store.create_automation({
        "name": "nightly", "task": "do things", "schedule_type": "interval",
        "interval_unit": "hours", "interval_value": 6,
    })
    s2 = store.create_session(trigger_type="automation", automation_id=aid, model_key="model-b")
    store.update_session(s2, status="loading")
    store.update_session(s2, status="failed", failure_reason="boom", failure_point="model_load")

    sessions = store.list_sessions()
    assert len(sessions) == 2

    d1 = store.get_session(s1)
    assert d1["trigger_type"] == "manual"
    assert d1["model_key"] == "model-a"
    assert d1["status"] == "completed"
    assert d1["started_at"] is not None and d1["ended_at"] is not None
    turns = store.list_turns(s1)
    assert [t["role"] for t in turns] == ["user", "assistant"]
    assert turns[0]["content"] == "hello"

    d2 = store.get_session(s2)
    assert d2["trigger_type"] == "automation"
    assert d2["automation_id"] == aid
    assert d2["model_key"] == "model-b"
    assert d2["status"] == "failed"
    assert d2["failure_reason"] == "boom"
    assert d2["failure_point"] == "model_load"
