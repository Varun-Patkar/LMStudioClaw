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
