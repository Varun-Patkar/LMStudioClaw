"""Per-run capability configuration (feature 002).

A :class:`RunConfig` is the optional configuration attached to a session start, a
follow-up that begins a new run, or a saved automation. It carries three orthogonal
choices, each defaulting to global settings when absent:

* ``model`` — the model key to load for this run (``None`` → default model);
* ``tool_overrides`` — per-tool enable(``True``)/disable(``False``) flags that are
  **independent** of the global tool configuration (a globally-enabled tool may be
  disabled for this run and vice versa) and never mutate global state (FR-028);
* ``mcp_selection`` — the MCP server ids active for this run (``None`` → all
  globally-enabled servers; ``[]`` → none). De-selecting a server scopes it out of
  this run only (FR-030).

Resolution of the *effective* toolset is **most-granular-wins** (FR-030a): the MCP
selection decides which servers are active, then per-tool overrides apply on top of
the resulting tool set. See :meth:`CapabilityRegistry.effective_tools`.

Skills are intentionally NOT representable here — they are always globally available
and are never per-run toggles (FR-031).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RunConfig:
    """Optional per-run model / tool / MCP configuration.

    Attributes:
        model: Model key to load for the run, or ``None`` to use the default.
        tool_overrides: Map of tool name → enabled flag, independent of global config.
        mcp_selection: List of MCP server ids active for the run; ``None`` means "all
            globally-enabled servers", an empty list means "no MCP servers".
    """

    model: str | None = None
    tool_overrides: dict[str, bool] = field(default_factory=dict)
    mcp_selection: list[str] | None = None

    @classmethod
    def from_dict(cls, data: dict | None) -> "RunConfig | None":
        """Build a :class:`RunConfig` from a JSON-ish dict (``None`` → ``None``).

        Unknown / malformed fields are coerced to safe defaults so a bad payload can
        never crash a run (validation happens again at the REST boundary).
        """
        if not data:
            return None
        raw_overrides = data.get("tool_overrides") or {}
        overrides: dict[str, bool] = {}
        if isinstance(raw_overrides, dict):
            for name, enabled in raw_overrides.items():
                overrides[str(name)] = bool(enabled)
        mcp = data.get("mcp_selection")
        if mcp is not None and not isinstance(mcp, list):
            mcp = None
        model = data.get("model")
        return cls(
            model=str(model) if model else None,
            tool_overrides=overrides,
            mcp_selection=[str(m) for m in mcp] if isinstance(mcp, list) else None,
        )

    def to_dict(self) -> dict:
        """Serialize to a plain dict for persistence / transport (no secrets)."""
        return {
            "model": self.model,
            "tool_overrides": dict(self.tool_overrides),
            "mcp_selection": list(self.mcp_selection) if self.mcp_selection is not None else None,
        }
