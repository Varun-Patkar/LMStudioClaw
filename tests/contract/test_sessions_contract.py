"""Contract tests for session REST + WebSocket events (SC-002, SC-013).

Uses FastAPI's TestClient with a Controller whose model lifecycle and engine are
faked so no real LM Studio is required. Verifies the response/event *shapes* and the
single-active-session queue contract.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from lmstudioclaw.app import Controller
from lmstudioclaw.model.lifecycle import LoadedModel
from lmstudioclaw.orchestrator.engine import SessionResult
from lmstudioclaw.web.api import create_app


class _FakeLifecycle:
    """Stand-in model lifecycle that never touches LM Studio."""

    async def detect_orphan(self):
        return None

    async def load(self, model_key, context_length=None):
        return LoadedModel(instance_id="inst-1", key=model_key or "fake", context_length=4096)

    async def unload(self, instance_id):
        return None

    async def unload_all(self):
        return None

    async def current(self):
        return None


@pytest.fixture()
def client(temp_app_paths, monkeypatch):
    """Build a TestClient over a Controller with faked lifecycle + engine."""
    controller = Controller()
    controller.lifecycle = _FakeLifecycle()

    async def fake_run_session(*, on_event, control, **kwargs):
        # Message-driven for deterministic timing: wait for a client message, then
        # emit a contract-shaped event sequence and complete. This guarantees a
        # connected WebSocket receives the events.
        import asyncio
        try:
            await asyncio.wait_for(control.inbox.get(), timeout=5)
        except asyncio.TimeoutError:
            return SessionResult("completed")
        await on_event({"type": "status", "status": "active"})
        await on_event({"type": "token", "text": "hello"})
        await on_event({"type": "budget", "used": 10, "total": 4096, "threshold": 0.9})
        return SessionResult("completed")

    monkeypatch.setattr(controller.engine, "run_session", fake_run_session)
    app = create_app(controller)
    with TestClient(app) as c:
        yield c


def test_start_session_shape(client):
    resp = client.post("/api/sessions", json={"model": "fake"})
    assert resp.status_code == 200
    body = resp.json()
    assert "session_id" in body and "queue_position" in body


def test_list_and_get_session(client):
    sid = client.post("/api/sessions", json={}).json()["session_id"]
    # Give the queue loop a moment to run the faked session to completion.
    for _ in range(50):
        detail = client.get(f"/api/sessions/{sid}").json()
        if detail["status"] in ("completed", "failed", "stopped"):
            break
    assert detail["id"] == sid
    assert "turns" in detail and "grants" in detail and "compression_events" in detail


def test_queue_endpoint_shape(client):
    client.post("/api/sessions", json={})
    queue = client.get("/api/queue").json()
    assert isinstance(queue, list)


def test_stop_unknown_session_404(client):
    resp = client.post("/api/sessions/does-not-exist/stop", json={"scope": "turn"})
    assert resp.status_code == 404


def test_websocket_receives_events(client):
    sid = client.post("/api/sessions", json={}).json()["session_id"]
    with client.websocket_connect(f"/ws/sessions/{sid}") as ws:
        # Send a message to drive the (faked) engine, then collect emitted events.
        ws.send_json({"type": "message", "text": "go"})
        seen = set()
        for _ in range(6):
            evt = ws.receive_json()
            seen.add(evt.get("type"))
            if {"status", "token"} <= seen:
                break
    assert "status" in seen and "token" in seen
