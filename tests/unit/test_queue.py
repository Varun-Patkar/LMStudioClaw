"""Unit tests for the single-active-session FIFO queue (FR-008)."""

from __future__ import annotations

import asyncio

import pytest

from lmstudioclaw.sessions.queue import SessionQueue


@pytest.mark.asyncio
async def test_single_active_invariant_and_order():
    q = SessionQueue()
    order: list[str] = []
    concurrent = {"max": 0, "now": 0}

    def make_runner(name: str):
        async def runner():
            concurrent["now"] += 1
            concurrent["max"] = max(concurrent["max"], concurrent["now"])
            await asyncio.sleep(0.01)
            order.append(name)
            concurrent["now"] -= 1
        return runner

    q.enqueue("a", make_runner("a"))
    q.enqueue("b", make_runner("b"))
    q.enqueue("c", make_runner("c"))

    loop_task = asyncio.create_task(q.run_loop())
    await asyncio.sleep(0.1)
    q.stop()
    await loop_task

    assert order == ["a", "b", "c"]          # FIFO order preserved
    assert concurrent["max"] == 1            # never two active at once


@pytest.mark.asyncio
async def test_cancel_before_start():
    q = SessionQueue()
    ran: list[str] = []

    def make_runner(name: str):
        async def runner():
            await asyncio.sleep(0.01)
            ran.append(name)
        return runner

    q.enqueue("a", make_runner("a"))
    q.enqueue("b", make_runner("b"))
    # Cancel b before the loop starts it.
    assert q.cancel("b") is True

    loop_task = asyncio.create_task(q.run_loop())
    await asyncio.sleep(0.08)
    q.stop()
    await loop_task

    assert "a" in ran
    assert "b" not in ran


@pytest.mark.asyncio
async def test_cannot_cancel_active(monkeypatch):
    q = SessionQueue()
    started = asyncio.Event()

    async def runner():
        started.set()
        await asyncio.sleep(0.05)

    q.enqueue("a", runner)
    loop_task = asyncio.create_task(q.run_loop())
    await started.wait()
    # Already active -> cannot cancel.
    assert q.cancel("a") is False
    q.stop()
    await loop_task


@pytest.mark.asyncio
async def test_persist_and_restore(temp_app_paths):
    """A persisted queue is restored across a fresh queue instance (FR-025a)."""
    from lmstudioclaw.sessions.store import Store

    store = Store(temp_app_paths.db_path)

    # Enqueue two runs with persistence, then drop the queue without running them.
    q1 = SessionQueue(store=store)

    async def _noop():
        return None

    q1.enqueue("r1", _noop, persist={"trigger_type": "manual", "run_config": {"model": "m"}})
    q1.enqueue("r2", _noop, persist={"trigger_type": "automation", "automation_id": "a1"})

    pending = store.list_queued_runs(pending_only=True)
    assert [r["id"] for r in pending] == ["r1", "r2"]
    assert pending[0]["run_config"] == {"model": "m"}

    # A new queue restores both pending runs in FIFO order.
    q2 = SessionQueue(store=store)
    restored_ids: list[str] = []

    def factory(row):
        restored_ids.append(row["id"])
        async def runner():
            return None
        return runner

    count = q2.restore_from_store(factory)
    assert count == 2
    assert restored_ids == ["r1", "r2"]
    store.close()


@pytest.mark.asyncio
async def test_completion_removes_persisted_row(temp_app_paths):
    """Running a persisted run to completion clears its queued_runs row."""
    from lmstudioclaw.sessions.store import Store

    store = Store(temp_app_paths.db_path)
    q = SessionQueue(store=store)

    async def runner():
        await asyncio.sleep(0.01)

    q.enqueue("done", runner, persist={"trigger_type": "manual"})
    loop_task = asyncio.create_task(q.run_loop())
    await asyncio.sleep(0.08)
    q.stop()
    await loop_task

    assert store.list_queued_runs() == []
    store.close()
