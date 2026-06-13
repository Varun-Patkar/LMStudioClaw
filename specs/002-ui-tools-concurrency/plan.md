# Implementation Plan: Professional UI, Default Agent Toolset & Single-Run Concurrency

**Branch**: `002-ui-tools-concurrency` | **Date**: 2026-06-13 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/002-ui-tools-concurrency/spec.md`

## Summary

This feature extends the existing call-based LM Studio agent runtime (feature `001-agent-runtime`)
along four axes without touching its core lifecycle (no model at idle; load-run-unload; one model at
a time):

1. **A professional, responsive, live-updating web UI** — replace the narrow fixed-width SPA shell
   with a fluid ~90vw layout, a consistent component system, a persistent top-right run indicator
   plus collapsible queue panel, and live status driven by a global control WebSocket so the user
   never reloads the page after "Load model".
2. **A richer, consistently-named default toolset** — reuse the existing `read_file` / `list_dir` /
   `write_file` and add `edit` (overloaded: exact-string find/replace **and** line-range replace),
   `grep`, `find`, `powershell` (consent-gated, workspace-rooted), and a `parallel` meta-tool that
   runs ≥2 independent sub-tool-calls concurrently. `read_file` gains optional range arguments.
3. **Single-run concurrency made visible & durable** — the existing single-active FIFO `SessionQueue`
   is preserved; this feature surfaces it in the UI and makes it **persisted/resumable** across app
   restarts.
4. **Per-run configuration** — sessions (including follow-ups that start a new run) and automations
   accept an optional run config: model, per-run tool enable/disable overrides (independent of global
   config), and per-run MCP selection. Resolution is most-granular-wins (MCP selection → per-tool
   override). Skills stay globally available and are never per-run toggles.

Technical approach: build on the current FastAPI + Uvicorn + WebSocket + vanilla-ESM-SPA stack and the
`CapabilityRegistry` / `SessionQueue` / `Engine` modules already in place. No new heavy dependencies;
the UI overhaul is plain CSS/JS (no framework) to honor Resource Frugality and the existing build.

## Technical Context

**Language/Version**: Python 3.11+ (backend), modern ES modules (browser SPA — no framework)

**Primary Dependencies**: FastAPI + Uvicorn (web), Starlette WebSockets (live updates), `openai`
client (LM Studio `/v1`), official `mcp` SDK (MCP client), `httpx` (native model API), SQLite
(`sqlite3` stdlib) for state, `pystray` + `Pillow` (tray). No new runtime dependency is introduced.

**Storage**: SQLite for run/queue/session state (extends the existing `sessions/store.py` schema with
a persisted-queue table); JSON/YAML files for config (`mcp.json`, settings, per-automation run config).

**Testing**: `pytest` (unit + integration + contract), reusing the existing `tests/` layout.

**Target Platform**: Windows 10/11 desktop (single-user, localhost-only). PowerShell is the shell.

**Project Type**: Desktop tray app + local web UI (single Python package, server-rendered static SPA).

**Performance Goals**: UI status reflects a state change live within ~1s of the event over WebSocket;
no idle polling (Constitution V); the run indicator/queue update on push, not on a timer.

**Constraints**: No model loaded at idle; exactly one active run; content width ≈90vw with ≈5vw
gutters and no fixed narrow max-width; PowerShell tool bounded by timeout + truncated output; all
file-accessing tools (incl. powershell) routed through the consent/path gate; secrets never reachable.

**Scale/Scope**: Single user, single machine. ~4 SPA views restyled + 1 new shared run/queue widget;
~5 new/extended agent tools; 1 persisted-queue table; per-run config plumbed through session +
automation start paths.

## Constitution Check

*GATE: evaluated against `.specify/memory/constitution.md` v1.1.0. Re-checked after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Modularity & Separation of Concerns | PASS | New tools live in dedicated `capabilities/file_tools.py` + `shell_tool.py` + `parallel_tool.py` (keeps `registry.py` under ~500 lines); the run/queue UI widget is its own `static/views/runbar.js`; persisted-queue logic added to `sessions/store.py` + `sessions/queue.py` without entangling the engine. Per-run config is a small dataclass passed through, not scattered branching. |
| II. Security First | PASS | PowerShell tool is consent-gated exactly like file tools (workspace-rooted; secrets/app-internals hard-denied; outside paths prompt; grants persist) — no new bypass. All REST/WS inputs validated with Pydantic. Output bounded/truncated. No secret values cross any boundary. Shell command is passed to PowerShell as a single bounded argument; no untrusted text is interpolated into a meta-shell. |
| III. Explicit User Consent | PASS | File/edit/write/powershell access outside consented folders raises the existing consent prompt (session/permanent). Per-run config is an explicit user choice. No new silent mutation. |
| IV. Configurability & Extensibility | PASS | Per-run tool/MCP overrides ride on the existing capability registry; adding a tool remains a single-module change. MCP selection reuses `mcp.json`. No core surgery. |
| V. Resource Frugality | PASS | Live UI uses the existing push WebSocket — **no new polling/timers**. The `parallel` tool bounds concurrency to model-emitted independent calls. Persisted queue uses best-effort SQLite writes (no background loop). UI is framework-free. |
| VI. Documentation for Onboarding | PASS | ARCHITECTURE.md + AGENTS.md updated for the new tools, run/queue surface, persisted queue, and per-run config; docstrings on every new module/function; this plan + research record decisions. |

**Initial gate**: PASS (no violations). **Complexity Tracking**: none required.

**Post-Design re-check (after Phase 1)**: PASS — the design adds one global-status WebSocket channel
(reusing the existing hub pattern, no polling), one persisted-queue table, and per-run config
dataclasses; no principle is violated and no new justified-complexity entries are needed.

## Project Structure

### Documentation (this feature)

```text
specs/002-ui-tools-concurrency/
├── plan.md              # This file
├── research.md          # Phase 0 — decisions & rationale
├── data-model.md        # Phase 1 — entities (RunConfig, persisted queue, tool override)
├── quickstart.md        # Phase 1 — how to validate each user story
├── contracts/
│   └── delta-api.md     # Phase 1 — REST/WS additions & changes vs the 001 contract
└── checklists/
    └── requirements.md  # (from /speckit.specify) — passing
```

### Source Code (repository root)

The existing package is extended in place; new files keep modules under the ~500-line limit.

```text
lmstudioclaw/
├── app.py                       # Controller: extend start_manual_session / enqueue_automation
│                                #   to accept RunConfig; broadcast global run/model status
├── capabilities/
│   ├── registry.py              # per-run tool override + MCP selection filtering
│   ├── file_tools.py            # NEW — read(range)/edit(exact|line-range)/grep/find/ls handlers
│   ├── shell_tool.py            # NEW — consent-gated, workspace-rooted PowerShell tool
│   └── parallel_tool.py         # NEW — parallel meta-tool (runs ≥2 independent sub-calls)
├── orchestrator/
│   └── engine.py                # honor per-run tool set; support parallel dispatch
├── sessions/
│   ├── queue.py                 # persist/restore queue items; resume on startup
│   └── store.py                 # NEW table: queued_runs (id, payload, position, created_at)
├── web/
│   ├── routes_sessions.py       # accept run_config on POST /api/sessions; queue endpoints
│   ├── routes_automations.py    # accept/persist run_config on automations
│   ├── ws.py                    # NEW global status channel (/ws/status) alongside per-session
│   └── static/
│       ├── app.css              # restyle: fluid 90vw layout, design tokens, components, responsive
│       ├── app.js               # SPA shell: mount run indicator + live status; remove 1100px cap
│       ├── api.js               # run-config helpers + status socket client
│       └── views/
│           ├── runbar.js        # NEW — top-right run indicator + collapsible queue panel
│           ├── sessions.js      # run-config form; live status; load-model live feedback
│           ├── automations.js   # per-automation run-config (model/tools/MCP)
│           ├── capabilities.js  # show new default tools
│           └── settings.js      # global tool enable/disable surface
└── ...

tests/
├── unit/
│   ├── test_file_tools.py       # NEW — edit exact/line-range, read range, grep/find/ls
│   ├── test_shell_tool.py       # NEW — powershell consent + timeout + truncation
│   ├── test_parallel_tool.py    # NEW — concurrent independent calls; same-target rejection
│   ├── test_run_config.py       # NEW — override precedence (MCP→tool), defaults, persistence
│   └── test_queue.py            # extend — persist/restore/resume
├── integration/
│   └── test_run_config_flow.py  # NEW — per-run model/tools/MCP applied; globals unchanged
└── contract/
    └── test_sessions_contract.py# extend — run_config + status channel shapes
```

**Structure Decision**: Single Python package (desktop tray app + local web UI), matching the existing
`001-agent-runtime` layout. The UI is a framework-free static SPA served by FastAPI. New tool handlers
and the run/queue UI widget are split into their own modules to respect the ~500-line modularity limit
and keep `registry.py` / `app.js` reviewable.

## Phase 0 — Research

See [research.md](research.md). All Technical Context items are resolved (no NEEDS CLARIFICATION
remain); the spec's 10 clarifications already pinned the behavioral decisions, and research records the
implementation-level choices (live-status channel, edit-tool overload, PowerShell sandboxing, parallel
meta-tool shape, persisted-queue storage, per-run override resolution, responsive layout strategy).

## Phase 1 — Design Artifacts

- [data-model.md](data-model.md) — new/extended entities: `RunConfig`, `ToolOverride`, `QueuedRun`
  (persisted queue), and the `Session`/`Automation` deltas that carry run config.
- [contracts/delta-api.md](contracts/delta-api.md) — REST additions/changes (run_config on session +
  automation start, queue cancel) and the new `/ws/status` global live-status channel, expressed as a
  delta over `001-agent-runtime/contracts/http-api.md`.
- [quickstart.md](quickstart.md) — step-by-step validation for each of the four user stories.

## Complexity Tracking

> No constitution violations — this section is intentionally empty.
