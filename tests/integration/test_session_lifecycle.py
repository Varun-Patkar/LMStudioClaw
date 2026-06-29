"""Integration test for the US1 session lifecycle (SC-001, SC-002, SC-003).

Drives a real orchestrator engine + capability registry + consent gate + store with a
**scripted fake** OpenAI async client (no LM Studio). Verifies: the agent streams
output, writes a file into the workspace through the consent-gated tool, the session
reaches ``completed``, and the model is unloaded on end.
"""

from __future__ import annotations

import asyncio

import pytest

from lmstudioclaw.app import Controller
from lmstudioclaw.model.lifecycle import LoadedModel


# --- scripted fake OpenAI client -------------------------------------------

class _Func:
    def __init__(self, name=None, arguments=None):
        self.name = name
        self.arguments = arguments


class _ToolCall:
    def __init__(self, index, id, name, arguments):
        self.index = index
        self.id = id
        self.function = _Func(name, arguments)


class _Delta:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _Choice:
    def __init__(self, delta):
        self.delta = delta


class _Chunk:
    def __init__(self, delta):
        self.choices = [_Choice(delta)]


class _Stream:
    """Async iterator over a fixed list of chunks."""

    def __init__(self, chunks):
        self._chunks = chunks

    def __aiter__(self):
        self._it = iter(self._chunks)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


class _Completions:
    def __init__(self, parent):
        self._parent = parent

    async def create(self, **kwargs):
        self._parent.calls += 1
        if self._parent.calls == 1:
            # First turn: ask to write a file into the workspace via a tool call.
            return _Stream([
                _Chunk(_Delta(tool_calls=[_ToolCall(
                    0, "call-1", "write_file",
                    '{"path": "%s", "content": "summary"}' % self._parent.target_path,
                )])),
            ])
        # Second turn: final answer, no tool calls.
        return _Stream([_Chunk(_Delta(content="Done."))])


class _Chat:
    def __init__(self, parent):
        self.completions = _Completions(parent)


class FakeOpenAI:
    """Minimal stand-in for AsyncOpenAI used by the engine."""

    def __init__(self, target_path: str):
        self.calls = 0
        self.target_path = target_path
        self.chat = _Chat(self)

    async def close(self):
        return None


# --- fake lifecycle ---------------------------------------------------------

class _FakeLifecycle:
    def __init__(self):
        self.loaded = False
        self.unloaded = False

    async def detect_orphan(self):
        return None

    async def load(self, model_key, context_length=None):
        self.loaded = True
        return LoadedModel(instance_id="inst-1", key=model_key or "fake", context_length=4096)

    async def unload(self, instance_id):
        self.unloaded = True

    async def unload_all(self):
        self.unloaded = True

    async def current(self):
        return None


@pytest.mark.asyncio
async def test_session_lifecycle_writes_file_and_unloads(temp_app_paths):
    controller = Controller()
    controller.settings.session_idle_timeout = 1  # end quickly after the turn
    controller.settings.max_run_duration = 30

    fake_lifecycle = _FakeLifecycle()
    controller.lifecycle = fake_lifecycle

    target = temp_app_paths.workspace / "summary.md"
    # Inject the scripted fake client into the real engine.
    from lmstudioclaw.orchestrator.engine import Engine

    controller.engine = Engine(
        controller.store, controller.registry, "http://x/v1", "k",
        client=FakeOpenAI(str(target).replace("\\", "/")),
    )

    await controller.startup()
    try:
        session_id, _pos = controller.start_manual_session(model="fake")
        # Send the first user message (interactive start).
        controller.hub.register(session_id).message("Please write summary.md")

        # Wait for the session to reach a terminal status.
        for _ in range(100):
            session = controller.store.get_session(session_id)
            if session and session["status"] in ("completed", "failed", "stopped"):
                break
            await asyncio.sleep(0.05)

        session = controller.store.get_session(session_id)
        assert session["status"] == "completed", session.get("failure_reason")
        assert target.exists() and target.read_text() == "summary"
        assert fake_lifecycle.loaded is True
        assert fake_lifecycle.unloaded is True

        # A detailed JSON log was written with the full system prompt + ordered events.
        import json

        log_path = controller.paths.logs_dir / f"{session_id}.json"
        assert log_path.exists()
        doc = json.loads(log_path.read_text(encoding="utf-8"))
        assert doc["status"] == "completed"
        types = [e["type"] for e in doc["events"]]
        assert "session_start" in types and "model_load" in types
        assert "api_request" in types and "model_unload" in types and "session_end" in types
        start = next(e for e in doc["events"] if e["type"] == "session_start")
        assert isinstance(start["system_prompt"], str) and start["system_prompt"]
    finally:
        await controller.shutdown()
