"""Smoke test: the controller boots, serves the control panel, and stays idle.

Approximates quickstart flow #1 (SC-001 idle = no model) without a live LM Studio:
the app starts with no model load attempted by the web layer, the health endpoint
responds, and the SPA index is served.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from lmstudioclaw.app import Controller
from lmstudioclaw.web.api import create_app


class _FakeLifecycle:
    """No-op lifecycle so boot never touches LM Studio."""

    async def detect_orphan(self):
        return None

    async def unload_all(self):
        return None


def test_controller_boots_and_serves(temp_app_paths):
    controller = Controller()
    controller.lifecycle = _FakeLifecycle()
    app = create_app(controller)
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["ok"] is True

        # The SPA index is served at the root.
        index = client.get("/")
        assert index.status_code == 200
        assert "LM Studio Manager" in index.text

        # No session is active at idle (SC-001 analogue: nothing running).
        assert client.get("/api/queue").json() == []
        assert controller.store.active_or_loading() is None
