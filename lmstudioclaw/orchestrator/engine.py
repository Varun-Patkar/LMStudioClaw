"""Interactive agent turn loop.

Drives a multi-turn conversation against LM Studio's OpenAI-compatible ``/v1``
endpoint using the ``openai`` async client, with streaming output and tool calling.
The loop supports live **steering** (inject into the current turn), **queuing** (a
message for the next turn), and **stop** (abort the current turn or end the session)
per FR-056–FR-060. Before each turn it checks the token budget and compacts at the
threshold (FR-061). Any file tool routes through the consent gate (FR-016).

Events are emitted through an async ``on_event`` callback so the WebSocket layer can
forward them to the browser; this module knows nothing about HTTP.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from openai import AsyncOpenAI

from ..capabilities.registry import CapabilityRegistry
from ..consent.path_gate import Access
from . import budget as budget_mod
from . import compaction as compaction_mod

EventFn = Callable[[dict], Awaitable[None]]

# How long to wait for the next user message before treating the session idle.
DEFAULT_IDLE_TIMEOUT = 600
# Maximum tool-call iterations within a single user turn (prevents runaway loops).
_MAX_TOOL_ITERS = 12


@dataclass
class SessionControl:
    """Shared control surface between the WebSocket handler and the engine.

    The WS handler pushes user input and control signals here; the engine consumes
    them. Consent decisions are delivered by resolving futures keyed by request id.
    """

    inbox: asyncio.Queue = field(default_factory=asyncio.Queue)
    steer_buffer: list[str] = field(default_factory=list)
    stop_turn: asyncio.Event = field(default_factory=asyncio.Event)
    stop_session: asyncio.Event = field(default_factory=asyncio.Event)
    _consent_waiters: dict[str, asyncio.Future] = field(default_factory=dict)

    def message(self, text: str) -> None:
        """Enqueue a normal next message (when idle)."""
        self.inbox.put_nowait(("message", text))

    def queue(self, text: str) -> None:
        """Enqueue a message to be processed after the current turn (FR-058)."""
        self.inbox.put_nowait(("message", text))

    def steer(self, text: str) -> None:
        """Inject steering text into the in-progress turn (FR-057)."""
        self.steer_buffer.append(text)

    def stop(self, scope: str = "turn") -> None:
        """Stop the current turn, or end the whole session (FR-059)."""
        if scope == "session":
            self.stop_session.set()
        self.stop_turn.set()

    def resolve_consent(self, request_id: str, granted: bool) -> None:
        """Resolve a pending consent request with the user's decision."""
        fut = self._consent_waiters.pop(request_id, None)
        if fut and not fut.done():
            fut.set_result(granted)


@dataclass
class SessionResult:
    """Terminal outcome of a session run."""

    status: str  # completed | failed | stopped
    failure_reason: str | None = None
    failure_point: str | None = None


class Engine:
    """Runs interactive sessions against the active LM Studio model."""

    def __init__(self, store, registry: CapabilityRegistry, openai_base: str, api_key: str,
                 client: AsyncOpenAI | None = None) -> None:
        """Wire the engine to the store, capability registry, and LM Studio /v1.

        ``client`` may be injected for testing; otherwise a real async client is built.
        """
        self._store = store
        self._registry = registry
        self._client = client or AsyncOpenAI(base_url=openai_base, api_key=api_key, timeout=600)

    async def run_session(
        self,
        *,
        session_id: str,
        model_id: str,
        system_prompt: str,
        context_length: int,
        control: SessionControl,
        on_event: EventFn,
        threshold: float = 0.90,
        idle_timeout: int = DEFAULT_IDLE_TIMEOUT,
        max_run_duration: int = 3600,
        unattended: bool = False,
        initial_message: str | None = None,
        history: list[dict] | None = None,
        run_config=None,
    ) -> SessionResult:
        """Run the interactive loop until the session ends, stops, or fails.

        ``initial_message`` lets automations supply the task prompt up front so an
        unattended run proceeds without waiting on the inbox. ``max_run_duration``
        is a hard wall-clock cap after which the session is force-ended (FR-062).
        ``history`` seeds prior conversation for persistent-session automations
        (FR-064); it is compacted if it would exceed the budget.
        """
        import time

        messages: list[dict] = [{"role": "system", "content": system_prompt}]
        if history:
            messages.extend(history)
        budget = budget_mod.allocate(context_length, threshold=threshold)
        deadline = time.monotonic() + max_run_duration if max_run_duration > 0 else None

        await on_event({"type": "status", "status": "active"})

        if run_config is not None:
            _tools, warnings = self._registry.effective_tools(run_config)
            for warning in warnings:
                await on_event({"type": "warning", "message": warning})

        if initial_message:
            control.message(initial_message)

        try:
            while not control.stop_session.is_set():
                if deadline is not None and time.monotonic() >= deadline:
                    await on_event({"type": "error", "reason": "Max run duration reached",
                                    "point": "max_run_duration"})
                    return SessionResult("stopped", failure_point="max_run_duration")

                user_text = await self._next_message(control, idle_timeout, unattended)
                if user_text is None:
                    break  # idle timeout or no more input -> end session

                messages.append({"role": "user", "content": user_text})
                self._store.add_turn(session_id, role="user", content=user_text,
                                     token_estimate=budget_mod.estimate_tokens(user_text))

                messages, budget = await self._maybe_compact(
                    session_id, messages, model_id, budget, on_event
                )

                control.stop_turn.clear()
                await self._run_turn(session_id, messages, model_id, budget, control, on_event,
                                     unattended, run_config)

                if control.stop_session.is_set():
                    return SessionResult("stopped")

            return SessionResult("completed")
        except Exception as exc:  # pragma: no cover - runtime/network dependent
            await on_event({"type": "error", "reason": str(exc), "point": "run_session"})
            return SessionResult("failed", failure_reason=str(exc), failure_point="run_session")

    # -- internals ----------------------------------------------------------

    async def _next_message(
        self, control: SessionControl, idle_timeout: int, unattended: bool
    ) -> str | None:
        """Wait for the next user message; return None on idle timeout."""
        try:
            kind, text = await asyncio.wait_for(control.inbox.get(), timeout=idle_timeout)
            return text
        except asyncio.TimeoutError:
            # Unattended runs end when their queued work is done; interactive runs
            # end on idle (FR-062 / idle unload).
            return None

    async def _maybe_compact(self, session_id, messages, model_id, budget, on_event):
        """Compact older turns when usage crosses the threshold (FR-061)."""
        budget.used = budget_mod.estimate_messages(messages)
        await on_event({"type": "budget", "used": budget.used, "total": budget.total,
                        "threshold": budget.threshold})
        if not budget_mod.should_compact(budget):
            return messages, budget
        result = await compaction_mod.compact(messages, model=model_id, client=self._client)
        if result.tokens_after < result.tokens_before:
            summary_turn = self._store.add_turn(
                session_id, role="system", content=result.summary,
                token_estimate=result.tokens_after,
            )
            self._store.add_compression_event(
                session_id, result.tokens_before, result.tokens_after, summary_turn
            )
            await on_event({"type": "compaction", "tokens_before": result.tokens_before,
                            "tokens_after": result.tokens_after})
            budget.used = result.tokens_after
            return result.messages, budget
        # Edge case: compaction could not reduce the context. Warn but proceed; if the
        # turn then overflows the model, the error is captured as a failed run.
        await on_event({"type": "error", "reason": "Context compaction could not reduce size",
                        "point": "compaction"})
        return messages, budget

    async def _run_turn(self, session_id, messages, model_id, budget, control, on_event,
                        unattended, run_config=None):
        """Run one assistant turn, resolving tool calls until a final answer."""
        if run_config is not None:
            specs, _warnings = self._registry.effective_tools(run_config)
        else:
            specs = self._registry.enabled_tools()
        tools = [t.to_openai() for t in specs]
        for _ in range(_MAX_TOOL_ITERS):
            if control.stop_turn.is_set():
                return
            assistant_text, tool_calls = await self._stream_completion(
                messages, model_id, tools, control, on_event
            )

            # Fold any steering text injected mid-turn into the conversation.
            if control.steer_buffer:
                steer = "\n".join(control.steer_buffer)
                control.steer_buffer.clear()
                messages.append({"role": "user", "content": f"[steering] {steer}"})
                self._store.add_turn(session_id, role="steer", content=steer)

            if not tool_calls:
                if assistant_text:
                    messages.append({"role": "assistant", "content": assistant_text})
                    self._store.add_turn(session_id, role="assistant", content=assistant_text,
                                         token_estimate=budget_mod.estimate_tokens(assistant_text))
                return

            # Record the assistant's tool-call turn, then execute each call.
            messages.append({"role": "assistant", "content": assistant_text or None,
                             "tool_calls": tool_calls})
            for call in tool_calls:
                await self._execute_tool_call(session_id, call, messages, control, on_event,
                                              unattended)

    async def _stream_completion(self, messages, model_id, tools, control, on_event):
        """Stream a single completion, emitting tokens; collect any tool calls."""
        kwargs: dict[str, Any] = {"model": model_id, "messages": messages, "stream": True}
        if tools:
            kwargs["tools"] = tools
        assistant_text = ""
        # Accumulate tool calls by index across streamed deltas.
        partial: dict[int, dict] = {}
        stream = await self._client.chat.completions.create(**kwargs)
        async for chunk in stream:
            if control.stop_turn.is_set():
                break
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta is None:
                continue
            if delta.content:
                assistant_text += delta.content
                await on_event({"type": "token", "text": delta.content})
            for tc in (delta.tool_calls or []):
                slot = partial.setdefault(tc.index, {"id": "", "name": "", "args": ""})
                if tc.id:
                    slot["id"] = tc.id
                if tc.function and tc.function.name:
                    slot["name"] = tc.function.name
                if tc.function and tc.function.arguments:
                    slot["args"] += tc.function.arguments
        tool_calls = [
            {"id": s["id"], "type": "function",
             "function": {"name": s["name"], "arguments": s["args"]}}
            for s in partial.values() if s["name"]
        ]
        return assistant_text, tool_calls

    async def _execute_tool_call(self, session_id, call, messages, control, on_event, unattended):
        """Execute one tool call through the registry and append its result."""
        import json

        name = call["function"]["name"]
        raw_args = call["function"].get("arguments") or "{}"
        try:
            args = json.loads(raw_args) if raw_args.strip() else {}
        except json.JSONDecodeError:
            args = {}

        await on_event({"type": "tool_call", "name": name, "args": args})
        self._store.add_turn(session_id, role="assistant", tool_call={"name": name, "args": args})

        consent_fn = self._make_consent_fn(session_id, control, on_event, unattended)
        result = await self._registry.invoke_tool(name, args, consent=consent_fn)

        await on_event({"type": "tool_result", "name": name, "ok": result.ok,
                        "summary": (result.output[:200] if result.ok else result.error)})
        self._store.add_turn(session_id, role="tool",
                             tool_result={"ok": result.ok,
                                          "output": result.output, "error": result.error})
        messages.append({
            "role": "tool", "tool_call_id": call["id"],
            "content": result.output if result.ok else f"ERROR: {result.error}",
        })

    def _make_consent_fn(self, session_id, control: SessionControl, on_event, unattended):
        """Build the async consent callback the registry uses for file access."""
        async def consent(path: str, access: Access) -> bool:
            # Unattended automations never prompt — they were denied upstream by the
            # gate's fail-fast, so this path is only reached for interactive runs.
            if unattended:
                return False
            import uuid
            request_id = str(uuid.uuid4())
            loop = asyncio.get_running_loop()
            fut: asyncio.Future = loop.create_future()
            control._consent_waiters[request_id] = fut
            await on_event({"type": "consent_request", "request_id": request_id,
                            "path": path, "access": access.value})
            try:
                return await asyncio.wait_for(fut, timeout=300)
            except asyncio.TimeoutError:
                control._consent_waiters.pop(request_id, None)
                return False
        return consent

    async def aclose(self) -> None:
        """Close the underlying OpenAI client."""
        await self._client.close()
