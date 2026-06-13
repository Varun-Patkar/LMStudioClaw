# Tasks: Professional UI, Default Agent Toolset & Single-Run Concurrency

**Input**: Design documents from `/specs/002-ui-tools-concurrency/`

**Prerequisites**: [plan.md](plan.md) (required), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/delta-api.md](contracts/delta-api.md),
[quickstart.md](quickstart.md)

**Tests**: Included — the plan and quickstart specify focused unit/integration/contract tests, and the
codebase already maintains a passing suite (45 tests). New tests target the new tools, run config, and
persisted queue.

**Organization**: Tasks are grouped by user story (P1×3, P2×1) so each story is an independently
testable increment. This feature **extends** the existing `lmstudioclaw/` package (feature 001); paths
are real.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: US1 (UI), US2 (toolset), US3 (concurrency/queue), US4 (per-run config)
- Every task includes an exact file path

## Path Conventions

Single Python package at repo root: `lmstudioclaw/` (source), `tests/` (pytest). The browser SPA lives
under `lmstudioclaw/web/static/`.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Prepare the new module files and confirm the baseline suite is green before changes.

- [x] T001 Run `pytest` to confirm the existing 45-test baseline passes before changes (no file change; record result).
- [x] T002 [P] Create empty module skeletons with module docstrings: `lmstudioclaw/capabilities/file_tools.py`, `lmstudioclaw/capabilities/shell_tool.py`, `lmstudioclaw/capabilities/parallel_tool.py`.
- [x] T003 [P] Create empty SPA widget skeleton `lmstudioclaw/web/static/views/runbar.js` with a top-of-file comment describing the run indicator + queue panel.

**Checkpoint**: New files exist and the suite is green.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Shared building blocks every story depends on — the `RunConfig` model, the persisted-queue
table, the global status channel, and the registry's per-run effective-toolset hook. **No user story
work begins until this phase is complete.**

- [x] T004 Define the `RunConfig` dataclass and (de)serialization (`{model?, tool_overrides: dict[str,bool], mcp_selection: list[str]|None}`) in `lmstudioclaw/capabilities/registry.py` (or a small `run_config.py` if registry nears the 500-line limit), per [data-model.md](data-model.md) §1.
- [x] T005 Implement the per-run **effective-toolset** resolution (most-granular-wins: MCP selection → per-tool overrides over built-in + MCP tools; globals never mutated; unknown refs warned not failed) as a method on `CapabilityRegistry` in `lmstudioclaw/capabilities/registry.py` (FR-028/FR-030a/FR-033).
- [x] T006 Add the persisted-queue table `queued_runs` (id, trigger_type, automation_id, run_config json, initial_message, position, started, created_at) and best-effort CRUD helpers to `lmstudioclaw/sessions/store.py`, per [data-model.md](data-model.md) §3.
- [x] T007 Extend `lmstudioclaw/sessions/queue.py` so `enqueue`/`cancel`/dequeue/completion persist via the store, and add `restore_from_store()` that re-enqueues not-yet-started rows in `position` order on startup (FR-025a).
- [x] T008 Add a global status hub + `/ws/status` endpoint to `lmstudioclaw/web/ws.py`: broadcast `model_status` / `run_status` / `queue` events and send a full snapshot on (re)connect (FR-005/FR-007/FR-024), per [contracts/delta-api.md](contracts/delta-api.md) §4. No polling.
- [x] T009 Wire the global status hub into the `Controller` in `lmstudioclaw/app.py`: emit status broadcasts on model load/unload and on queue/run transitions; call `queue.restore_from_store()` during startup (FR-005/FR-025a).
- [x] T010 [P] Add a status-socket client + run-config request helpers to `lmstudioclaw/web/static/api.js` (subscribe to `/ws/status`, expose model/run/queue state to views).
- [x] T011 [P] Unit test the persisted queue (persist/restore/resume, cancel removes row) in `tests/unit/test_queue.py` (extend existing).

**Checkpoint**: Run config, persisted queue, status channel, and registry hook are in place — stories can proceed.

---

## Phase 3: User Story 1 — Professional, live-updating, responsive UI (Priority: P1) 🎯 MVP

**Goal**: A polished, fluid ~90vw, responsive UI that reflects model/run status live (no manual reload)
with a non-blocking loader.

**Independent Test**: Resize narrow→wide (content ≈90vw, no horizontal scroll); click "Load model" and
see an immediate loader then a live status transition without reloading the page.

### Tests for User Story 1

- [x] T012 [P] [US1] Contract test the `/ws/status` event shapes (model_status/run_status/queue + reconnect snapshot) in `tests/contract/test_sessions_contract.py` (extend) per [contracts/delta-api.md](contracts/delta-api.md) §4.

### Implementation for User Story 1

- [x] T013 [US1] Overhaul `lmstudioclaw/web/static/app.css`: remove `#view { max-width: 1100px }`, set fluid `width: min(90vw, 100%)` with ≈5vw gutters; add design tokens (spacing/radius/type scale/elevation) and consistent button/card/table/badge components (FR-001/FR-002).
- [x] T014 [US1] Add responsive breakpoints in `lmstudioclaw/web/static/app.css`: collapse the top nav to a compact menu on narrow widths; keep run indicator + primary controls reachable, no horizontal scroll (FR-003).
- [x] T015 [US1] Update `lmstudioclaw/web/static/index.html` only if structural hooks are needed (e.g., a `#runbar` mount + compact-nav toggle); keep markup minimal.
- [x] T016 [US1] In `lmstudioclaw/web/static/app.js`: subscribe to the status socket on shell load, mount the run indicator, and apply live model/run status app-wide (FR-005); preserve dark/light/system theming consistently (FR-006).
- [x] T017 [US1] In `lmstudioclaw/web/static/views/settings.js` (model management) and the load control: show a non-blocking progress indicator + busy state on "Load model" submit and clear it from live `model_status` events — no page reload (FR-004/FR-005, SC-001).
- [x] T018 [US1] Ensure the status socket client reconnects and re-renders from the snapshot after a dropped channel in `lmstudioclaw/web/static/api.js` + `app.js` (FR-007).

**Checkpoint**: UI is professional, fluid ~90vw, responsive, and live — US1 demoable on its own.

---

## Phase 4: User Story 2 — Default agent toolset with file-aware read/edit (Priority: P1)

**Goal**: The agent has read_file(range)/list_dir/write_file/edit/grep/find/powershell/parallel, all
consent-gated, with precise overloaded edits.

**Independent Test**: In a session, exercise find/grep/read-range/edit(exact)/edit(line-range)/write/
ls/powershell/parallel; a targeted edit changes only the intended section; consent gate blocks
unconsented paths.

### Tests for User Story 2

- [x] T019 [P] [US2] Unit-test file tools (read range, edit exact-string unique-or-fail, edit line-range out-of-bounds, write makes parents, grep, find, list_dir) in `tests/unit/test_file_tools.py`.
- [x] T020 [P] [US2] Unit-test the PowerShell tool (workspace cwd, timeout, output truncation, non-zero exit surfaced, consent prompt outside consented paths, secrets denied) in `tests/unit/test_shell_tool.py`.
- [x] T021 [P] [US2] Unit-test the `parallel` meta-tool (≥2 independent calls run concurrently; results indexed; duplicate write/edit-target pair rejected) in `tests/unit/test_parallel_tool.py`.

### Implementation for User Story 2

- [x] T022 [P] [US2] Implement `read_file` range support (`start_line`/`end_line`), `write_file` (create/overwrite, creating parent folders), and `grep`/`find`, plus move/extend the existing read/list/write handlers into `lmstudioclaw/capabilities/file_tools.py`, all routed through `PathGate` (FR-009/FR-011/FR-012/FR-013/FR-015).
- [x] T023 [P] [US2] Implement the overloaded `edit` tool (exact-string find/replace, unique-or-fail; line-range replace, bounds-checked; atomic temp-file write; consent-gated) in `lmstudioclaw/capabilities/file_tools.py` (FR-010, Edge Cases).
- [x] T024 [P] [US2] Implement the consent-gated `powershell` tool (workspace cwd, `-Command` single-arg, per-call timeout, truncated stdout/stderr, exit-code surfaced, `PathGate` for declared out-of-workspace paths) in `lmstudioclaw/capabilities/shell_tool.py` (FR-014/FR-015a).
- [x] T025 [US2] Implement the `parallel` meta-tool (`{calls:[{tool,arguments}]}`, len≥2, `asyncio.gather` dispatching each sub-call through `registry.invoke_tool` so consent/timeout apply; reject same-target write/edit pairs) in `lmstudioclaw/capabilities/parallel_tool.py` (clarification Q2).
- [x] T026 [US2] Register the full default toolset in `CapabilityRegistry._builtin_tools()` in `lmstudioclaw/capabilities/registry.py`, with clear descriptions/params (incl. read-before-edit guidance) per [contracts/delta-api.md](contracts/delta-api.md) §6 (FR-008/FR-016/FR-017).
- [x] T027 [US2] Add read-before-edit guidance to the agent system prompt/tool descriptions via `Controller._build_system_prompt` in `lmstudioclaw/app.py` (or persona default) (FR-016).

**Checkpoint**: All eight default tools usable and consent-gated; targeted edits are precise — US2 demoable.

---

## Phase 5: User Story 3 — Single-run concurrency with visible run & queue surface (Priority: P1)

**Goal**: At most one active run; a top-right indicator (click → running session) and a collapsible
queue panel shown only when non-empty; the queue persists across restarts.

**Independent Test**: Start a run (indicator shows it; click navigates to the session); start a second
session + an automation while it runs (both queue FIFO, exactly one active); drain queue (panel hides);
restart app with items queued (queue restored).

### Tests for User Story 3

- [x] T028 [P] [US3] Integration-test single-active enforcement + FIFO ordering + auto-start of next item + cancel, driving the queue/status surfaces, in `tests/integration/test_session_lifecycle.py` (extend) (FR-018/FR-019/FR-020/FR-025).

### Implementation for User Story 3

- [x] T029 [US3] Implement the top-right run indicator + collapsible queue panel (shown only when non-empty; FIFO order; type/label) in `lmstudioclaw/web/static/views/runbar.js`, fed by the status socket (FR-021/FR-023/FR-024, SC-006).
- [x] T030 [US3] Indicator click navigates to the running session view (manual or automation) with full controls (stop/steer/queue/transcript) in `lmstudioclaw/web/static/views/runbar.js` + `sessions.js` (FR-022).
- [x] T031 [US3] For an automation run, show the automation's definition alongside the session with an edit affordance (and keep it editable from the Automations list) in `lmstudioclaw/web/static/views/sessions.js` + `automations.js` (FR-022).
- [x] T032 [US3] Extend `GET /api/queue` snapshot items with `trigger_type` + `label` for the run/queue surface in `lmstudioclaw/web/routes_sessions.py` per [contracts/delta-api.md](contracts/delta-api.md) §3 (FR-023).
- [x] T033 [US3] Ensure firing automations enqueue through the same single-active queue and broadcast run/queue status in `lmstudioclaw/app.py` (`enqueue_automation`) + `lmstudioclaw/automations/scheduler.py` wiring (FR-018/FR-019/FR-024).
- [x] T034 [US3] On startup, restore the persisted queue (re-enqueue not-yet-started runs in FIFO order) and reconcile an interrupted in-progress run — re-queue where it can be safely restarted, else record as interrupted and surface it — in `lmstudioclaw/app.py` startup + `lmstudioclaw/sessions/queue.py` (FR-025a, Edge Cases).

**Checkpoint**: Concurrency is enforced and fully visible; queue survives restart — US3 demoable.

---

## Phase 6: User Story 4 — Per-run configuration for sessions and automations (Priority: P2)

**Goal**: Sessions (and follow-ups) and automations accept a run config (model, per-run tool overrides,
MCP selection); globals never change; skills stay global.

**Independent Test**: Start a session with a non-default model + tool overrides (disable a global-on,
enable a global-off) + MCP subset; the run uses exactly that; globals unchanged after; precedence
keeps a server while dropping one of its tools.

### Tests for User Story 4

- [x] T035 [P] [US4] Unit-test run-config precedence (MCP→tool, defaults when absent, globals immutable, session-persistence, stale-ref warn) in `tests/unit/test_run_config.py`.
- [x] T036 [P] [US4] Integration-test a session + automation applying per-run model/tools/MCP with globals unchanged in `tests/integration/test_run_config_flow.py`.

### Implementation for User Story 4

- [x] T037 [US4] Accept optional `run_config` on `POST /api/sessions` (and follow-up new-run) in `lmstudioclaw/web/routes_sessions.py` with Pydantic validation per [contracts/delta-api.md](contracts/delta-api.md) §1 (FR-026/FR-032).
- [x] T038 [US4] Accept/persist `run_config` on `POST/PATCH /api/automations` and include it in `GET /api/automations`, with `model_override` back-compat, in `lmstudioclaw/web/routes_automations.py` per [contracts/delta-api.md](contracts/delta-api.md) §2 (FR-027/FR-033).
- [x] T039 [US4] Thread `RunConfig` through `Controller.start_manual_session` + `enqueue_automation` and into the session row / queued run in `lmstudioclaw/app.py`; resolve model from `run_config.model` when present (FR-026/FR-027/FR-029/FR-030b).
- [x] T040 [US4] Apply the per-run effective toolset where the engine collects tools (`engine.py` line ~208 `enabled_tools()`), passing the run's `RunConfig` so overrides + MCP selection take effect for that run only in `lmstudioclaw/orchestrator/engine.py` (FR-028/FR-030/FR-030a).
- [x] T041 [P] [US4] Build the run-config form (model select, per-tool enable/disable, MCP multi-select; skills NOT shown) in `lmstudioclaw/web/static/views/sessions.js` (session start/follow-up) (FR-026/FR-031).
- [x] T042 [P] [US4] Add the same run-config editor to automation create/edit in `lmstudioclaw/web/static/views/automations.js` (FR-027/FR-031).

**Checkpoint**: Per-run model/tools/MCP work for sessions + automations; globals untouched — US4 demoable.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Documentation, edge cases, and final verification across all stories.

- [x] T043 [P] Update `ARCHITECTURE.md` for the new tools (file_tools/shell_tool/parallel_tool), the `/ws/status` channel, the persisted `queued_runs` queue, the run/queue UI surface, and `RunConfig` (Constitution VI).
- [x] T044 [P] Update `AGENTS.md` conventions: new default toolset + consent-gated PowerShell, single-active persisted queue, per-run config precedence (most-granular-wins) (Constitution VI).
- [x] T045 [P] Verify edge cases from [spec.md](spec.md): channel-loss recovery, model-load failure surfaced, edit not-found/ambiguous, powershell hang/timeout, stale per-run reference, queued-item cancel, very small viewport, all-tools-disabled run.
- [x] T046 Run `pytest` (full suite) and walk [quickstart.md](quickstart.md) US1–US4 manually; confirm SC-001..SC-008.

---

## Dependencies & Execution Order

### Phase dependencies

- **Setup (Ph1)** → no deps; do first.
- **Foundational (Ph2)** → depends on Setup; **blocks all user stories** (RunConfig, persisted queue, `/ws/status`, registry hook).
- **US1 (Ph3)**, **US2 (Ph4)**, **US3 (Ph5)** are all P1 and, once Ph2 is done, are largely independent (US1=CSS/SPA, US2=tool handlers, US3=queue UI/wiring). They can proceed in parallel by area.
- **US4 (Ph6, P2)** depends on Ph2 (RunConfig) and is cleanest after US2 (tools exist to override) and US3 (queue carries run config).
- **Polish (Ph7)** → after the stories it documents/verifies.

### Story independence

- **US1** (UI look/feel/live status) — independently testable via the browser + `/ws/status` contract test.
- **US2** (toolset) — independently testable via tool unit tests + a session.
- **US3** (concurrency/queue) — independently testable via the lifecycle integration test + restart.
- **US4** (per-run config) — independently testable via run-config unit/integration tests.

### Suggested MVP

**US1 + US2 + US3** (all P1) form the MVP: a professional live UI, a capable consent-gated toolset, and
visible/durable single-run concurrency. **US4** (P2) is the first increment after MVP.

---

## Parallel Execution Examples

- **Setup**: T002 and T003 in parallel (different files); T001 first.
- **Foundational**: T010 and T011 in parallel after their deps (T010 client/api.js, T011 queue test);
  T004→T005 sequential (same file), T006→T007 sequential (store→queue), T008→T009 sequential (ws→app).
- **US2 tools**: T022, T023, T024 in parallel (file_tools vs file_tools vs shell_tool — note T022/T023
  share `file_tools.py`, so coordinate or sequence those two), T025 (parallel_tool.py) independent;
  tests T019/T020/T021 in parallel up front.
- **US4 UI**: T041 and T042 in parallel (sessions.js vs automations.js).
- **Polish**: T043, T044, T045 in parallel (docs vs docs vs verification).

---

## Coverage Summary

| Story | Tasks | FRs covered |
|-------|-------|-------------|
| Foundational | T004–T011 | FR-005, FR-007, FR-024, FR-025a, FR-028, FR-030a, FR-033 |
| US1 (UI) | T012–T018 | FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007 |
| US2 (tools) | T019–T027 | FR-008–FR-017, FR-015a |
| US3 (concurrency) | T028–T034 | FR-018, FR-019, FR-020, FR-021, FR-022, FR-023, FR-024, FR-025, FR-025a |
| US4 (per-run config) | T035–T042 | FR-026–FR-033 |
| Polish | T043–T046 | Edge cases, SC-001..SC-008, Constitution VI |

**Total tasks**: 46 — Setup 3, Foundational 8, US1 7, US2 9, US3 7, US4 8, Polish 4.

All format rules honored: every task has a checkbox, sequential ID, optional `[P]`, story label on
user-story tasks (none on Setup/Foundational/Polish), and an exact file path.
