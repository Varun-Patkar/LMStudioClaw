# Architecture — LM Studio Manager (Call-Based Agent Runtime)

This document describes the modules, data/control flow, integration points, invariants,
and extension points of the agent runtime. See [README.md](README.md) for the
human-facing overview and [specs/001-agent-runtime/](specs/001-agent-runtime/) for the
full design (spec, plan, contracts, data model).

## Overview

A resident **controller** (system tray + local web UI) runs while the PC is on with
**no model loaded at idle**. On a manual session or a fired automation it loads the
chosen model, runs an interactive multi-turn agent loop (streaming, steering, queuing,
stop, automatic context compaction) using enabled skills/tools/MCP servers within a
hierarchical, least-privilege folder-consent boundary, then unloads the model and
records the session. Exactly one session runs at a time (FIFO queue).

## Module map

```text
lmstudioclaw/
├── cli.py                  # Thin entry point: free-port selection, tray, uvicorn
├── app.py                  # Controller: wires services, lifespan, session coordination
├── config/
│   ├── paths.py            # Documents layout + isolated %APPDATA% secrets path + bootstrap
│   └── settings.py         # File-backed settings singleton (safe defaults)
├── model/
│   ├── catalog.py          # LM Studio connection + one-call model discovery (no polling)
│   ├── lifecycle.py        # load / unload / warmup / orphan-detect (single-model invariant)
│   └── context_prefs.py    # per-model context clamp [1024, max]
├── orchestrator/
│   ├── engine.py           # interactive turn loop: stream, tool calls, steer/queue/stop
│   ├── budget.py           # token estimate + context-window allocation
│   ├── compaction.py       # ~90% summarize-and-replace compression
│   ├── persona.py          # persona resolution (default + library)
│   └── memory.py           # durable agent learnings (Documents memory/ area)
├── capabilities/
│   ├── registry.py         # unified capability surface + built-in consent-gated fs tools
│   ├── skills.py           # SKILL.md discovery/validation + referenced scripts
│   ├── tools.py            # custom python tools (trust gate, in-process exec)
│   └── mcp_client.py       # MCP servers via the `mcp` SDK (isolated short-lived sessions)
├── consent/
│   └── path_gate.py        # canonicalize + hierarchical grant check + hard deny-list
├── automations/
│   └── scheduler.py        # event-driven Daily/Interval scheduler + missed-run detection
├── sessions/
│   ├── queue.py            # single-active-session FIFO
│   └── store.py            # SQLite persistence (best-effort writes) + retention pruning
├── secrets/
│   └── vault.py            # isolated secrets store (user-only writes, no agent read path)
├── notifications/
│   └── toast.py            # Windows toast notifications (never contain secrets)
├── web/
│   ├── api.py              # FastAPI app factory + static SPA mount + health
│   ├── ws.py               # session WebSocket hub (streaming + steer/queue/stop/consent)
│   ├── routes_*.py         # REST route groups (sessions, automations, capabilities, settings)
│   └── static/             # vanilla-JS SPA (app shell + per-area views)
└── tray/
    └── icon.py             # pystray tray: Open (browser) / Quit (graceful shutdown)
```

## Control flow (a session)

1. `web/routes_sessions.py` (manual) or `automations/scheduler.py` (automation) asks the
   `Controller` to start a session.
2. The `Controller` creates a `Session` row (`queued`) and enqueues a runner on the
   `SessionQueue`.
3. The queue runs one runner at a time. The runner:
   - prepares the capability registry (skills/tools/MCP + memory tools, scoped),
   - loads the model via `ModelLifecycle` (`loading` → `active`),
   - runs `Engine.run_session`, which streams tokens, dispatches tool calls through the
     `CapabilityRegistry` (file I/O via the `PathGate`), compacts at the budget threshold,
     and honors steer/queue/stop signals from the WebSocket,
   - on any terminal state **always unloads the model** and clears session-scoped grants,
   - records the result and prunes old history.
4. Events flow `Engine → on_event → SessionHub.broadcast → WebSocket → browser`.

## Integration points

- **LM Studio native API** (`/api/v1/models[/load|/unload]`) via `httpx` — model catalog
  and lifecycle (reused from the original app).
- **LM Studio OpenAI-compatible API** (`/v1`) via the `openai` async client — chat
  streaming + tool calling and compaction summaries.
- **MCP servers** via the official `mcp` SDK — external tools.
- **VS Code** via the `code` CLI — `POST /api/open-in-vscode` opens referenced files.

## Invariants

- **At most one model loaded** (single-active FIFO queue); idle = zero models.
- **Every terminal session unloads the model.**
- **All agent file I/O passes the `PathGate`**; the secrets area and app internals are a
  hard deny-list regardless of grants; workspace is always allowed; grants are
  hierarchical and least-privilege (read ≠ write).
- **Secrets never reach the agent** — no `get_value` is exposed; only runtime `inject`.
- **No idle polling** of LM Studio; the scheduler sleeps until the next fire.
- **Best-effort persistence** — storage hiccups never crash the controller.

## Extension points (plug-and-play)

- **Skills**: drop a `SKILL.md` folder under `Documents/LMStudioClaw/skills/`.
- **Custom tools**: drop a `.py` module under `tools/` (requires trust confirmation).
- **MCP servers**: add an entry to `mcp.json` (or via the UI / agent).
- **Personas**: managed in Settings; the default is editable but not deletable.

Adding any capability only touches its files plus a registry row — no other module changes.
