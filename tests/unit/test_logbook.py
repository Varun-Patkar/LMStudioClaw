"""Unit tests for the detailed session log book (JSON logger + manifest + viewer asset)."""

from __future__ import annotations

import json

from lmstudioclaw.sessions import logbook


def test_logger_writes_ordered_events(temp_app_paths):
    logs = temp_app_paths.logs_dir
    log = logbook.SessionLogger(logs, "sess-1", meta={"trigger_type": "manual", "model_key": "m"})
    log.event("session_start", system_prompt="You are helpful.")
    log.event("api_request", model="m", messages=[{"role": "system", "content": "You are helpful."}])
    log.event("tool_call", name="read_file", args={"path": "x"})
    log.finalize("completed")

    doc = json.loads((logs / "sess-1.json").read_text(encoding="utf-8"))
    assert doc["session_id"] == "sess-1" and doc["status"] == "completed"
    seqs = [e["seq"] for e in doc["events"]]
    assert seqs == sorted(seqs)  # strictly ordered
    types = [e["type"] for e in doc["events"]]
    assert types == ["session_start", "api_request", "tool_call", "session_end"]


def test_list_and_read_logs(temp_app_paths):
    logs = temp_app_paths.logs_dir
    logbook.SessionLogger(logs, "a", meta={"model_key": "m"}).finalize("completed")
    logbook.SessionLogger(logs, "b").finalize("failed")

    manifest = logbook.list_logs(logs)
    ids = {m["session_id"] for m in manifest}
    assert ids == {"a", "b"}
    assert logbook.read_log(logs, "a")["status"] == "completed"
    assert logbook.read_log(logs, "missing") is None


def test_ensure_assets_writes_viewer(temp_app_paths):
    logbook.ensure_logs_assets(temp_app_paths.logs_dir)
    index = temp_app_paths.logs_dir / "index.html"
    assert index.exists()
    assert "LMStudioClaw Logs" in index.read_text(encoding="utf-8")
