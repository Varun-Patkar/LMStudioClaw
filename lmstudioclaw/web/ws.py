"""WebSocket session hub and endpoint.

Bridges the orchestrator engine to the browser. For each session the hub holds a
:class:`SessionControl` (consumed by the engine) and the set of connected sockets.
Server→client events emitted by the engine are broadcast to all attached sockets;
client→server messages are translated into control signals (steer/queue/stop/message)
and consent decisions per ``contracts/http-api.md``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from fastapi import WebSocket, WebSocketDisconnect

from ..orchestrator.engine import SessionControl


@dataclass
class _SessionChannel:
    """Per-session control + connected sockets + a small replayable snapshot.

    ``last_budget`` and ``generating`` are cached so a socket that connects (or
    reconnects) mid-run immediately sees the token gauge and the correct
    working/idle state instead of a stale ``0/0`` until the next turn.
    """

    control: SessionControl
    sockets: set[WebSocket] = field(default_factory=set)
    last_budget: dict | None = None
    generating: bool = False


class SessionHub:
    """Tracks live session channels and fans events out to browser sockets."""

    def __init__(self) -> None:
        """Initialize an empty hub."""
        self._channels: dict[str, _SessionChannel] = {}

    def register(self, session_id: str) -> SessionControl:
        """Create (or return) the control surface for a session."""
        chan = self._channels.get(session_id)
        if chan is None:
            chan = _SessionChannel(control=SessionControl())
            self._channels[session_id] = chan
        return chan.control

    def control(self, session_id: str) -> SessionControl | None:
        """Return the control surface for a session, if registered."""
        chan = self._channels.get(session_id)
        return chan.control if chan else None

    def unregister(self, session_id: str) -> None:
        """Drop a session channel once the run is finished."""
        self._channels.pop(session_id, None)

    async def broadcast(self, session_id: str, event: dict) -> None:
        """Send an event to every socket attached to a session."""
        chan = self._channels.get(session_id)
        if not chan:
            return
        # Cache snapshot-worthy state so late joiners can be brought up to date.
        etype = event.get("type")
        if etype == "budget":
            chan.last_budget = event
        elif etype == "turn":
            chan.generating = event.get("state") == "start"
        elif etype == "status" and event.get("status") in ("completed", "failed", "stopped"):
            chan.generating = False
        dead: list[WebSocket] = []
        for ws in chan.sockets:
            try:
                await ws.send_json(event)
            except Exception:  # pragma: no cover - socket may close mid-send
                dead.append(ws)
        for ws in dead:
            chan.sockets.discard(ws)

    async def attach(self, session_id: str, websocket: WebSocket) -> None:
        """Accept and register a websocket for a session, replaying its snapshot."""
        await websocket.accept()
        chan = self._channels.get(session_id)
        if chan is None:
            chan = _SessionChannel(control=SessionControl())
            self._channels[session_id] = chan
        chan.sockets.add(websocket)
        # Bring the new socket up to date with cached run state (no polling).
        if chan.last_budget is not None:
            try:
                await websocket.send_json(chan.last_budget)
            except Exception:  # pragma: no cover - socket may close mid-send
                pass
        try:
            await websocket.send_json(
                {"type": "turn", "state": "start" if chan.generating else "end"})
        except Exception:  # pragma: no cover - socket may close mid-send
            pass

    def detach(self, session_id: str, websocket: WebSocket) -> None:
        """Remove a websocket from a session channel."""
        chan = self._channels.get(session_id)
        if chan:
            chan.sockets.discard(websocket)


async def session_ws_endpoint(websocket: WebSocket, session_id: str, hub: SessionHub) -> None:
    """Handle a session WebSocket: forward client signals to the engine (FR-056–FR-060)."""
    await hub.attach(session_id, websocket)
    control = hub.register(session_id)
    try:
        while True:
            msg = await websocket.receive_json()
            kind = msg.get("type")
            if kind == "steer":
                control.steer(msg.get("text", ""))
            elif kind == "queue":
                control.queue(msg.get("text", ""))
            elif kind == "message":
                control.message(msg.get("text", ""))
            elif kind == "stop":
                control.stop(msg.get("scope", "turn"))
            elif kind == "consent":
                control.resolve_consent(msg.get("request_id", ""), bool(msg.get("granted")))
    except WebSocketDisconnect:
        hub.detach(session_id, websocket)
    except Exception:  # pragma: no cover - defensive
        hub.detach(session_id, websocket)


class StatusHub:
    """App-wide live-status channel (``/ws/status``) for the SPA shell.

    Broadcasts ``model_status`` / ``run_status`` / ``queue`` events so the top-right
    run indicator, the collapsible queue panel, and the "Load model" feedback update
    live without polling (FR-005/FR-024). On connect it replays a full snapshot so the
    UI recovers current state after a dropped channel (FR-007). It is push-only — client
    messages are ignored (the channel exists purely to receive status).
    """

    def __init__(self) -> None:
        """Initialize with no sockets and no snapshot provider yet."""
        self._sockets: set[WebSocket] = set()
        # Set by the controller: returns the current set of status events to replay.
        self.snapshot_provider = None

    async def attach(self, websocket: WebSocket) -> None:
        """Accept a socket and immediately replay the current status snapshot."""
        await websocket.accept()
        self._sockets.add(websocket)
        if self.snapshot_provider is not None:
            try:
                for event in self.snapshot_provider():
                    await websocket.send_json(event)
            except Exception:  # pragma: no cover - defensive
                pass

    def detach(self, websocket: WebSocket) -> None:
        """Remove a socket from the status channel."""
        self._sockets.discard(websocket)

    async def broadcast(self, event: dict) -> None:
        """Send a status event to every connected socket (dropping dead ones)."""
        dead: list[WebSocket] = []
        for ws in self._sockets:
            try:
                await ws.send_json(event)
            except Exception:  # pragma: no cover - socket may close mid-send
                dead.append(ws)
        for ws in dead:
            self._sockets.discard(ws)


async def status_ws_endpoint(websocket: WebSocket, hub: StatusHub) -> None:
    """Handle the global status WebSocket: push-only, ignores client messages."""
    await hub.attach(websocket)
    try:
        while True:
            await websocket.receive_text()  # keepalive; inbound is ignored
    except WebSocketDisconnect:
        hub.detach(websocket)
    except Exception:  # pragma: no cover - defensive
        hub.detach(websocket)
