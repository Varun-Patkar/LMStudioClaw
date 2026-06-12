# Phase 0 Research: Call-Based LM Studio Agent Runtime

**Feature**: 001-agent-runtime | **Date**: 2026-06-12 | **Spec**: [spec.md](spec.md)

This document resolves the open technical decisions for the plan. Each entry records the
**Decision**, the **Rationale**, and the **Alternatives considered**.

---

## 1. Orchestration engine

**Decision**: Build a **lightweight custom orchestrator** in Python using the `openai` package
(against LM Studio's `/v1` OpenAI-compatible endpoint) for the agent turn/tool-call loop, plus the
official **`mcp` Python SDK** for MCP client connections.

**Rationale**:
- The user explicitly chose a lightweight custom orchestrator over Microsoft Agent Framework (MAF).
- LM Studio already exposes an OpenAI-compatible Chat Completions API with tool-calling; the
  `openai` client gives streaming + tool-call deltas with no extra abstraction.
- The `mcp` SDK is the standard, maintained client for MCP stdio/SSE servers — avoids hand-rolling
  the protocol (Constitution II: don't reinvent security-sensitive wire formats).
- Keeps the dependency surface small and the control flow explicit (steering, queuing, compression
  need fine-grained control of the turn loop that a heavy framework would obscure).

**Alternatives considered**:
- **Microsoft Agent Framework**: rich (MCP, approvals, sessions) but heavyweight; its turn loop is
  harder to interrupt for steering/queue/compression and adds a large dependency. Rejected per user.
- **LangChain / LlamaIndex agents**: large dependency trees, opinionated memory models that fight
  our explicit token-budget design. Rejected.

---

## 2. Web UI stack

**Decision**: **FastAPI + Uvicorn** serving a single-page control panel; **WebSocket** for live
session streaming/steering, REST for everything else. Frontend is a lightweight SPA (plain
TypeScript/Vanilla or a small framework) served as static files by FastAPI.

**Rationale**:
- The user chose a local web UI (tkinter dropped). FastAPI is async (fits the async orchestrator and
  MCP SDK), has first-class WebSocket support for streaming tokens and receiving steer/queue/stop
  events, and serves static assets simply.
- Single process: FastAPI app + tray + scheduler run in one Python process, so the tray "Open"
  action just launches the browser at the served URL.
- Pydantic (bundled with FastAPI) gives boundary validation for free (Constitution II: validate
  inputs at the boundary).

**Alternatives considered**:
- **Flask + SSE**: SSE is one-way; we need bidirectional (steer/queue/stop) → WebSocket is cleaner.
- **Electron / Tauri desktop app**: heavier, new toolchain, contradicts "local web UI" choice.
- **Streamlit / Gradio**: fast to build but poor fit for custom session controls, tray integration,
  and multi-page management.

**Open sub-decision (defer to implementation)**: whether the SPA uses a build step (Vite) or is
hand-authored static files. Either satisfies the contract; pick the lightest that supports the
session view's streaming UI.

---

## 3. System tray + lifecycle

**Decision**: Keep **pystray + Pillow** for the tray (already a dependency). The tray's primary
action opens the browser to the actual served URL; "Quit" stops Uvicorn, the scheduler, and unloads
any loaded model. The web server runs in the main process; the tray icon runs via
`pystray` `run_detached()`.

**Rationale**: Reuses proven code from the current app; matches the existing "tray ≠ quit" and
"open from tray" conventions in [AGENTS.md](../../AGENTS.md).

**Alternatives considered**: `infi.systray` (Windows-only, less maintained) — rejected, pystray works.

---

## 4. Automation scheduler

**Decision**: A **custom asyncio-based scheduler** inside the controller process that computes each
automation's next fire time (Daily: selected weekdays + time; Interval: every X min/hr/day) and
sleeps until the nearest one. On startup it performs **missed-run detection** by comparing each
automation's expected fire times against its `last_run` timestamp.

**Rationale**:
- The user accepted an in-app scheduler (app assumed running while PC is on).
- A custom scheduler keeps control over the FIFO single-session queue and missed-run reporting, and
  avoids a heavy dependency.
- Constitution V (no idle polling) tension: a scheduler is an inherent timer. Mitigation — it uses a
  **single event-driven sleep-until-next-fire** (not a busy poll loop), and there is still **no model
  loaded** while it waits. This is justified complexity (see plan Complexity Tracking).

**Alternatives considered**:
- **APScheduler**: capable, but adds a dependency and its job stores/threading model is more than we
  need for a handful of local automations. Could be adopted later; custom is sufficient for v1.
- **Windows Task Scheduler**: survives reboots, but the user explicitly preferred in-app scheduling
  and an always-on app; OS integration adds packaging complexity. Rejected for v1.

---

## 5. Context / token budgeting & compression

**Decision**: Track token usage with a **model-aware tokenizer estimate** (use the model's reported
context length from LM Studio; estimate tokens via a tokenizer such as `tiktoken` as an
approximation, falling back to a chars/4 heuristic when no tokenizer matches). Maintain a **budget
allocator** dividing the context window across: system/persona, skills, memory/learnings,
conversation, and live tool output. When usage crosses the configurable threshold (default ~90%),
**summarize older turns** via a compression call to the same model and replace them with the summary.

**Rationale**:
- Local models report `max_context_length` (already read by the current app); usage must be tracked
  to trigger compression before overflow (FR-061, FR-067, FR-068).
- A pluggable estimator avoids hard-coupling to any one tokenizer (models vary); approximation is
  acceptable because the threshold leaves headroom.
- Summarize-and-replace is the standard, model-agnostic compression approach and keeps persistent
  automation sessions affordable (FR-064).

**Alternatives considered**:
- **Exact tokenization per model**: ideal but infeasible across arbitrary local GGUF models; the
  approximation + headroom is robust enough.
- **Hard truncation (drop oldest)**: loses information that persistent sessions/learnings depend on.
  Rejected in favor of summarization.

---

## 6. Persistence / storage

**Decision**: **SQLite** (via stdlib `sqlite3`) for structured, queryable state — sessions, turns,
automations, grants, capability registry, notifications. **JSON/YAML files** for human-editable
config (settings, `mcp.json`) and the `SKILL.md`/tools/memory file areas. The **secrets store** is a
separate file under app-data (e.g. `%APPDATA%`), outside any agent-accessible path.

**Rationale**:
- Sessions/transcripts/automations are relational and grow over time; SQLite gives durable queries,
  retention pruning, and crash safety without a server (Constitution V: no extra services).
- Config that users (and the agent) edit stays as plain files for transparency and plug-and-play
  (Constitution IV).
- Secrets isolation (FR-076–FR-078) requires the secrets file to live **outside** the Documents tree
  and never be added to a grant.

**Alternatives considered**:
- **All-JSON**: simple but poor for querying/pruning large session history and concurrent writes.
- **TinyDB / shelve**: weaker concurrency and query story than SQLite. Rejected.

---

## 7. Filesystem consent enforcement

**Decision**: A single **path-authorization gate** that every agent file operation must pass through.
It canonicalizes the target path (resolve symlinks + `..`), then checks it is the workspace or within
a granted folder (hierarchical: prefix match on the canonical granted root). Grants are session or
permanent; the secrets area and app internals are on a hard **deny-list** regardless of grants.

**Rationale**: Centralizing the check (one chokepoint) satisfies FR-019–FR-027, FR-069/FR-070,
FR-077 and prevents traversal/symlink escape (Constitution II). Hierarchical = canonical-prefix
match. Least-privilege is enforced by the agent's request tool asking for the narrowest path.

**Alternatives considered**: per-tool ad-hoc checks — error-prone, easy to bypass. Rejected: one gate.

---

## 8. Custom Python tools execution

**Decision**: Load user tools from the `tools/` folder as Python modules exposing a documented
contract (a callable + metadata). They run **in-process** with a per-call **timeout** and exception
capture, gated behind the **trust confirmation** (FR-015). All tool I/O still passes the path gate.

**Rationale**: In-process matches the user's "it's their machine" stance and keeps v1 simple; the
trust gate + arbitrary-code warning is the v1 safeguard (FR-015). Timeouts/exception capture satisfy
FR-017/FR-018. The future safety reviewer (FR-029) can inspect tool source because it stays on disk.

**Alternatives considered**: subprocess/sandbox isolation — deferred (Out of Scope) per spec; noted as
future hardening.

---

## 9. File-open-in-VS-Code

**Decision**: Open file references by invoking the VS Code CLI (`code <path>`); if unavailable, fall
back to a clear error message (FR-074).

**Rationale**: Simple, matches the user's editor assumption; no extension needed.

**Alternatives considered**: `os.startfile` (opens default app, not necessarily VS Code) — rejected,
user specified VS Code.

---

## 10. Notifications

**Decision**: Native **Windows toast** notifications (e.g. via `win10toast`-style or `winrt` toast
APIs) for automation-running, automation-missed, and run-completed/failed events (FR-042).

**Rationale**: Matches the existing Windows-only assumption and the spec's default channel.

**Alternatives considered**: in-UI-only notifications — insufficient when the browser tab is closed.

---

## 11. Resolved spec deferrals

- **Data retention default**: Decision — default **keep 90 days** of session history (configurable;
  FR-038/FR-051), pruned at startup and after each run. Rationale: bounded disk use without losing
  recent context; user can change it. Alternatives: keep-forever (unbounded growth) rejected as
  default.
- **Secret storage mechanism**: Decision — plaintext JSON in an app-data secrets file outside agent
  scope (per clarification). Rationale/again per FR-076–FR-078.

---

## Dependency summary (proposed; install commands, not pinned per Constitution IV)

| Concern | Library |
|--------|---------|
| Web server + validation | `fastapi`, `uvicorn`, `pydantic` |
| Model client (LM Studio /v1) | `openai` (existing) |
| LM Studio native API | `httpx` (existing) |
| MCP clients | `mcp` (official Python SDK) |
| Tray + icon | `pystray`, `pillow` (existing) |
| Token estimate | `tiktoken` (approximation; optional) |
| Windows toast | a Windows toast library (e.g. `winrt`/`win10toast`) |
| Config | `pyyaml` (existing), stdlib `json`, stdlib `sqlite3` |

All new dependencies are installed via terminal commands (no manual version pins) per Constitution IV
and the repo's dependency rule.
