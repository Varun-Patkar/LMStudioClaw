# Contract: Internal Module Interfaces

**Feature**: 001-agent-runtime | **Date**: 2026-06-12 | **Spec**: [spec.md](../spec.md)

Module boundary contracts that keep concerns separable (Constitution I) and let capabilities plug in
without touching unrelated modules (Constitution IV). These are behavioral interface descriptions;
exact signatures are finalized in implementation. All long-running calls are `async`.

---

## `model/lifecycle.py` — Model lifecycle service

Reuses the existing `httpx` native-API logic. The orchestrator depends on this; nothing else loads
models.

- `async load(model_key, context_length) -> LoadedModel` — load via `/api/v1/models/load`.
- `async unload(instance_id) -> None` — unload via `/api/v1/models/unload`.
- `async warmup(model_id) -> None` — initialize KV cache (existing behavior).
- `async detect_orphan() -> LoadedModel | None` — on startup, find a model left loaded (FR-006).

**Invariants**: never more than one loaded model; every session terminal state calls `unload`.

## `model/catalog.py`

- `async list_models() -> list[ModelInfo]` — one call, no polling (Constitution V); includes
  `max_context_length`, capabilities, quant, size.

## `orchestrator/engine.py` — Interactive turn loop

- `async run_session(session, registry, budget, consent, on_event) -> SessionResult`
  - Drives turns against LM Studio `/v1` via the `openai` client (streaming + tool calls).
  - Emits events through `on_event` (mapped to WebSocket events).
  - Accepts control signals: `steer(text)`, `queue(text)`, `stop(scope)`.
  - Calls `budget`/`compaction` before each turn; calls `consent` before any file tool.
- Control-signal contract:
  - `steer` during generation injects into the current turn; when idle, behaves as `message`.
  - `queue` always enqueues for the next turn.
  - `stop(turn)` aborts the current turn, keeps the session open; `stop(session)` ends it.

## `orchestrator/budget.py` + `compaction.py`

- `estimate_tokens(text|messages) -> int` — model-aware estimate (tiktoken/heuristic fallback).
- `allocate(total_context) -> Budget` — split across persona/skills/memory/conversation/tool output.
- `should_compact(budget) -> bool` — true at/above threshold (~0.90).
- `async compact(messages, model) -> (summary_turn, freed_tokens)` — summarize-and-replace older
  turns (FR-061); records a `CompressionEvent`.

## `orchestrator/persona.py`

- `resolve(persona_id | None) -> Persona` — returns selected or the editable default (FR-071/FR-073).

## `capabilities/registry.py` — Unified capability registry

- `discover() -> list[Capability]` — scan skills folder, tools folder, `mcp.json`.
- `enabled_tools() -> list[ToolSpec]` — tools+MCP tools offered to the model as callable functions.
- `enabled_skills() -> list[SkillDoc]` — `SKILL.md` instructions injected within budget.
- `add_capability(spec, added_by) -> Capability` — used by both UI and the **agent** (FR-079).
- `invoke_tool(name, args, consent) -> ToolResult` — dispatch to python tool or MCP; enforce
  per-call timeout (FR-018); route file I/O through `consent`.

**Plug-and-play rule**: adding/removing a skill/tool/MCP only touches its files + a registry row;
no other module changes (Constitution IV).

## `capabilities/skills.py` / `tools.py` / `mcp_client.py`

- `skills.load(folder) -> SkillDoc | InvalidSkill` — parse `SKILL.md`, list referenced scripts.
- `tools.load(module) -> ToolSpec` — requires `trust_confirmed` before enable (FR-015); runs
  in-process with timeout + exception capture.
- `mcp_client.connect(entry) -> McpSession | ConnectError` — via the `mcp` SDK; expose tools.

## `consent/path_gate.py` — Path-authorization gate (single chokepoint)

- `authorize(path, access, session) -> Decision` — canonicalize (resolve symlinks + `..`); allow if
  workspace or hierarchical-prefix of an active grant; **deny-list** the secrets area + app internals
  (FR-077). For unattended automations, never block interactively — fail fast if no permanent grant
  (FR-025).
- `request_grant(path, access, session) -> pending_request_id` — surfaces a `consent_request` event.

**Invariant**: every agent file operation passes through `authorize`; there is no other file path.

## `automations/scheduler.py`

- `next_fire(automation, now) -> datetime` — Daily (weekdays+time) or Interval (every X).
- `async run() -> None` — event-driven: sleep until the nearest `next_fire`, then enqueue a session;
  no busy poll (Constitution V).
- `detect_missed(now) -> list[MissedRun]` — startup comparison vs `last_run_at` (FR-031).

## `sessions/queue.py` — Single-active-session FIFO

- `enqueue(request) -> position` · `cancel(id)` · `async run_loop()` — ensures exactly one active
  session; dequeues in order (FR-008).

## `sessions/store.py` — SQLite persistence

- CRUD for sessions/turns/automations/grants/capabilities/notifications; retention pruning. All
  writes best-effort/transactional so corruption can't crash the controller (Constitution II).

## `secrets/vault.py` — Isolated secrets store

- `set(ref_name, value)` / `delete(ref_name)` — **user-only** (FR-078).
- `inject(connection_spec) -> connection_spec_with_secret` — runtime-only; secrets never returned to
  callers that serialize to the agent/UI/logs (FR-077). No `get_value()` exposed to the agent.

## `notifications/toast.py`

- `notify(type, message, related?) -> None` — Windows toast; messages contain no secrets (FR-026).

## `web/api.py` + `web/ws.py`

- Translate REST/WebSocket (see [http-api.md](http-api.md)) into calls on the above services; validate
  inputs with Pydantic; never serialize secrets.

## `tray/icon.py`

- `start(open_url)` — pystray icon; "Open" launches the browser at the served URL; "Quit" triggers
  graceful shutdown (stop server + scheduler, unload model). Closing the browser does **not** quit
  (FR-043).
