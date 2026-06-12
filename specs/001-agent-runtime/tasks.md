---
description: "Task list for Call-Based LM Studio Agent Runtime implementation"
---

# Tasks: Call-Based LM Studio Agent Runtime

**Input**: Design documents from [specs/001-agent-runtime/](.)

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/http-api.md](contracts/http-api.md),
[contracts/internal-interfaces.md](contracts/internal-interfaces.md)

**Tests**: Focused tests are included for the high-risk modules the plan names as priority test
targets (consent path gate, token budget/compaction, scheduler, session queue, HTTP/WS contract).
Other areas are validated via the quickstart flows. This is not full TDD.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on incomplete tasks)
- **[Story]**: US1–US7 (maps to spec user stories); Setup/Foundational/Polish have no story label
- All paths are relative to the repository root `d:\Projects\LMStudioClaw`

## Path conventions

Single Python package `lmstudioclaw/` (see [plan.md](plan.md) Project Structure) + `tests/`.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Package skeleton, dependencies, and the Documents/secrets folder bootstrap.

- [X] T001 Restructure `lmstudioclaw/` into the planned package: create empty subpackages with `__init__.py` for `config/`, `model/`, `orchestrator/`, `capabilities/`, `consent/`, `automations/`, `sessions/`, `secrets/`, `notifications/`, `web/`, `tray/` per [plan.md](plan.md).
- [X] T002 Add runtime dependencies to [pyproject.toml](../../pyproject.toml) (`fastapi`, `uvicorn`, `mcp`, `tiktoken`, a Windows toast lib) without pinning versions; document the `pip install` commands in [specs/001-agent-runtime/quickstart.md](quickstart.md). (Per Constitution IV / repo dependency rule.)
- [X] T003 [P] Create `tests/` with `unit/`, `integration/`, `contract/` subfolders and `conftest.py` (pytest + pytest-asyncio already in `dev` extras).
- [X] T004 [P] Implement `lmstudioclaw/config/paths.py`: resolve the Documents layout (`skills/`, `tools/`, `workspace/`, `memory/`, `mcp.json`) and the isolated secrets path under `%APPDATA%` (outside any agent path) per FR-053, FR-076.
- [X] T005 Implement first-run bootstrap in `lmstudioclaw/config/paths.py` (or a `bootstrap.py`): create folders if absent, warn if not creatable (FR-053, SC-009).

**Checkpoint**: Package imports, folders bootstrap on first run.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Storage, settings, secrets vault, model lifecycle, consent gate, and the agent loop
core — everything user stories build on. **No user story can start until this phase completes.**

- [X] T006 Implement `lmstudioclaw/sessions/store.py`: SQLite schema + CRUD for sessions, turns, automations, grants, capabilities, notifications, compression events per [data-model.md](data-model.md); best-effort/transactional writes (Constitution II).
- [X] T007 [P] Implement `lmstudioclaw/config/settings.py`: load/save settings with safe defaults (theme=system, retention=90d, compression threshold=0.90, web port, idle timeout, max run duration) per FR-044–FR-052; secrets referenced by name only.
- [X] T008 [P] Implement `lmstudioclaw/secrets/vault.py`: isolated secrets store with `set`/`delete` (user-only) and runtime `inject`; never expose `get_value` to agent paths; never logged (FR-076–FR-078, FR-026, SC-012).
- [X] T009 [P] Implement `lmstudioclaw/model/catalog.py`: discover LM Studio models in one call (no polling), exposing context length/capabilities/quant/size — reuse existing `httpx` logic from [lmstudioclaw/cli.py](../../lmstudioclaw/cli.py) (FR-045, Constitution V).
- [X] T010 Implement `lmstudioclaw/model/lifecycle.py`: `load`/`unload`/`warmup`/`detect_orphan` reusing existing native-API code; enforce single-loaded-model invariant (FR-002, FR-006).
- [X] T011 [P] Move per-model context preferences into `lmstudioclaw/model/context_prefs.py`, preserving existing clamp `[1024, max_context_length]` behavior (FR-046).
- [X] T012 Implement `lmstudioclaw/consent/path_gate.py`: canonicalize (resolve symlinks + `..`), hierarchical grant prefix-match, workspace always-allow, secrets/app-internals deny-list, fail-fast for unattended runs (FR-019–FR-027, FR-069, FR-070, FR-077).
- [X] T013 [P] [Test] Unit tests for the path gate in `tests/unit/test_path_gate.py`: traversal escape, symlink escape, hierarchical subfolder allow, deny-list secrets, least-privilege (SC-004).
- [X] T014 Implement `lmstudioclaw/orchestrator/budget.py`: token estimate (tiktoken + heuristic fallback) and budget allocation across persona/skills/memory/conversation/tool-output (FR-067, FR-068).
- [X] T015 [P] [Test] Unit tests for budget allocation + threshold detection in `tests/unit/test_budget.py` (SC-018).
- [X] T016 Implement `lmstudioclaw/orchestrator/compaction.py`: summarize-and-replace older turns at threshold; record CompressionEvent (FR-061, SC-014).
- [X] T017 Implement `lmstudioclaw/orchestrator/persona.py`: resolve selected persona or editable default (FR-071, FR-073) backed by store.
- [X] T018 Implement `lmstudioclaw/capabilities/registry.py` skeleton: unified capability model + `invoke_tool` dispatch with per-call timeout, routing file I/O through the consent gate (FR-009, FR-016, FR-018).
- [X] T019 Implement `lmstudioclaw/orchestrator/engine.py`: interactive turn loop against LM Studio `/v1` via `openai` (streaming + tool calls), calling budget/compaction/consent; emit events via callback (FR-009, FR-056, FR-060).
- [X] T020 Implement `lmstudioclaw/sessions/queue.py`: single-active-session FIFO with enqueue/cancel/run-loop ensuring no two models load at once (FR-008).
- [X] T021 [P] [Test] Unit tests for the FIFO queue (single-active invariant, cancel-before-start) in `tests/unit/test_queue.py`.
- [X] T022 [P] Implement `lmstudioclaw/notifications/toast.py`: Windows toast for run/automation events; messages contain no secrets (FR-042, FR-026).
- [X] T023 Implement `lmstudioclaw/app.py` + update [lmstudioclaw/cli.py](../../lmstudioclaw/cli.py): controller lifespan wiring (FastAPI app + scheduler + tray), startup orphan-model detection, retention pruning (FR-001, FR-006, FR-038).
- [X] T024 [P] Implement `lmstudioclaw/tray/icon.py`: pystray tray; "Open" launches browser at served URL (incl. fallback port), "Quit" graceful shutdown (unload model, stop server/scheduler); close ≠ quit (FR-040, FR-041, FR-043).
- [X] T025 Implement `lmstudioclaw/web/api.py` base app + `lmstudioclaw/web/ws.py` skeleton: FastAPI on localhost with fallback port, Pydantic boundary validation, static SPA mount (FR-039, FR-055).

**Checkpoint**: Controller boots, tray opens browser, no model at idle, storage/settings/secrets/consent/engine in place. User stories can now proceed.

---

## Phase 3: User Story 1 — Interactive agent session (Priority: P1) 🎯 MVP

**Goal**: Start a session, run the model with streaming + steering/queue/stop + compression, then
unload on end.

**Independent Test**: With LM Studio up and no model loaded, start a session, send a message; verify
model loads, streams output, writes into `workspace/`, controls behave, and the model unloads on end.

- [X] T026 [US1] Implement `POST /api/sessions` (start/queue), `GET /api/sessions`, `GET /api/sessions/{id}`, `POST /api/sessions/{id}/stop`, `GET/DELETE /api/queue` in `lmstudioclaw/web/api.py` per [contracts/http-api.md](contracts/http-api.md) (FR-003, FR-004, FR-005, FR-008, FR-073).
- [X] T027 [US1] Implement the WebSocket `/ws/sessions/{id}` in `lmstudioclaw/web/ws.py`: server→client `status`/`token`/`tool_call`/`tool_result`/`budget`/`compaction`/`error`; client→server `steer`/`queue`/`stop`/`message` (FR-056–FR-060).
- [X] T028 [US1] Wire steering/queue/stop signals from `ws.py` into `orchestrator/engine.py` (Enter=steer current turn, Alt+Enter=queue, Stop=halt turn keep session) (FR-057–FR-059, SC-013).
- [X] T029 [US1] Implement session-end model unload + terminal status recording via `model/lifecycle.py` + `sessions/store.py`; idle-timeout + max-run-duration enforcement (FR-002, FR-007, FR-062, SC-002).
- [X] T030 [P] [US1] Build the Session view SPA in `lmstudioclaw/web/static/`: composer (Enter/Alt+Enter), streaming transcript, Stop button, live budget/context indicator (FR-039, FR-060, FR-068).
- [X] T031 [P] [US1] [Test] Contract test for session REST + WebSocket events in `tests/contract/test_sessions_contract.py` (SC-002, SC-013).
- [X] T032 [US1] [Test] Integration test: idle→start→stream→file-in-workspace→unload in `tests/integration/test_session_lifecycle.py` (SC-001, SC-002, SC-003).

**Checkpoint**: A user can run a full interactive session end to end (MVP).

---

## Phase 4: User Story 2 — Consent-bounded filesystem access (Priority: P1)

**Goal**: Workspace always allowed; other folders require session/permanent grant; hierarchical +
least-privilege; revocable.

**Independent Test**: Agent attempts access outside `workspace/`; prompt appears; session grant gone
next run; permanent persists; subfolder allowed under parent grant.

- [X] T033 [US2] Add the consent file-access tool(s) to `capabilities/registry.py` so all agent file ops route through `consent/path_gate.py` (FR-020, FR-024).
- [X] T034 [US2] Implement consent request/response flow: emit `consent_request` over WS, pause the run, resume on decision; fail-fast for unattended automations (FR-021, FR-025).
- [X] T035 [US2] Implement `GET/POST /api/grants` + `DELETE /api/grants/{id}` in `web/api.py`; persist session vs permanent grants; remove session grants on session end (FR-021–FR-023).
- [X] T036 [P] [US2] Build the consent prompt UI + grants management view in `web/static/` (grant session/permanent/deny, list, revoke) (FR-023).
- [X] T037 [US2] [Test] Integration test for consent lifecycle in `tests/integration/test_consent_flow.py`: prompt, session-scope expiry, permanent persistence across restart, hierarchical subfolder, revoke (SC-004, SC-005).

**Checkpoint**: Agent file access is fully consent-bounded and revocable.

---

## Phase 5: User Story 3 — Manage sessions (Priority: P2)

**Goal**: Sessions page lists active/past runs with metadata, transcripts, grants, failures.

**Independent Test**: Run two sessions; open the Sessions page; verify metadata, transcripts, and
grant inspection/revocation.

- [X] T038 [US3] Extend session REST to return full metadata (trigger, model, status, timestamps, transcript turns, grants, compression events, failure reason/point) per [data-model.md](data-model.md) (FR-033, FR-036).
- [X] T039 [P] [US3] Build the Sessions list + detail SPA view in `web/static/`: status, trigger, model, transcript, active-session live status + Stop, grant management (FR-034, FR-035, FR-037).
- [X] T040 [US3] [Test] Integration test: two runs recorded with correct trigger/model/status/transcript in `tests/integration/test_sessions_history.py` (SC-011).

**Checkpoint**: Full session visibility and management.

---

## Phase 6: User Story 4 — Schedule automations (Priority: P2)

**Goal**: Daily/Interval schedules, new vs persistent session mode, run notifications, missed-run
reporting on startup.

**Independent Test**: Create a Daily (today, 1 min out) and an Interval automation; verify
notification + run; toggle session mode; simulate a miss and verify startup notification.

- [X] T041 [US4] Implement `lmstudioclaw/automations/scheduler.py`: `next_fire` for Daily (weekdays+time) and Interval (every X), event-driven sleep-until-next-fire (no busy poll), enqueue on fire (FR-029, FR-063, Constitution V).
- [X] T042 [US4] Implement missed-run detection on startup comparing `last_run_at` vs expected fires; emit `automation_missed` notifications (FR-030, SC-006).
- [X] T043 [US4] Implement persistent vs new session mode in the queue/engine path: persistent reuses + compresses the prior session; new starts fresh (FR-032, FR-064, SC-016).
- [X] T044 [US4] Implement `POST/GET/PATCH/DELETE /api/automations` + `POST /api/automations/{id}/run` in `web/api.py` (FR-028, FR-032, FR-073).
- [X] T045 [US4] Emit `automation_running` notification + record automation-triggered sessions (FR-029, FR-031, FR-042).
- [X] T046 [P] [US4] Build the Automations SPA view in `web/static/`: Daily multi-weekday+time picker, Interval picker, session-mode toggle, enable/disable, schedule/last/next display (FR-034).
- [X] T047 [P] [US4] [Test] Unit tests for `next_fire` + missed-run detection in `tests/unit/test_scheduler.py` (SC-006, SC-015).
- [X] T070 [US4] Implement `lmstudioclaw/orchestrator/memory.py` (agent learnings): let the agent persist durable learnings to the Documents `memory/` area and load relevant learnings into a session within the token budget (esp. persistent-session automations); never include secrets (FR-065, FR-066, SC-017). Expose the persist/recall as registry tools routed through the consent gate where applicable.

**Checkpoint**: Unattended scheduled automations run, notify, and report misses.

---

## Phase 7: User Story 5 — Add and use custom skills (Priority: P2)

**Goal**: Discover `SKILL.md` skills (+ referenced scripts), enable/disable, agent uses them.

**Independent Test**: Drop a valid skill folder; it appears and is usable; invalid `SKILL.md` shows
invalid; referenced script is invokable.

- [X] T048 [US5] Implement `lmstudioclaw/capabilities/skills.py`: scan skills folder, parse `SKILL.md` metadata, list referenced scripts, mark invalid ones (FR-010, FR-011, FR-017).
- [X] T049 [US5] Wire enabled skills into `registry.py` so instructions are injected within the token budget and referenced scripts are invokable (FR-012, FR-067).
- [X] T050 [US5] Implement capability listing/enable/disable + `POST /api/capabilities/refresh` in `web/api.py` for skills (FR-011).
- [X] T051 [P] [US5] Build the Settings → Skills SPA section in `web/static/` (list, metadata, validity, enable/disable) (FR-011).
- [X] T052 [US5] [Test] Integration test: add valid skill → appears+usable; malformed → invalid, in `tests/integration/test_skills.py` (SC-007).

**Checkpoint**: Skills are pluggable and usable by sessions and automations.

---

## Phase 8: User Story 6 — Add MCP servers and custom tools (Priority: P2)

**Goal**: Add MCP servers (`mcp.json`) and custom Python tools (with arbitrary-code trust gate);
both callable by the agent.

**Independent Test**: Add an MCP server → its tools callable; add a custom tool → warning + trust
confirm required before enable, then callable.

- [X] T053 [US6] Implement `lmstudioclaw/capabilities/mcp_client.py`: connect to MCP servers from `mcp.json` via the `mcp` SDK; expose tools; report connect failures (FR-013, FR-017).
- [X] T054 [US6] Implement `lmstudioclaw/capabilities/tools.py`: load custom Python tools, enforce arbitrary-code warning + trust confirmation before enable, in-process exec with timeout + exception capture (FR-014, FR-015, FR-018).
- [X] T055 [US6] Extend `registry.py` to offer MCP + custom tools to the engine and route their file I/O through the consent gate (FR-009, FR-016).
- [X] T056 [US6] Implement `POST /api/capabilities/mcp`, capability enable/disable/trust in `web/api.py`; agent-initiated capability authoring path (FR-079) sharing the same code; secret values only via secrets endpoint (FR-078).
- [X] T057 [P] [US6] Build the Settings → MCP Servers & Tools SPA section + secrets entry UI (write-only values; list ref-names only) in `web/static/` (FR-015, FR-076–FR-078).
- [X] T058 [P] [US6] Implement `GET /api/secrets`, `PUT /api/secrets/{ref}`, `DELETE /api/secrets/{ref}` in `web/api.py` (list ref-names only, write-only values) (FR-076–FR-078).
- [X] T059 [US6] [Test] Integration test: MCP add → tools callable; custom tool → trust gate enforced; secret never readable by agent but capability connects, in `tests/integration/test_tools_mcp_secrets.py` (SC-008, SC-021, SC-022).

**Checkpoint**: MCP servers and custom tools are pluggable (by user and agent); secrets isolated.

---

## Phase 9: User Story 7 — Configure the application (Priority: P3)

**Goal**: Settings for theme, default model, startup, notifications, web UI, idle/unload, retention,
personas, and Advanced → Model Management.

**Independent Test**: Switch theme dark/light/system; set default model; set per-model context in
Advanced and verify it applies on load.

- [X] T060 [US7] Implement `GET/PATCH /api/settings` + `GET /api/models` + model load/unload/warmup/context-pref endpoints in `web/api.py` (FR-003, FR-044–FR-052, FR-046).
- [X] T061 [US7] Implement persona management endpoints `GET/POST/PATCH/DELETE /api/personas` in `web/api.py` (default editable, cannot delete default) (FR-072, FR-075).
- [X] T062 [US7] Implement `POST /api/open-in-vscode` (invoke `code <path>`, clear error if unavailable) in `web/api.py` (FR-074); wire file links in SPA to it (SC-020).
- [X] T063 [P] [US7] Build the Settings SPA: theme (dark/light/system), default model, startup, notifications, web/connection, idle/timeouts/retention/compression, Personas library, and **Advanced → Model Management** (per-model context, manual load/unload/warmup) (FR-044–FR-052, FR-046, FR-072, SC-010, SC-019).
- [X] T064 [US7] [Test] Integration test: theme persist, default model applied, per-model context applied on load in `tests/integration/test_settings.py` (SC-010).

**Checkpoint**: Full configuration surface; existing model-management preserved under Advanced.

---

## Phase 10: Polish & Cross-Cutting Concerns

**Purpose**: Documentation, resilience, edge cases, and final validation.

- [X] T065 [P] Create `ARCHITECTURE.md`: modules/responsibilities, data/control flow, LM Studio + agent-config integration points, invariants, and extension points (Constitution VI).
- [X] T066 [P] Update [README.md](../../README.md) (new call-based agent overview, install/run) and [AGENTS.md](../../AGENTS.md) (new architecture, reconcile the "two Markdown files only" rule to allow `ARCHITECTURE.md` per constitution v1.1.0); fix the stale path/venv in [lmstudio.bat](../../lmstudio.bat).
- [X] T067 Implement edge-case handling: LM Studio unavailable, model load OOM/timeout, stop mid-tool/load, compression-cannot-reduce, web-port-in-use fallback, malformed `mcp.json`/skill (spec Edge Cases; FR-017, FR-054, FR-055).
- [X] T068 [P] Add docstrings to all public functions/classes/modules and inline comments for non-obvious logic; verify no file exceeds ~500 meaningful lines, split if needed (Constitution I, VI).
- [X] T069 Run the full quickstart validation in [quickstart.md](quickstart.md) (all 10 flows) and confirm Success Criteria SC-001…SC-022; fix gaps.

---

## Dependencies & Execution Order

- **Setup (T001–T005)** → blocks everything.
- **Foundational (T006–T025)** → blocks all user stories. Within it: store/settings/secrets
  (T006–T008) → model (T009–T011) → consent (T012–T013) → budget/compaction/persona (T014–T017) →
  registry/engine/queue (T018–T021) → notifications/app/tray/web-base (T022–T025).
- **User stories**:
  - US1 (T026–T032) — MVP; depends only on Foundational.
  - US2 (T033–T037) — depends on Foundational (consent gate) + US1 session/WS plumbing.
  - US3 (T038–T040) — depends on US1 (sessions exist).
  - US4 (T041–T047, T070) — depends on US1 (engine/queue) + US2 (permanent grants for unattended); T070 (agent learnings) also depends on the budget/compaction modules (T014, T016).
  - US5 (T048–T052), US6 (T053–T059) — depend on Foundational registry + US1; independent of each other.
  - US7 (T060–T064) — depends on Foundational; mostly independent (can start early for Settings UI).
- **Polish (T065–T069)** — after the user stories it documents/validates.

## Parallel execution examples

- **Setup**: T003, T004 in parallel after T001.
- **Foundational**: T007, T008, T009 in parallel after T006; T013/T015/T021 tests parallel with their
  siblings; T022/T024 parallel with web base.
- **Within a story**: SPA view tasks marked [P] (T030, T036, T039, T046, T051, T057, T063) run parallel
  to their backend tasks; contract/integration tests [P] run alongside.
- **Across stories** (after Foundational): US5 and US6 backend work can proceed in parallel; US7
  Settings UI can be built in parallel with US3/US4.

## Implementation strategy

- **MVP first**: Setup + Foundational + **US1** delivers a working interactive agent that loads,
  runs, and unloads — demonstrable on its own.
- **Increment 2**: US2 (consent) hardens security — pair with US1 for a safe MVP.
- **Increment 3+**: US3 (sessions UI), US4 (automations), US5/US6 (capabilities), US7 (settings),
  each an independently testable slice.
- **Finish**: Polish (docs, edge cases, full quickstart validation).

## Format validation

All tasks use `- [ ] T### [P?] [US#?] description with file path`. Setup/Foundational/Polish carry no
story label; user-story tasks carry US1–US7; `[P]` marks parallelizable tasks on distinct files.

Total: **70 tasks** — Setup 5, Foundational 20, US1 7, US2 5, US3 3, US4 8, US5 5, US6 7, US7 5,
Polish 5.
