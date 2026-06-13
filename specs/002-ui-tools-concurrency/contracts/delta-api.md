# Contract Delta: REST + WebSocket additions for 002

**Feature**: 002-ui-tools-concurrency | **Date**: 2026-06-13 | **Spec**: [spec.md](../spec.md)

This is a **delta** over the 001 contract
([../../001-agent-runtime/contracts/http-api.md](../../001-agent-runtime/contracts/http-api.md)). All
prior endpoints, security rules (localhost-only, no secret values ever returned, Pydantic boundary
validation) remain in force. Only additions and changed shapes are listed here.

---

## 1. Run configuration on session start (CHANGED)

`POST /api/sessions` — body extended with an optional `run_config`.

```jsonc
// Request
{
  "model": "qwen2.5-coder",          // optional — back-compat top-level model still accepted
  "persona_id": "…",                 // optional (001)
  "run_config": {                    // NEW, optional
    "model": "qwen2.5-coder",        // overrides top-level/default if present
    "tool_overrides": {              // independent of global config
      "powershell": false,           // disable a globally-enabled tool for this run
      "grep": true                   // enable a globally-disabled tool for this run
    },
    "mcp_selection": ["server-a"]    // null=all enabled, []=none, [ids]=only these
  }
}
// Response (unchanged shape)
{ "session_id": "…", "queue_position": 0 }
```

**Semantics**: If `run_config` is absent → global defaults (FR-032). Unknown tool/MCP references are
ignored with a noted warning, not an error (FR-033). The global configuration is never mutated (FR-028).
A follow-up that starts a new run MAY supply a fresh `run_config` (FR-026).

---

## 2. Run configuration on automations (CHANGED)

`POST /api/automations` and `PATCH /api/automations/{id}` — body accepts a `run_config` (same shape as
above), persisted with the automation and applied on each scheduled run (FR-027).

```jsonc
{
  "name": "Nightly summary",
  "schedule": { "type": "daily", "days": ["mon","wed","fri"], "time": "09:00" },
  "session_mode": "persistent",
  "run_config": { "model": "…", "tool_overrides": { … }, "mcp_selection": [ … ] }
}
```

`GET /api/automations` list entries additionally include `run_config` so the UI can render/edit it.
The legacy `model_override` is treated as `run_config.model` when `run_config` is absent (back-compat).

---

## 3. Queue surface (CONFIRMED + persisted semantics)

Existing endpoints keep their shapes; persistence semantics are added (FR-025a):

| Method | Path | Purpose | Notes |
|--------|------|---------|-------|
| GET | `/api/queue` | FIFO snapshot (active + waiting) | now reflects **persisted** queue restored on startup |
| DELETE | `/api/queue/{id}` | Cancel a not-yet-started item | removes the persisted `queued_runs` row (FR-025) |

`GET /api/queue` snapshot item shape (extended with type/label for the UI run/queue surface):
```jsonc
[
  { "id": "…", "state": "active",  "trigger_type": "manual",     "label": "…" },
  { "id": "…", "state": "queued",  "trigger_type": "automation", "label": "Nightly summary" }
]
```

---

## 4. Global live-status channel (NEW)

`WS /ws/status` — one app-wide channel the SPA shell subscribes to for the top-right run indicator,
the collapsible queue panel, and live "Load model" feedback. No request body; localhost-only.

**Server → client events**:
```jsonc
{ "type": "model_status", "status": "idle|loading|ready|error|unloaded", "model": "…", "reason": "…?" }
{ "type": "run_status",   "active": { "id": "…", "trigger_type": "manual|automation",
                                       "status": "loading|active|completed|failed|stopped",
                                       "label": "…" } | null }
{ "type": "queue",        "items": [ { "id": "…", "state": "queued",
                                       "trigger_type": "…", "label": "…" }, … ] }
```

**Semantics**:
- The server pushes an event whenever model status, the active run, or queue contents change — **no
  client polling** (Constitution V; FR-005, FR-024).
- On (re)connect the server immediately sends the current `model_status`, `run_status`, and `queue`
  snapshot so the UI recovers after a dropped channel without a stale in-flight state (FR-007).
- `run_status.active = null` + empty `queue.items` → UI shows the idle indicator and hides the queue
  panel (FR-021, FR-023, FR-025 visibility rules).

---

## 5. Model load feedback (CONFIRMED)

`POST /api/models/load` and `/unload` keep their 001 shapes. The UI MUST NOT block on them: it shows a
non-blocking progress indicator on submit (FR-004) and reflects the result via the `/ws/status`
`model_status` events (FR-005) — the user never manually reloads the page.

---

## 6. Default tools — parameter contracts (NEW)

The agent-facing tool surface produced by `CapabilityRegistry`. Names are the descriptive identities
(reuse existing where present). All file-accessing tools route through the consent/path gate.

```jsonc
// read_file — whole file or a range
{ "name": "read_file", "parameters": { "path": "string",
    "start_line": "integer?", "end_line": "integer?" } }   // range optional (FR-009)

// list_dir
{ "name": "list_dir", "parameters": { "path": "string" } }

// write_file — create/overwrite, makes parent dirs (FR-011)
{ "name": "write_file", "parameters": { "path": "string", "content": "string" } }

// edit — overloaded (exactly one mode per call) (FR-010)
{ "name": "edit", "parameters": {
    "path": "string",
    "old_string": "string?", "new_string": "string?",          // exact-string mode (unique-or-fail)
    "start_line": "integer?", "end_line": "integer?", "new_content": "string?" } } // line-range mode

// grep — content search (FR-012)
{ "name": "grep", "parameters": { "pattern": "string", "path": "string?", "glob": "string?" } }

// find — file paths by glob (FR-012)
{ "name": "find", "parameters": { "glob": "string", "path": "string?" } }

// powershell — workspace-rooted, consent-gated, bounded (FR-014/FR-015a)
{ "name": "powershell", "parameters": { "command": "string", "cwd": "string?" } }

// parallel — run ≥2 independent sub-tool-calls concurrently (FR-008 toolset; clarification Q2)
{ "name": "parallel", "parameters": { "calls": [ { "tool": "string", "arguments": "object" } ] } }
```

**Tool semantics**:
- `edit` fails (file untouched) if exact-string `old_string` is missing or matches more than once, or if
  a line range is out of bounds (Edge Cases).
- `powershell` bounds execution time and truncates output; non-zero exit codes are surfaced; paths
  outside consented folders raise the standard consent prompt; secrets/app-internals always denied.
- `parallel` is for independent calls only; it MUST NOT be used for concurrent operations on the same
  target (e.g., two edits to one file) and rejects an obvious duplicate write/edit-target pair.
- Tool descriptions instruct the agent to **read the relevant section before editing** (FR-016).

---

## Security rules (unchanged, restated)

- Localhost-only; no remote access. No secret values returned by any endpoint or sent over `/ws/status`.
- All request bodies validated with Pydantic at the boundary.
- File/shell access remains bounded by the consent/path gate; the secrets store and app internals are a
  hard deny-list.
