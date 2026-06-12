"""Agent learnings / durable memory.

Lets the agent persist durable insights to the Documents ``memory/`` area and recall
them into later sessions within the token budget (FR-065/FR-066/SC-017). Learnings
are first-party, path-constrained operations: the ``remember``/``recall`` tools write
and read **only** inside the memory folder, so they do not require a consent prompt
yet cannot touch anything outside that area. Secrets are never written here.

Files are simple Markdown notes named ``<scope>-<timestamp>.md`` so they are easy to
inspect and edit by hand.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from .budget import estimate_tokens

# Cap how much learning text is loaded into a session's context.
_DEFAULT_RECALL_TOKENS = 1500


def _safe_scope(scope: str) -> str:
    """Sanitize a scope label for use in a filename."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", scope or "global")[:64] or "global"


def persist_learning(memory_dir: Path, content: str, scope: str = "global") -> Path:
    """Write a learning note into the memory area; returns the file path.

    ``scope`` is ``global`` (loaded into every session) or an automation id (loaded
    only for that automation's runs).
    """
    memory_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    path = memory_dir / f"{_safe_scope(scope)}-{stamp}.md"
    path.write_text(content.strip() + "\n", encoding="utf-8")
    return path


def load_learnings(
    memory_dir: Path, scope: str | None = None, max_tokens: int = _DEFAULT_RECALL_TOKENS
) -> str:
    """Load relevant learnings (global + ``scope``) up to a token budget.

    Newest notes are preferred; loading stops once the budget is reached so memory
    never crowds out the conversation (FR-066/FR-067).
    """
    if not memory_dir.exists():
        return ""
    wanted = {"global"}
    if scope:
        wanted.add(_safe_scope(scope))
    files = sorted(memory_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    collected: list[str] = []
    used = 0
    for f in files:
        prefix = f.stem.rsplit("-", 1)[0]
        if prefix not in wanted:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        cost = estimate_tokens(text)
        if used + cost > max_tokens:
            break
        collected.append(text)
        used += cost
    return "\n\n".join(collected)


def register_memory_tools(registry, memory_dir: Path, scope: str | None = None) -> None:
    """Register first-party ``remember``/``recall`` tools on the registry.

    These are path-constrained to the memory area (no consent prompt needed) and are
    used by sessions and especially persistent-session automations.
    """
    from ..capabilities.registry import ToolResult, ToolSpec

    async def _remember(*, content: str, consent=None) -> ToolResult:
        """Persist a durable learning note to the memory area."""
        try:
            path = persist_learning(memory_dir, content, scope or "global")
            return ToolResult(True, f"Saved learning to {path.name}")
        except OSError as exc:
            return ToolResult(False, "", error=f"Could not save learning: {exc}")

    async def _recall(*, consent=None) -> ToolResult:
        """Recall previously saved learnings relevant to this session."""
        text = load_learnings(memory_dir, scope)
        return ToolResult(True, text or "(no learnings saved yet)")

    registry.register_tool(ToolSpec(
        "remember", "Persist a durable learning/insight for future sessions.",
        {"type": "object",
         "properties": {"content": {"type": "string", "description": "The insight to remember"}},
         "required": ["content"]},
        _remember,
    ))
    registry.register_tool(ToolSpec(
        "recall", "Recall durable learnings saved in earlier sessions.",
        {"type": "object", "properties": {}},
        _recall,
    ))
