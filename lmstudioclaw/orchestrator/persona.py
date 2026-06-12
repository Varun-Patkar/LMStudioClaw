"""Persona resolution.

A persona is a named system-prompt body. Each session uses either an explicitly
selected persona or the editable default (FR-071/FR-073). Persona content counts
against the token budget (FR-067).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Persona:
    """A resolved persona (system prompt) for a session."""

    id: str
    name: str
    instructions: str
    is_default: bool


def resolve(store, persona_id: str | None) -> Persona:
    """Return the selected persona, or the editable default when ``persona_id`` is None.

    Falls back to the default if the requested persona id no longer exists. The
    default persona is created on demand by the store if absent.
    """
    if persona_id:
        row = store.get_persona(persona_id)
        if row:
            return _to_persona(row)
    default = store.ensure_default_persona()
    return _to_persona(default)


def _to_persona(row: dict) -> Persona:
    """Map a persona DB row to a :class:`Persona`."""
    return Persona(
        id=row["id"],
        name=row["name"],
        instructions=row["instructions"],
        is_default=bool(row["is_default"]),
    )
