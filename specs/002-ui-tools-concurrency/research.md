# Phase 0 Research: Professional UI, Default Agent Toolset & Single-Run Concurrency

**Feature**: 002-ui-tools-concurrency | **Date**: 2026-06-13 | **Spec**: [spec.md](spec.md)

The spec's 10 clarifications already resolved the **behavioral** questions. This document records the
**implementation-level** decisions, each as Decision / Rationale / Alternatives, grounded in the
existing `001-agent-runtime` codebase so no module is reinvented.

---

## 1. Live UI status — push channel vs polling

- **Decision**: Add a single global WebSocket `/ws/status` that broadcasts run/model/queue state
  changes (model `loading|ready|error|unloaded`, active-run identity/status, queue snapshot). The SPA
  shell subscribes once and updates the top-right run indicator, queue panel, and the "Load model"
  button feedback live. The existing per-session `/ws/sessions/{id}` channel is unchanged.
- **Rationale**: Constitution V forbids idle polling/timers. A push channel reusing the existing
  `SessionHub` fan-out pattern gives instant, resource-free updates and removes the manual page reload
  the user complained about. One global channel keeps the client simple (one socket for app-wide
  status, one per open session).
- **Alternatives considered**: (a) Polling `/api/queue` + `/api/models` on a timer — rejected:
  violates Resource Frugality and is laggy. (b) Server-Sent Events — rejected: the project already
  standardizes on WebSockets; mixing transports adds surface for no gain. (c) Piggy-backing all
  status on the per-session socket — rejected: status must be visible on every page, including when no
  session socket is open.

## 2. Responsive layout strategy (≈90vw, no fixed narrow cap)

- **Decision**: Replace `#view { max-width: 1100px }` with a fluid container: `width: min(90vw,
  100%)` centered with ~5vw gutters, a CSS custom-property design-token system (spacing, radius,
  typography scale, elevation), CSS Grid/Flex for component layouts, and a small set of responsive
  breakpoints that collapse the top nav into a compact menu on narrow widths. No CSS framework.
- **Rationale**: The user explicitly wants ~90vw content and called the 1100px cap "outrageous".
  Plain CSS honors Resource Frugality (no framework/bundler) and fits the existing static-file serving.
  Design tokens give the "consistent visual system" (FR-001) cheaply and keep dark/light/system theming
  (already present) working.
- **Alternatives considered**: (a) Adopt Tailwind/Bootstrap — rejected: adds a build step + dependency
  weight against Constitution V and the project's framework-free SPA. (b) Keep a max-width but raise it
  — rejected: the user wants viewport-proportional width, not a bigger fixed column.

## 3. The `edit` tool — overloaded targeting

- **Decision**: One `edit` tool exposing two mutually-exclusive modes via parameters:
  - **exact-string mode**: `{path, old_string, new_string}` — `old_string` must occur **exactly once**;
    otherwise the call fails (not found / ambiguous) and the file is untouched.
  - **line-range mode**: `{path, start_line, end_line, new_content}` — replaces that inclusive 1-based
    range; out-of-bounds fails cleanly without writing.
  The tool reads the file, validates, writes atomically (temp file + replace), and routes through the
  consent gate. Tool description instructs the agent to read the relevant section before editing.
- **Rationale**: Matches the clarified decision (Q4): line-range is token-efficient for whole-line/block
  edits; exact-string handles sub-line/cross-line spans. Unique-match-or-fail prevents silent wrong
  edits. Atomic write protects against partial corruption (Constitution II best-effort writes).
- **Alternatives considered**: (a) Exact-string only — rejected: the user wants line-range for
  efficiency. (b) Line-range only — rejected: can't express mid-line/cross-line spans. (c) Two separate
  tools — rejected: one overloaded tool is a smaller, clearer surface for the model.

## 4. PowerShell tool — sandboxing within the consent model

- **Decision**: A `powershell` tool runs `pwsh`/`powershell.exe` with the working directory set to the
  agent **workspace**, a per-call timeout, and truncated stdout/stderr. The command string is passed as
  a single argument (`-Command`) — no interpolation of untrusted text into an outer shell. Before
  running, the tool registers a consent check anchored at the workspace; if the agent's command targets
  paths outside currently consented folders, the same consent prompt used by file tools is raised, and
  a permanent grant is persisted as a user-consented folder (reusing `PathGate`). The secrets store and
  app-internal areas remain on the hard deny-list.
- **Rationale**: Implements clarification Q1 (PowerShell shares the file-tool consent model). Setting
  cwd to the workspace + timeout + truncation bounds blast radius and prevents runaway output
  (Constitution II/V). Passing the command as one argument avoids meta-shell injection.
- **Practical limit**: A shell can technically reach any path the user can; the gate cannot intercept
  every in-process file op a child process performs. The design therefore (i) anchors cwd to the
  workspace, (ii) reuses `PathGate` for explicit path arguments the agent declares, and (iii) keeps the
  hard deny-list for secrets/internals. Deeper OS-level sandboxing (job objects, AppContainer) is noted
  as a future hardening item, out of scope here — consistent with the project's single-user-machine
  threat model and the 001 secrets-isolation guarantee (secrets never live under any agent-reachable
  path regardless).
- **Alternatives considered**: (a) Full unbounded shell — rejected: breaks the consent guarantee the
  spec requires. (b) Per-command approval for every invocation — rejected by the user (blocks
  unattended automations). (c) No shell tool — rejected: the user explicitly requires `powershell`.

## 5. The `parallel` meta-tool

- **Decision**: Declare a `parallel` tool with parameter `{calls: [{tool, arguments}, …]}` (length ≥2).
  The engine executes the listed sub-calls concurrently via `asyncio.gather`, each still dispatched
  through `registry.invoke_tool` (so consent + per-call timeout apply per sub-call), and returns an
  array of results keyed by index. The tool description states it is only for **independent** calls and
  must not be used for concurrent operations on the same target (e.g., two edits to one file).
- **Rationale**: Implements clarification Q2 (explicit meta-tool, not native parallel tool-calling).
  Reusing `invoke_tool` per sub-call keeps consent/timeout/security uniform. `asyncio.gather` is the
  natural concurrency primitive in the existing async engine.
- **Same-target safety**: The tool does not attempt automatic conflict detection beyond rejecting an
  obvious duplicate-path write/edit pair; correctness for independent calls is the contract, and the
  description steers the agent. (Heuristic conflict detection beyond duplicate write/edit targets is out
  of scope.)
- **Alternatives considered**: (a) Rely on the model's native multi-tool-call — rejected by the user
  (uncertain model support). (b) A thread pool — rejected: the engine and tools are already async;
  `asyncio.gather` avoids thread-marshalling overhead.

## 6. Persisted, resumable run queue

- **Decision**: Add a `queued_runs` SQLite table holding the serialized run request (trigger type,
  run config, position, created_at, started flag). On enqueue, write the row (best-effort); on
  dequeue/cancel/completion, update/remove it. On startup, `SessionQueue` restores not-yet-started
  rows in order and re-enqueues them; an interrupted in-progress run is detected (a `loading|active`
  session with no live process) and either resumed or re-queued, while truly un-resumable manual turns
  are recorded and surfaced. Automation misses continue to use the existing scheduler missed-run logic.
- **Rationale**: Implements clarification (Q on persistence — fully persisted). SQLite is already the
  state store; `store.py` is best-effort by design, so a write hiccup never crashes the controller
  (Constitution II). Restoring on startup means no queued work is silently lost.
- **Alternatives considered**: (a) In-memory only — rejected by the user. (b) A separate queue file
  (JSON) — rejected: duplicates persistence machinery; SQLite already provides atomic, queryable state
  alongside sessions. (c) Full mid-turn checkpointing of conversation state — deferred: resuming a
  queued (not-yet-started) run is sufficient for the requirement; mid-turn replay is a larger effort
  and not requested.

## 7. Per-run configuration & override resolution

- **Decision**: Introduce a `RunConfig` dataclass `{model?, tool_overrides: {name: bool}, mcp_selection:
  [server_ids]?}`. Sessions and automations carry an optional `RunConfig` (persisted with the automation;
  attached to the session row / queued run for a manual start and follow-ups). The `CapabilityRegistry`
  gains a per-run "effective toolset" computation: **MCP selection decides active servers first**, then
  **per-tool overrides apply on top** of the resulting (built-in + MCP) tool set — most-granular-wins.
  Absent config → global defaults. De-selecting an MCP for a run scopes it out of that run only (no
  global change). Per-session choices persist for the session until a follow-up changes them. A
  reference to a removed tool/MCP is ignored with a noted warning rather than failing the run.
- **Rationale**: Implements clarifications Q3 (independent per-run overrides) and the precedence Q
  (most-granular-wins; MCP de-selection is run-scoped; session-persistent until follow-up). The engine
  already calls `registry.enabled_tools()` each turn, so injecting a per-run effective set is a minimal,
  localized change. Skills are excluded from per-run config (they cost no idle tokens and load on demand).
- **Alternatives considered**: (a) MCP-selection-final precedence — rejected by the user (wanted the
  finest decision to win). (b) Mutating global config per run — rejected: violates the "global unchanged"
  requirement (FR-028). (c) Most-restrictive-wins (AND of layers) — rejected: can't express "keep server,
  drop one of its tools" as cleanly as the chosen ordering.

## 8. Tool naming & consent reuse

- **Decision**: Keep one descriptive toolset. Reuse existing `read_file` / `list_dir` / `write_file`
  (extending `read_file` with optional `start_line`/`end_line` or byte range), and add `edit`, `grep`,
  `find`, `powershell`, `parallel`. All file-accessing tools continue to route through `PathGate`
  exactly as today. New tool handlers move to `file_tools.py` / `shell_tool.py` / `parallel_tool.py`
  so `registry.py` stays under the modularity limit.
- **Rationale**: Implements clarification Q5 (descriptive names, reuse where present, not aliases).
  Splitting handlers out of `registry.py` respects Constitution I as the tool count grows.
- **Alternatives considered**: (a) Rename to short forms (`read`/`ls`/`write`) — rejected by the user
  (favor existing descriptive names). (b) Alias layer — rejected (the user explicitly does not want
  backward-compat aliases). (c) Keep all handlers in `registry.py` — rejected: pushes it past ~500 lines.

---

## Resolved unknowns

All Technical Context fields are concrete; **no NEEDS CLARIFICATION remain**. No new runtime
dependencies are required — every decision reuses an existing module (FastAPI/WebSocket hub, `PathGate`,
`SessionQueue`, `CapabilityRegistry`, `Engine`, SQLite `store`, static SPA).
