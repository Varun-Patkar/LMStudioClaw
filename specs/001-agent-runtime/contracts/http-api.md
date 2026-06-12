# Contract: HTTP + WebSocket Control-Panel API

**Feature**: 001-agent-runtime | **Date**: 2026-06-12 | **Spec**: [spec.md](../spec.md)

The control panel (web UI) talks to the local controller over **REST** (state/management) and a
**WebSocket** (live session streaming + steering). All endpoints are served on `localhost` only
(single-user, FR — no remote access). Request/response bodies are JSON validated with Pydantic at the
boundary. **No secret values are ever returned** by any endpoint (FR-026/FR-077).

Base URL: `http://localhost:{web_port}` (with fallback port if taken; the tray opens the actual URL).

> This is a behavioral contract (shapes + semantics), not a frozen OpenAPI document. Field names are
> indicative; implementers MUST preserve the semantics and the security rules.

---

## REST endpoints

### Sessions

| Method | Path | Purpose | Maps to |
|--------|------|---------|---------|
| POST | `/api/sessions` | Start a manual session (body: `{model?, persona_id?}`). Returns `{session_id, queue_position}`. If a session is active, it is **queued** (FR-008). | US1 |
| GET | `/api/sessions` | List sessions (filter by status/trigger; paginated). | US3 |
| GET | `/api/sessions/{id}` | Session detail + turns + grants + compression events. | US3 |
| POST | `/api/sessions/{id}/stop` | Stop generating current turn **or** end session (body: `{scope: "turn"\|"session"}`). | FR-005/FR-059 |
| GET | `/api/queue` | View the FIFO queue. | FR-008 |
| DELETE | `/api/queue/{id}` | Cancel a queued (not-yet-started) item. | FR-008 |

### Automations

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/automations` | Create (Daily: `{days[], time}` or Interval: `{unit, value}`; `session_mode`, `persona_id?`, `model_override?`). |
| GET | `/api/automations` | List with `schedule`, `session_mode`, `enabled`, `last_run_result`, `next_run_at`. |
| PATCH | `/api/automations/{id}` | Edit / enable / disable. |
| DELETE | `/api/automations/{id}` | Delete. |
| POST | `/api/automations/{id}/run` | Run now (enters the queue). |

### Capabilities (skills / tools / MCP)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/capabilities` | List skills, tools, MCP servers with status/enabled/trust. |
| POST | `/api/capabilities/refresh` | Re-scan skills/tools/`mcp.json`. |
| PATCH | `/api/capabilities/{id}` | Enable/disable; for tools, set `trust_confirmed` (requires explicit confirm — FR-015). |
| POST | `/api/capabilities/mcp` | Add an MCP server entry (writes `mcp.json`; secret values via the secrets endpoint only). |

### Secrets (user-only; values write-only)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/secrets` | List secret **ref names + owners only** (never values). |
| PUT | `/api/secrets/{ref_name}` | Set/replace a secret value (write-only; stored in isolated vault). |
| DELETE | `/api/secrets/{ref_name}` | Remove a secret. |

> The agent has **no** route to read secret values; these endpoints are user-initiated from the UI
> (FR-078). Responses never echo the value.

### Consent / grants

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/grants` | List active grants (path, scope, access). |
| POST | `/api/grants` | Respond to a pending request: `{request_id, decision: "session"\|"permanent"\|"deny", access}`. |
| DELETE | `/api/grants/{id}` | Revoke a grant (applies to subsequent checks — FR-023). |

### Personas

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/personas` | List personas (default flagged). |
| POST | `/api/personas` | Create. |
| PATCH | `/api/personas/{id}` | Edit/rename (including the default). |
| DELETE | `/api/personas/{id}` | Delete (not the default). |

### Settings & model management

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/settings` | Current settings (no secret values). |
| PATCH | `/api/settings` | Update theme, default model, startup, notifications, web port, timeouts, retention, compression threshold. |
| GET | `/api/models` | Discover LM Studio models (catalog; one call, no polling). |
| POST | `/api/models/context-pref` | Set per-model context length (Advanced → Model Management). |
| POST | `/api/models/load` · `/unload` · `/warmup` | Manual model management (Advanced). |

### Files

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/open-in-vscode` | Open a referenced file path in VS Code; error if unavailable (FR-074). |

---

## WebSocket: `/ws/sessions/{id}`

Bidirectional channel for a session's live interaction.

**Server → client events** (streamed):
```jsonc
{ "type": "status", "status": "loading|active|completed|failed|stopped" }
{ "type": "token", "text": "…" }                      // streamed assistant output
{ "type": "tool_call", "name": "…", "args": { } }
{ "type": "tool_result", "name": "…", "ok": true, "summary": "…" }
{ "type": "consent_request", "request_id": "…", "path": "…", "access": "read|read_write" }
{ "type": "budget", "used": 12345, "total": 32768, "threshold": 0.9 }
{ "type": "compaction", "tokens_before": 30000, "tokens_after": 12000 }
{ "type": "error", "reason": "…", "point": "…" }
```

**Client → server events**:
```jsonc
{ "type": "steer", "text": "…" }     // Enter while generating → inject into current turn (FR-057)
{ "type": "queue", "text": "…" }     // Alt+Enter → next-turn message (FR-058)
{ "type": "stop", "scope": "turn" }  // Stop generating (FR-059)
{ "type": "message", "text": "…" }   // normal next message when idle
```

**Semantics**:
- `steer` while no turn is generating is treated as a normal `message` (edge case in spec).
- `consent_request` pauses the run until a `/api/grants` decision (or fails fast for unattended
  automations with no permanent grant — FR-025).
- The model is unloaded when the session reaches a terminal status; the socket then closes.

---

## Cross-cutting rules

- **Localhost-only**, single user; no auth in v1 (single local user assumption).
- **Validation** at every boundary (Pydantic); reject malformed bodies with clear errors.
- **No secrets** in any response, log, or WebSocket event (FR-026/FR-077).
- **Best-effort persistence**: a storage hiccup must not crash the controller (Constitution II).
- **Single active session**: `POST /api/sessions` and automation triggers never start a second
  concurrent run; they enqueue (FR-008).
