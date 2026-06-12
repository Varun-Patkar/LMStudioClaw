"""Unit tests for token budget allocation and threshold detection (SC-018)."""

from __future__ import annotations

from lmstudioclaw.orchestrator.budget import (
    Budget,
    allocate,
    estimate_messages,
    estimate_tokens,
    should_compact,
)


def test_estimate_tokens_nonzero_for_text():
    assert estimate_tokens("hello world this is a test") > 0
    assert estimate_tokens("") == 0


def test_estimate_messages_sums_content():
    msgs = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "a longer reply with more words here"},
    ]
    assert estimate_messages(msgs) >= estimate_tokens("hello")


def test_allocation_does_not_exceed_total():
    budget = allocate(10000)
    assert sum(budget.alloc.values()) <= budget.total
    assert set(budget.alloc) == {"persona", "skills", "memory", "conversation", "tool_output"}


def test_threshold_detection():
    budget = Budget(total=1000, threshold=0.9)
    budget.used = 800
    assert not should_compact(budget)
    budget.used = 900
    assert should_compact(budget)
    budget.used = 950
    assert should_compact(budget)


def test_limit_and_fraction():
    budget = allocate(2000, threshold=0.8)
    assert budget.limit == 1600
    budget.used = 1000
    assert abs(budget.fraction_used - 0.5) < 1e-6
