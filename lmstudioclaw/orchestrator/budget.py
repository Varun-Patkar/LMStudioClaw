"""Token estimation and context-budget allocation.

The agent's context window is divided across consumers — persona, skills, memory,
conversation, and tool output (FR-067/FR-068). Crossing the threshold (~0.90)
triggers compaction before overflow (FR-061).

Token counts are estimated with ``tiktoken`` when available, falling back to a
character-based heuristic so the runtime never hard-depends on a specific tokenizer.
"""

from __future__ import annotations

from dataclasses import dataclass, field

try:  # tiktoken is optional; degrade gracefully if unavailable.
    import tiktoken

    _ENCODER = tiktoken.get_encoding("cl100k_base")
except Exception:  # pragma: no cover - environment-dependent
    _ENCODER = None

# Heuristic: ~4 characters per token for English text.
_CHARS_PER_TOKEN = 4

# Default split of the context window across consumers (fractions summing to ~1.0).
_DEFAULT_SPLIT = {
    "persona": 0.10,
    "skills": 0.15,
    "memory": 0.10,
    "conversation": 0.50,
    "tool_output": 0.15,
}


def estimate_tokens(text: str) -> int:
    """Estimate the token count of a string (tiktoken or heuristic fallback)."""
    if not text:
        return 0
    if _ENCODER is not None:
        try:
            return len(_ENCODER.encode(text))
        except Exception:  # pragma: no cover - defensive
            pass
    return max(1, len(text) // _CHARS_PER_TOKEN)


def estimate_messages(messages: list[dict]) -> int:
    """Estimate tokens for a list of chat messages (content + small per-message overhead)."""
    total = 0
    for msg in messages:
        content = msg.get("content") or ""
        if isinstance(content, list):
            content = " ".join(part.get("text", "") for part in content if isinstance(part, dict))
        total += estimate_tokens(str(content)) + 4  # per-message framing overhead
    return total


@dataclass
class Budget:
    """A per-session allocation of the model's context window."""

    total: int
    threshold: float = 0.90
    used: int = 0
    alloc: dict[str, int] = field(default_factory=dict)

    @property
    def limit(self) -> int:
        """The token count at which compaction should trigger."""
        return int(self.total * self.threshold)

    @property
    def fraction_used(self) -> float:
        """Current usage as a fraction of the total window (0..>1)."""
        return self.used / self.total if self.total else 0.0


def allocate(total_context: int, threshold: float = 0.90,
             split: dict[str, float] | None = None) -> Budget:
    """Build a :class:`Budget` splitting ``total_context`` across consumers.

    The sum of allocations never exceeds ``total_context`` (data-model validation).
    """
    split = split or _DEFAULT_SPLIT
    alloc = {name: int(total_context * frac) for name, frac in split.items()}
    # Guard against rounding pushing the sum over the total.
    overflow = sum(alloc.values()) - total_context
    if overflow > 0 and "conversation" in alloc:
        alloc["conversation"] = max(0, alloc["conversation"] - overflow)
    return Budget(total=total_context, threshold=threshold, alloc=alloc)


def should_compact(budget: Budget) -> bool:
    """Return True when usage has reached/exceeded the compaction threshold."""
    return budget.used >= budget.limit
