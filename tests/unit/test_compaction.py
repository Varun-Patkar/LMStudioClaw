"""Unit tests for compaction boundary safety (no orphaned tool_call/result pairs)."""

from __future__ import annotations

from lmstudioclaw.orchestrator.compaction import _safe_cut


def test_safe_cut_does_not_start_tail_on_tool_message():
    """The kept tail must not begin with a ``tool`` message (its assistant request
    would be summarized away, breaking the API's tool_call→tool pairing)."""
    body = [
        {"role": "user", "content": "do it"},
        {"role": "assistant", "content": "first"},
        {"role": "user", "content": "more"},
        # A tool exchange whose request would land just before the naive cut.
        {"role": "assistant", "tool_calls": [{"id": "1", "function": {"name": "f", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "1", "content": "big tool output"},
        {"role": "assistant", "content": "done"},
    ]
    cut = _safe_cut(body, keep_recent=4)
    # The tail must not start on a tool message.
    assert body[cut].get("role") != "tool"


def test_safe_cut_plain_conversation_is_naive():
    body = [{"role": "user", "content": str(i)} for i in range(10)]
    assert _safe_cut(body, keep_recent=4) == 6
