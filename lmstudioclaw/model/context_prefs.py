"""Per-model context-length preferences.

Preserves the existing behaviour from the original tray app: a user may pin an exact
context length per model, clamped to ``[1024, model.max_context_length]`` (FR-046).
Stored in ``config/context_prefs.json`` next to the package.
"""

from __future__ import annotations

import json
from pathlib import Path

_PREFS_PATH = Path(__file__).resolve().parent.parent / "config" / "context_prefs.json"

# Lower bound for a pinned context length (preserves original clamp).
MIN_CONTEXT = 1024


def load_prefs() -> dict[str, int]:
    """Load saved per-model context preferences (positive ints only)."""
    if not _PREFS_PATH.exists():
        return {}
    try:
        data = json.loads(_PREFS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, int) and v > 0}


def save_prefs(prefs: dict[str, int]) -> None:
    """Persist context preferences to disk (best-effort)."""
    try:
        _PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _PREFS_PATH.write_text(json.dumps(prefs, indent=2), encoding="utf-8")
    except OSError:
        pass


def preferred_context(model: dict, prefs: dict[str, int] | None = None) -> int:
    """Resolve the effective context: pinned preference if set, else model max.

    Falls back to 4096 if the model exposes no usable max context length.
    """
    prefs = load_prefs() if prefs is None else prefs
    key = model.get("key", "")
    max_ctx = int(model.get("max_context_length", 0) or 0)
    if max_ctx <= 0:
        return 4096
    pref = int(prefs.get(key, max_ctx))
    if pref < 1:
        return max_ctx
    return min(pref, max_ctx)


def set_context_pref(model: dict, requested: int, prefs: dict[str, int] | None = None) -> int:
    """Validate, clamp, and persist an exact context preference; return applied value.

    Raises ``ValueError`` if the model has no valid key/max context length.
    """
    prefs = load_prefs() if prefs is None else prefs
    key = model.get("key", "")
    max_ctx = int(model.get("max_context_length", 0) or 0)
    if not key or max_ctx <= 0:
        raise ValueError("Selected model does not expose a valid max context length.")
    requested = max(MIN_CONTEXT, min(int(requested), max_ctx))
    prefs[key] = requested
    save_prefs(prefs)
    return requested
