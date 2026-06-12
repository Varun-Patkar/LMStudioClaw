"""LM Studio Manager — call-based local agent runtime.

A resident controller (system tray + local web UI) that stays running while the PC
is on with **no model loaded at idle**. On a manual session or a fired automation it
loads the chosen model, runs an interactive multi-turn agent loop (steering, message
queuing, stop-generating, automatic context compression) using enabled skills, custom
tools, and MCP servers within a hierarchical, least-privilege folder-consent boundary,
then unloads the model and records the session.

See ``specs/001-agent-runtime/`` for the full design.
"""

__version__ = "1.0.0"
