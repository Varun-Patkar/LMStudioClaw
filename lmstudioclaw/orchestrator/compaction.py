"""Automatic context compaction (summarize-and-replace).

When a session's token usage crosses the budget threshold (~0.90), older
conversation turns are summarized into a single replacement turn so the session can
continue without overflowing the model's context window (FR-061). A
``CompressionEvent`` is recorded for visibility (SC-014).
"""

from __future__ import annotations

from dataclasses import dataclass

from openai import AsyncOpenAI

from .budget import estimate_messages

# Keep the most recent N turns verbatim; summarize everything older.
_KEEP_RECENT = 4

_SUMMARY_SYSTEM = (
    "You are compressing a conversation to save context space. Produce a concise but "
    "faithful summary of the earlier messages: capture decisions made, facts learned, "
    "files touched, and any pending intentions. Do not include secret values. Output "
    "only the summary."
)


@dataclass
class CompactionResult:
    """Outcome of a compaction pass."""

    messages: list[dict]   # the new, compacted message list
    summary: str           # the generated summary text
    tokens_before: int
    tokens_after: int


async def compact(
    messages: list[dict],
    *,
    model: str,
    client: AsyncOpenAI,
) -> CompactionResult:
    """Summarize older turns and return a compacted message list.

    The system message (index 0, if a system role) and the most recent
    ``_KEEP_RECENT`` turns are preserved verbatim; everything in between is replaced
    by a single summary message. If there is nothing meaningful to compact, the
    original messages are returned unchanged.
    """
    tokens_before = estimate_messages(messages)

    # Identify a leading system message to preserve.
    head: list[dict] = []
    body = messages
    if messages and messages[0].get("role") == "system":
        head = [messages[0]]
        body = messages[1:]

    if len(body) <= _KEEP_RECENT + 1:
        # Too little to gain from compaction.
        return CompactionResult(messages, "", tokens_before, tokens_before)

    cut = _safe_cut(body, _KEEP_RECENT)
    if cut <= 0:
        # Everything recent is one indivisible tool exchange — nothing safe to drop.
        return CompactionResult(messages, "", tokens_before, tokens_before)
    to_summarize = body[:cut]
    recent = body[cut:]

    transcript = "\n".join(
        f"{m.get('role', '?')}: {_msg_text(m)}" for m in to_summarize
    )
    summary = await _summarize(transcript, model=model, client=client)

    summary_msg = {"role": "system", "content": f"[Earlier conversation summary]\n{summary}"}
    new_messages = [*head, summary_msg, *recent]
    tokens_after = estimate_messages(new_messages)
    return CompactionResult(new_messages, summary, tokens_before, tokens_after)


def _msg_text(msg: dict) -> str:
    """Best-effort readable text for a message (incl. tool-call names) for summaries."""
    content = msg.get("content") or ""
    if isinstance(content, list):
        content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
    calls = msg.get("tool_calls") or []
    if calls:
        names = ", ".join(c.get("function", {}).get("name", "?") for c in calls if isinstance(c, dict))
        content = f"{content} [called: {names}]".strip()
    return str(content)


def _safe_cut(body: list[dict], keep_recent: int) -> int:
    """Return an index splitting ``body`` so the kept tail never orphans a tool call.

    The OpenAI chat API requires every assistant message that has ``tool_calls`` to be
    immediately followed by the ``tool`` results answering each call. A naive
    ``body[-keep_recent:]`` slice can start on an orphaned ``tool`` message (its
    assistant request summarized away) or end the summarized half on a dangling
    assistant ``tool_calls`` (its results kept). We move the boundary **earlier** until
    the kept tail starts cleanly (not on a ``tool`` message) so the pairing stays intact.
    """
    cut = max(0, len(body) - keep_recent)
    # Walk the boundary back while the first kept message is a tool result (its
    # assistant tool_calls would otherwise be on the summarized side).
    while cut > 0 and body[cut].get("role") == "tool":
        cut -= 1
    return cut


async def _summarize(transcript: str, *, model: str, client: AsyncOpenAI) -> str:
    """Ask the active model to summarize a transcript chunk."""
    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SUMMARY_SYSTEM},
                {"role": "user", "content": transcript},
            ],
            max_tokens=512,
            temperature=0.2,
        )
        return resp.choices[0].message.content or ""
    except Exception:  # pragma: no cover - network/runtime dependent
        # If summarization fails we cannot reduce; the caller decides how to proceed.
        return ""
