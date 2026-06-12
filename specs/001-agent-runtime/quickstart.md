# Quickstart: Call-Based LM Studio Agent Runtime

**Feature**: 001-agent-runtime | **Date**: 2026-06-12 | **Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)

Developer-facing guide to set up, run, and validate the agent runtime during implementation.

## Prerequisites

- Windows 10/11.
- Python 3.12+.
- **LM Studio** running locally with its server enabled (native API + OpenAI-compatible `/v1`).
- **VS Code** installed and on PATH (`code` command) for file-open links (FR-074).
- At least one chat (non-embedding) model available in LM Studio.

## Setup

```powershell
# From the repo root (D:\Projects\LMStudioClaw)
python -m venv .venv
. .\.venv\Scripts\Activate.ps1          # activate before ANY terminal command (repo rule)
pip install -e ".[dev]"                  # editable install + pytest extras

# New runtime dependencies (installed via commands, not pinned in pyproject per Constitution IV)
pip install fastapi uvicorn "mcp" tiktoken
# plus a Windows toast library when wiring notifications
```

> Note: [lmstudio.bat](lmstudio.bat) currently points at a stale path/venv (`LMStudioManager` +
> `venv\`). Fix it to `LMStudioClaw` + `.venv\` when touching startup behavior (see [AGENTS.md](AGENTS.md)).

## First run

```powershell
lmstudio        # console-script entry point (pyproject [project.scripts])
```

On first run the controller:
1. Creates the Documents folder layout if absent (FR-053):
   `Documents\LMStudioClaw\{skills\, tools\, workspace\, memory\}` + `mcp.json`.
2. Creates the **isolated secrets file** under `%APPDATA%` (outside any agent-accessible path).
3. Starts the web server, the tray icon, and the scheduler — **with no model loaded**.
4. Opens (or lets you open from the tray) the control panel in your browser.

## Configuration

- Connection defaults: [lmstudioclaw/configs/default.yaml](lmstudioclaw/configs/default.yaml)
  (`lmstudio.base_url`, `lmstudio.api_key`). The API key is referenced by name and stored in the vault.
- Per-model context prefs: managed in Settings → Advanced → Model Management (preserves existing
  behavior; persisted in `configs/context_prefs.json`).
- Settings (theme, default model, web port, timeouts, retention, compression threshold): Settings page.

## Validate the primary flows (maps to spec Success Criteria)

1. **Idle = no model** (SC-001): start the app; confirm LM Studio shows no loaded model.
2. **Interactive session** (US1/SC-002/003): start a session, send "list files in my workspace and
   write summary.md"; confirm streaming output, a file written under `workspace\`, then the model
   unloads on session end.
3. **Steering/queue/stop** (SC-013): while generating, press Enter (steer), Alt+Enter (queue), and
   Stop generating; confirm each behaves per [contracts/http-api.md](contracts/http-api.md).
4. **Compression** (SC-014): drive a long session past the threshold; confirm a `compaction` event
   and continuity.
5. **Consent** (US2/SC-004/005): ask the agent to read a file outside `workspace\`; confirm a consent
   prompt, grant "session" then verify it's gone next run; grant "permanent" and verify it persists;
   confirm a subfolder is allowed under a parent grant (hierarchical).
6. **Automation** (US4/SC-006/015/016): create a Daily (today's weekday, 1 min out) and an Interval
   automation; confirm notification + run; toggle persistent vs new session; close the app over a
   scheduled time and confirm a "missed at …" notification on next start.
7. **Skills/tools/MCP** (US5/US6/SC-007/008): drop a `SKILL.md`, add a custom tool (confirm the
   arbitrary-code warning + trust gate), add an MCP server in `mcp.json`; confirm each is usable.
8. **Secrets isolation** (SC-021/022): set an MCP secret via the UI; confirm the value never appears
   in transcripts/logs and the agent cannot read it, but the capability connects.
9. **Personas** (SC-019): edit the default persona, create a second; select per session.
10. **Settings/theme** (SC-010): switch dark/light/system; set default model; confirm persistence.

## Tests

```powershell
pytest                      # unit + integration + contract (add tests under tests/)
```

Priority test targets: `consent/path_gate` (traversal/symlink/hierarchy), `orchestrator/budget` +
`compaction`, `automations/scheduler` (next_fire + missed-run), `sessions/queue` (single-active),
and the HTTP/WebSocket contract.

## Notes & conventions

- Activate `.venv` before any terminal command (repo global rule).
- Keep modules ≤ ~500 meaningful lines; split when growing (Constitution I).
- Never write secrets to logs/transcripts; all agent file I/O goes through the consent gate.
- Update `ARCHITECTURE.md`, `README.md`, and [AGENTS.md](AGENTS.md) as the design lands
  (Constitution VI).
