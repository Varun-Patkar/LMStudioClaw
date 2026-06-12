# Implementation Plan: Call-Based LM Studio Agent Runtime

**Branch**: `001-agent-runtime` | **Date**: 2026-06-12 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from [specs/001-agent-runtime/spec.md](spec.md)

## Summary

Transform the existing single-file LM Studio tray model-manager into a **call-based, local agent
runtime**. A resident controller (system tray + local web UI) stays running while the PC is on with
**no model loaded at idle**. On a manual session or a fired automation it loads the chosen model,
runs an **interactive multi-turn agent loop** (steering, message queuing, stop-generating, automatic
context compression at ~90%) using enabled skills, custom tools, and MCP servers within a
**hierarchical, least-privilege folder-consent** boundary, then unloads the model and records the
session. Exactly one session runs at a time (FIFO queue). Automations support Daily/Interval
schedules and new-or-persistent session modes; the agent can persist learnings to a memory area.
Secrets are isolated outside agent reach. The existing model load/unload/context functionality is
preserved under Settings → Advanced → Model Management.

**Technical approach**: a modular Python package — async **FastAPI + WebSocket** web UI, a
**lightweight custom orchestrator** (`openai` client against LM Studio `/v1` + official `mcp` SDK), a
**model lifecycle service** (reusing today's `httpx` native-API code), a **path-authorization gate**
for consent, an **asyncio scheduler** for automations, **SQLite** for state, and **pystray** for the
tray. See [research.md](research.md) for decision rationale.

## Technical Context

**Language/Version**: Python 3.12+ (existing `requires-python = ">=3.12"`).

**Primary Dependencies**: FastAPI + Uvicorn + Pydantic (web/validation), `openai` (LM Studio `/v1`
chat + tool calling, existing), `httpx` (LM Studio native API, existing), `mcp` (official MCP Python
SDK), `pystray` + `Pillow` (tray, existing), `pyyaml` (config, existing), a Windows toast library;
optional `tiktoken` for token estimation. Frontend: lightweight SPA served as static files.

**Storage**: SQLite (sessions, turns, automations, grants, capability registry, notifications) via
stdlib `sqlite3`; JSON/YAML files for human-editable config and the `SKILL.md`/tools/memory areas; an
isolated secrets file under `%APPDATA%` outside any agent-accessible path.

**Testing**: `pytest` (+ `pytest-asyncio`) — already declared in `pyproject.toml` `dev` extras; add a
`tests/` directory (currently none).

**Target Platform**: Windows 10/11 desktop (single local user), consistent with the current app.

**Project Type**: Local desktop application = resident controller (web service + tray) + ephemeral
agent runtime, single Python process.

**Performance Goals**: Idle = 0 models loaded, minimal CPU (event-driven scheduler, no busy poll).
Session start to first activity within a few seconds under normal conditions (SC-003). Model unloaded
within seconds of session end (SC-002).

**Constraints**: No two models loaded at once (single-session FIFO queue). No idle polling of LM
Studio. All agent file I/O passes the consent gate; secrets never reach the agent. Files ≤ ~500
meaningful lines (Constitution I). Best-effort external-file writes (Constitution II).

**Scale/Scope**: Single user; a handful of concurrent automations; session history bounded by a
configurable retention window (default 90 days). 79 functional requirements across 7 user stories.

## Constitution Check

*GATE: must pass before Phase 0. Re-checked after Phase 1 design (below).*

| Principle | Assessment | Status |
|----------|------------|--------|
| **I. Modularity & SoC** | Single `cli.py` is replaced by a package with separated concerns (web, orchestrator, model lifecycle, consent, scheduler, capabilities, storage, tray). Each module targets ≤500 meaningful lines. | PASS (by design) |
| **II. Security First** | Central path-authorization gate (traversal/symlink-safe), secrets isolated outside agent scope and never logged, boundary validation via Pydantic, best-effort file writes, arbitrary-code trust gate for tools. | PASS |
| **III. Explicit User Consent** | Folder access is consent-gated (session/permanent); destructive scope changes prompt. **Tension**: automated runs load models without a per-run prompt. Mitigated: automations are pre-authorized when the user creates/enables them and may use only permanent grants (FR-025); model load/unload around a run is user-invoked behavior, not hidden background action. | PASS (justified) |
| **IV. Configurability & Plug-and-Play** | Skills (`SKILL.md`), tools (folder), MCPs (`mcp.json`), personas, and settings are all file/config-driven; capabilities add/remove without touching unrelated modules; agent can also author capabilities. | PASS |
| **V. Resource Frugality** | No model at idle; model loaded only per run; off-UI-thread async work. **Tension**: a resident web server + always-on scheduler are long-lived. Mitigated: scheduler is event-driven (single sleep-until-next-fire, no busy poll), no LM Studio polling, no model held at idle. | PASS (justified) |
| **VI. Documentation for Onboarding** | Plan + research + data-model + contracts + quickstart produced now; `ARCHITECTURE.md`, `README.md`, and `AGENTS.md` updates are required implementation deliverables; docstrings mandated. | PASS (planned) |

No unjustified violations. Justified tensions are recorded in **Complexity Tracking**.

## Project Structure

### Documentation (this feature)

```text
specs/001-agent-runtime/
├── plan.md              # This file
├── research.md          # Phase 0 decisions
├── data-model.md        # Phase 1 entities
├── quickstart.md        # Phase 1 dev/run guide
├── contracts/           # Phase 1 interface contracts
│   ├── http-api.md      # REST + WebSocket control-panel contract
│   └── internal-interfaces.md  # Module boundary contracts
└── checklists/
    └── requirements.md  # Spec quality checklist (existing)
```

### Source Code (repository root)

The current single module [lmstudioclaw/cli.py](../../lmstudioclaw/cli.py) is decomposed into
a package. Model lifecycle code is preserved (moved, not rewritten) into `model/`.

```text
lmstudioclaw/
├── __init__.py
├── cli.py                     # Thin entry point: start controller, tray, web server
├── app.py                     # Controller wiring / lifespan (FastAPI app + scheduler + tray)
├── config/
│   ├── settings.py            # Settings load/save, defaults, theme, retention, budgets
│   └── paths.py               # Documents folder layout + isolated secrets path resolution
├── model/                     # Model lifecycle (reuses existing httpx native-API logic)
│   ├── lifecycle.py           # load / unload / warmup / orphan-detect
│   ├── catalog.py             # discover models, context lengths, capabilities
│   └── context_prefs.py       # per-model context-length preferences (existing behavior)
├── orchestrator/
│   ├── engine.py              # interactive turn loop: stream, steer, queue, stop
│   ├── budget.py              # token estimate + context budget allocation
│   ├── compaction.py          # ~90% summarize-and-replace compression
│   └── persona.py             # persona resolution (default + library)
├── capabilities/
│   ├── skills.py              # discover/validate SKILL.md + referenced scripts
│   ├── tools.py               # load custom python tools, trust gate, timeouts
│   ├── mcp_client.py          # MCP server connections via `mcp` SDK
│   └── registry.py            # unified tool/skill/MCP registry offered to the agent
├── consent/
│   └── path_gate.py           # canonicalize + hierarchical grant check + deny-list
├── automations/
│   └── scheduler.py           # asyncio scheduler, Daily/Interval, missed-run detection
├── sessions/
│   ├── queue.py               # single-active-session FIFO queue
│   └── store.py               # SQLite persistence for sessions/turns/grants/etc.
├── secrets/
│   └── vault.py               # isolated secrets store (user-only writes, never to agent)
├── notifications/
│   └── toast.py               # Windows toast notifications
├── web/
│   ├── api.py                 # REST routes (sessions, automations, capabilities, settings)
│   ├── ws.py                  # WebSocket session stream + steer/queue/stop events
│   └── static/                # SPA assets (control panel: Sessions, Automations, Settings)
├── tray/
│   └── icon.py                # pystray tray; "Open" launches browser at served URL
└── configs/
    ├── default.yaml           # existing connection defaults
    └── context_prefs.json     # existing per-model prefs

tests/
├── unit/                      # path gate, budget, scheduler timing, persona resolution
├── integration/               # session lifecycle, consent flow, automation fire + missed-run
└── contract/                  # HTTP/WebSocket contract tests
```

**Structure Decision**: Single Python package, modular by concern (Constitution I). The model
lifecycle code from the existing `cli.py` is relocated into `model/` rather than rewritten, preserving
proven behavior (warmup, context clamping, orphan unload). The web UI and orchestrator are new
modules. No file should exceed ~500 meaningful lines; `orchestrator/engine.py` and `web/api.py` are the
likeliest to need further splitting during implementation.

## Complexity Tracking

| Violation / Tension | Why Needed | Simpler Alternative Rejected Because |
|---------------------|-----------|--------------------------------------|
| Always-on web server + tray process | The product must be reachable on demand and run automations while the PC is on (FR-001, FR-039). | A purely on-demand process can't fire scheduled automations or host the control panel. |
| In-app scheduler (a timer) vs. Principle V | Automations require time-based triggers and missed-run detection (FR-029–FR-031). | OS Task Scheduler rejected by user; busy-polling rejected — scheduler uses one event-driven sleep-until-next-fire and holds no model at idle. |
| Automated model load without per-run consent prompt vs. Principle III | Unattended automations must run without a human present (US4). | Pre-authorization model: user creates/enables the automation and grants permanent folder access up front; no hidden background action beyond what was scheduled. |
| New dependencies (FastAPI, mcp SDK, toast) | Web UI, MCP integration, and notifications are core required capabilities. | Hand-rolling HTTP/MCP/WebSocket would be larger, more error-prone, and less secure than maintained libraries. |

## Phase 1 outputs

- [data-model.md](data-model.md) — entities, fields, relationships, state transitions.
- [contracts/http-api.md](contracts/http-api.md) — REST + WebSocket control-panel contract.
- [contracts/internal-interfaces.md](contracts/internal-interfaces.md) — module boundary contracts.
- [quickstart.md](quickstart.md) — setup, run, and developer onboarding.

## Post-Design Constitution Re-Check

After Phase 1 design, all gates still **PASS**: the module layout enforces Modularity (I); the path
gate + secrets vault + boundary validation enforce Security (II); the consent gate and
pre-authorization model satisfy Consent (III); file/config-driven capabilities satisfy
Configurability (IV); idle-no-model + event-driven scheduler satisfy Frugality (V); and the planned
`ARCHITECTURE.md`/docstrings satisfy Documentation (VI). No new violations introduced by the design.
