"""Single-active-session FIFO queue.

Guarantees that **exactly one** session is loading/active at any time (FR-008) so two
models are never loaded at once. Manual sessions and fired automations both enqueue
here; items run strictly in arrival order. Queued (not-yet-started) items can be
cancelled.

The queue is **persisted** through the store (FR-025a): each enqueue writes a
``queued_runs`` row, dequeue marks it started, and completion/cancellation removes it.
On startup the controller calls :meth:`restore_from_store` to re-enqueue any runs that
were still pending when the app last stopped, so no queued work is silently lost.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

# A runner is an async callable that executes the session and returns when it ends.
Runner = Callable[[], Awaitable[None]]


@dataclass
class QueueItem:
    """A queued session execution."""

    session_id: str
    runner: Runner
    started: bool = False
    cancelled: bool = False


@dataclass
class SessionQueue:
    """An asyncio FIFO that runs one session at a time (optionally persisted)."""

    store: object | None = None
    _items: deque[QueueItem] = field(default_factory=deque)
    _wakeup: asyncio.Event = field(default_factory=asyncio.Event)
    _active: QueueItem | None = None
    _stopped: bool = False

    def enqueue(self, session_id: str, runner: Runner, *, persist: dict | None = None) -> int:
        """Add a session to the queue; return its 1-based position (0 = runs next/now).

        When ``persist`` is provided and a store is attached, the run is recorded in
        ``queued_runs`` so it survives a restart (FR-025a). ``persist`` carries the
        run metadata (``trigger_type``, ``automation_id``, ``run_config``,
        ``initial_message``).
        """
        item = QueueItem(session_id=session_id, runner=runner)
        self._items.append(item)
        if self.store is not None and persist is not None:
            self.store.enqueue_run(run_id=session_id, **persist)
        self._wakeup.set()
        # Position relative to the currently active item.
        return len(self._items) - 1

    def cancel(self, session_id: str) -> bool:
        """Cancel a queued item that has not started yet. Returns success."""
        for item in self._items:
            if item.session_id == session_id and not item.started:
                item.cancelled = True
                if self.store is not None:
                    self.store.remove_queued_run(session_id)
                return True
        return False

    def restore_from_store(self, runner_factory: Callable[[dict], Runner | None]) -> int:
        """Re-enqueue persisted, not-yet-started runs on startup (FR-025a).

        ``runner_factory`` rebuilds a runner from a persisted ``queued_runs`` row
        (the controller supplies it, since runner construction needs model/persona
        context). Rows for which a runner cannot be rebuilt are dropped. Returns the
        number of runs restored. Rows are NOT re-persisted (they already exist).
        """
        if self.store is None:
            return 0
        restored = 0
        for row in self.store.list_queued_runs(pending_only=True):
            runner = runner_factory(row)
            if runner is None:
                self.store.remove_queued_run(row["id"])
                continue
            self._items.append(QueueItem(session_id=row["id"], runner=runner))
            restored += 1
        if restored:
            self._wakeup.set()
        return restored

    def snapshot(self) -> list[dict]:
        """Return the current queue contents (for the ``/api/queue`` view)."""
        out = []
        if self._active is not None:
            out.append({"session_id": self._active.session_id, "state": "active"})
        for item in self._items:
            if not item.cancelled:
                out.append({"session_id": item.session_id, "state": "queued"})
        return out

    @property
    def active_session_id(self) -> str | None:
        """The id of the currently running session, if any."""
        return self._active.session_id if self._active else None

    def stop(self) -> None:
        """Signal the run loop to exit after the current item (graceful shutdown)."""
        self._stopped = True
        self._wakeup.set()

    async def run_loop(self) -> None:
        """Dequeue and run items one at a time until stopped (FR-008)."""
        while not self._stopped:
            if not self._items:
                self._wakeup.clear()
                await self._wakeup.wait()
                continue
            item = self._items.popleft()
            if item.cancelled:
                if self.store is not None:
                    self.store.remove_queued_run(item.session_id)
                continue
            item.started = True
            self._active = item
            if self.store is not None:
                self.store.mark_run_started(item.session_id)
            try:
                await item.runner()
            except Exception:  # pragma: no cover - runner records its own failure
                pass
            finally:
                self._active = None
                if self.store is not None:
                    self.store.remove_queued_run(item.session_id)
