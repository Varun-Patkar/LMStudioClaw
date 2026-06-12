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

    to_summarize = body[:-_KEEP_RECENT]
    recent = body[-_KEEP_RECENT:]

    transcript = "\n".join(
        f"{m.get('role', '?')}: {m.get('content', '')}" for m in to_summarize
    )
    summary = await _summarize(transcript, model=model, client=client)

    summary_msg = {"role": "system", "content": f"[Earlier conversation summary]\n{summary}"}
    new_messages = [*head, summary_msg, *recent]
    tokens_after = estimate_messages(new_messages)
    return CompactionResult(new_messages, summary, tokens_before, tokens_after)


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
