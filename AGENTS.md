# AGENTS.md

AI-agent guide for **LMStudioClaw** (v1.0.0) — a Windows tray controller + local web
UI that runs a **call-based, LM Studio-powered agent**: no model at idle, load-run-unload
per session/automation. See [README.md](README.md) for the human overview and
[ARCHITECTURE.md](ARCHITECTURE.md) for module-level detail.

## Architecture (modular package, not one file)

The app is a Python package decomposed by concern (Constitution I). High level:

- `cli.py` — thin entry: free-port selection, tray, uvicorn.
- `app.py` — `Controller` wires every service + FastAPI `lifespan` + session coordination.
- `config/` — `paths.py` (Documents layout + isolated `%APPDATA%` secrets + bootstrap),
  `settings.py`.
- `model/` — `catalog.py`, `lifecycle.py`, `context_prefs.py` (reuse the original `httpx`
  native-API logic; **only** module that loads models).
- `orchestrator/` — `engine.py` (interactive turn loop), `budget.py`, `compaction.py`,
  `persona.py`, `memory.py` (agent learnings).
- `capabilities/` — `registry.py` (unified surface + built-in consent-gated fs tools),
  `skills.py`, `tools.py`, `mcp_client.py`.
- `consent/path_gate.py` — the single chokepoint for all agent file access.
- `automations/scheduler.py` — event-driven Daily/Interval scheduler + missed-run detection.
- `sessions/` — `queue.py` (single-active FIFO), `store.py` (SQLite, best-effort writes).
- `secrets/vault.py` — isolated secret store (user-only writes; no agent read path).
- `notifications/toast.py`, `web/` (`api.py`, `ws.py`, `routes_*.py`, `static/` SPA), `tray/icon.py`.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full map and control flow.

## Conventions specific to this project (don't violate these)

- **No idle polling.** Models are discovered on demand; the scheduler sleeps until the next
  fire (no busy loop). Do not add timers that poll LM Studio.
- **One model at a time.** Sessions run through `sessions/queue.py` (FIFO, single active).
  Every terminal session unloads the model. The queue is **persisted** (`queued_runs`
  table) and restored on startup; an interrupted in-progress run is recorded as
  interrupted, not silently replayed.
- **Default toolset (feature 002).** The agent's default tools are `read_file` (optional
  line range), `list_dir`, `write_file`, `edit` (exact-string find/replace OR line-range
  replace), `grep`, `find`, `powershell`, and `parallel` (≥2 independent sub-calls).
  Handlers live in `capabilities/file_tools.py` / `shell_tool.py` / `parallel_tool.py`
  to keep `registry.py` under the size limit. The agent should read the relevant section
  of a file before editing it. `parallel` is for independent calls only — never two
  edits to the same file.
- **PowerShell shares the consent model.** The `powershell` tool starts in the workspace,
  can reach already-consented folders, is bounded by a timeout + output truncation, and
  raises the same consent prompt for paths outside consented folders; secrets/app
  internals are always denied.
- **Per-run config (feature 002).** Sessions and automations may carry a
  `RunConfig{model, tool_overrides, mcp_selection}`. Resolution is **most-granular-wins**
  (MCP selection picks servers, then per-tool overrides apply on top) and never mutates
  global config. Skills are always global — never per-run toggles.
- **Live UI, no polling.** App-wide status flows over `/ws/status` (`StatusHub`); the SPA
  updates the run indicator/queue panel and "Load model" feedback from pushed events. Do
  not add status polling timers. Content uses a fluid ~90vw layout (no fixed narrow cap).
- **All agent file I/O goes through `consent/path_gate.py`.** Workspace is always allowed;
  the secrets area + app internals are a hard deny-list; grants are hierarchical and
  least-privilege (read ≠ write).
- **Secrets never reach the agent.** Only `vault.inject` exists for trusted runtime use;
  there is no `get_value`. Never log/echo secret values.
- **Best-effort persistence.** `sessions/store.py` swallows storage errors so the
  controller never crashes on a hiccup. Preserve this.
- **Boundary validation.** All REST/WS inputs are validated with Pydantic in `web/`.
- **Context length clamp** stays `[1024, max_context_length]` (`model/context_prefs.py`).

## Build / run / test

```powershell
python -m venv venv
venv\Scripts\Activate.ps1            # activate before ANY terminal command (repo rule)
pip install -e ".[dev]"

lmstudio                             # run the controller (entry point in pyproject.toml)
pytest                               # unit + integration + contract tests under tests/
```

- If the venv `pip` launcher errors with a stale `LMStudioClaw` path, use `python -m pip`
  (and `python -m pytest`).
- Connection settings: `lmstudioclaw/config/default.yaml` (`/v1` is stripped for the native API).

## Pitfalls

- Windows-only by design (`%APPDATA%`, `pythonw`, system tray, Windows toasts).
- `cli()` needs `pystray`/`Pillow` for the tray; uvicorn serves the web UI.
- Some web tooling deprecation warnings (Starlette/httpx) are benign.

## Editing rules (from repo global instructions)

- Markdown files allowed: `README.md`, `AGENTS.md`, **and `ARCHITECTURE.md`** (the latter is
  a required deliverable per Constitution v1.1.0). Do not add other `.md` docs.
- Keep modules ≤ ~500 meaningful lines; split a growing module into a new file rather than
  letting it balloon (`web/api.py` is already split into `routes_*.py`).
- Don't pin dependency versions in `pyproject.toml` unless asked; suggest the install command.
