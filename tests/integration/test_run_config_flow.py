"""Integration test: per-run config is applied + persisted; globals stay unchanged (US4)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from lmstudioclaw.app import Controller
from lmstudioclaw.model.lifecycle import LoadedModel
from lmstudioclaw.orchestrator.engine import SessionResult
from lmstudioclaw.web.api import create_app


class _FakeLifecycle:
    async def detect_orphan(self):
        return None

    async def load(self, model_key, context_length=None):
        return LoadedModel(instance_id="i1", key=model_key or "fake", context_length=4096)

    async def unload(self, instance_id):
        return None

    async def unload_all(self):
        return None

    async def current(self):
        return None


@pytest.fixture()
def client(temp_app_paths, monkeypatch):
    controller = Controller()
    controller.lifecycle = _FakeLifecycle()

    async def fake_run_session(*, on_event, control, **kwargs):
        return SessionResult("completed")

    monkeypatch.setattr(controller.engine, "run_session", fake_run_session)
    app = create_app(controller)
    with TestClient(app) as c:
        c._controller = controller
        yield c


def test_session_run_config_persisted(client):
    """A session started with run_config stores that config on the session row."""
    rc = {"model": "fake", "tool_overrides": {"powershell": False}, "mcp_selection": []}
    sid = client.post("/api/sessions", json={"run_config": rc}).json()["session_id"]
    session = client._controller.store.get_session(sid)
    assert session is not None
    import json
    stored = json.loads(session["run_config"])
    assert stored["model"] == "fake"
    assert stored["tool_overrides"] == {"powershell": False}


def test_globals_unchanged_after_override(client):
    """A per-run tool override does not mutate the global toolset (FR-028)."""
    before = {t.name for t in client._controller.registry.enabled_tools()}
    rc = {"tool_overrides": {"powershell": False, "grep": False}}
    client.post("/api/sessions", json={"run_config": rc})
    after = {t.name for t in client._controller.registry.enabled_tools()}
    assert before == after
    assert "powershell" in after and "grep" in after


def test_automation_run_config_roundtrip(client):
    """An automation persists and returns its run_config (FR-027)."""
    body = {
        "name": "Nightly", "task": "do it", "schedule_type": "interval",
        "interval_unit": "hours", "interval_value": 6,
        "run_config": {"model": "fast", "tool_overrides": {"edit": False}, "mcp_selection": None},
    }
    aid = client.post("/api/automations", json=body).json()["id"]
    listed = client.get("/api/automations").json()
    automation = next(a for a in listed if a["id"] == aid)
    assert automation["run_config"]["model"] == "fast"
    assert automation["run_config"]["tool_overrides"] == {"edit": False}
