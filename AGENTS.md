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
- **Live UI, no polling.** App-wide status flows over `/ws/status` (`StatusHub`); the UI
  updates the run indicator/queue panel and "Load model" feedback from pushed events. Do
  not add status polling timers. Content uses a fluid ~90vw layout (no fixed narrow cap).
  Per-session `/ws/sessions/{id}` caches the last `budget` + working/idle (`turn`) state
  and replays them on (re)connect, so a reloaded mid-run session shows the gauge and the
  correct Stop-turn state immediately. Tool turns are persisted (`tool_call`/`tool_result`,
  incl. display `meta`) and rebuilt into cards on reload; one reply renders as one bubble.
- **The web UI is a React app** (`frontend/`, Vite + react-router + react-markdown +
  framer-motion) that **builds into `lmstudioclaw/web/static/`** — the FastAPI StaticFiles
  mount serves the build unchanged. The agent runtime serves files; it does not require
  Node at run time. NOTE: this supersedes the original framework-free SPA decision at the
  user's explicit request (a tension with Constitution V — Resource Frugality); keep the
  bundle lean and avoid heavy deps. Edit UI under `frontend/src/`, then rebuild; never
  hand-edit files in `web/static/` (they are generated).
- **All agent file I/O goes through `consent/path_gate.py`.** The workspace **and the
  whole `Documents/LMStudioClaw` home** (skills, tools, memory, `mcp.json`) are always
  allowed without a prompt; the secrets area + app internals are a hard deny-list
  (evaluated first, so they always win); other grants are hierarchical and
  least-privilege (read ≠ write). The agent is told its home layout + where `mcp.json`
  lives in the system prompt, so it edits config directly rather than asking for drive
  access.
- **MCP supports stdio and HTTP.** `mcp.json` uses the standard format: local servers
  use `command`/`args`/`env`; remote servers use `type` (`"http"` or `"sse"`) + `url` +
  optional auth `headers` (e.g. `{"Authorization": "Bearer <token>"}`). Transport is
  resolved in `capabilities/mcp_client.py` (`McpServer.transport`); auth keys live only
  in `headers` and are never logged. The Capabilities UI form switches fields by
  transport and parses `Key: Value` header lines. On Windows, a stdio `command` is
  resolved on PATH and `.cmd`/`.bat` shims (e.g. `npx`) run via `cmd /c`
  (`_resolve_stdio_command`) so the same config that works in VS Code works here; the
  `open-in-vscode` launcher does the same for `code`. SDK `ExceptionGroup`s are flattened
  to the real cause so a failed server shows a useful reason.
- **Tool actions are shown, not named.** File tools attach display-only `meta` to
  `ToolResult` (action + before/after content); MCP tools attach `meta` with the
  server/tool + `input`/`output`. The engine forwards it on the `tool_result` event and
  the UI (`components/ToolCard.jsx`) renders a readable card ("Read X", "Created Y",
  "Edited Z +n −m", "Used server · tool") with an expandable side-by-side diff,
  new-file contents, or an input→output panel; file cards also offer **Open in VS Code**.
  Never surface the raw tool name. `meta` is display-only — it must never be fed back
  into the model context. The per-run tool list (`/api/tools`) keeps MCP servers out of
  the custom-tools list (they are toggled separately under MCP selection). The run-config
  UI offers **per-server and per-tool** MCP granularity (VS Code style): the persisted
  per-server tool list (name + description, in the capability `metadata`) drives a
  server→tools tree; unchecking a tool adds a `tool_overrides["{server}__{tool}"]=false`
  while unchecking a server drops it from `mcp_selection`. Hover shows descriptions.
- **Secrets never reach the agent.** Only `vault.inject` exists for trusted runtime use;
  there is no `get_value`. Never log/echo secret values. A stored secret can be consumed
  by any capability without the agent seeing it: MCP `env`/`headers` values may be
  `"${secret:REF_NAME}"` (resolved at connect time via `_resolve_secrets`); a custom tool
  may declare `SECRETS = {"ENV_VAR": "REF"}` (or a list) and receive resolved values via a
  reserved `_secrets` kwarg; a skill may list `secrets:` in its SKILL.md front-matter and
  its scripts get them as subprocess env vars. Resolution goes through
  `CapabilityRegistry._secret_env`; values are never persisted resolved or shown.
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

### Web UI (React)

```powershell
cd frontend
npm install                          # first time only
npm run build                        # builds into ../lmstudioclaw/web/static (served by FastAPI)
npm run dev                          # optional: Vite dev server on :5273, proxies /api + /ws to :8765
```

After changing anything under `frontend/src/`, run `npm run build` and restart/refresh the
controller. The build wipes `web/static/` and regenerates `index.html` + `assets/`.

- **uvicorn needs `uvicorn[standard]`** (bundles `websockets`); without it, `/ws/*`
  upgrades fail at runtime even though `TestClient` WebSocket tests pass.

- If the venv `pip` launcher errors with a stale `LMStudioClaw` path, use `python -m pip`
  (and `python -m pytest`).
- Connection settings: `lmstudioclaw/config/default.yaml` (`/v1` is stripped for the native API).

## Pitfalls

- Windows-only by design (`%APPDATA%`, `pythonw`, system tray, Windows toasts).
- `cli()` needs `pystray`/`Pillow` for the tray; uvicorn serves the web UI.
- Some web tooling deprecation warnings (Starlette/httpx) are benign.

## Editing rules (from repo global instructions)

- Markdown files allowed: `README.md`, `AGENTS.md`, **and `ARCHITECTURE.md`** (the latter is
  a required deliverable per Constitution v1.1.0). Do not add other `.md` docs. README
  screenshots live in `docs/images/` (binary assets only — not a docs site).
- Keep modules ≤ ~500 meaningful lines; split a growing module into a new file rather than
  letting it balloon (`web/api.py` is already split into `routes_*.py`).
- Don't pin dependency versions in `pyproject.toml` unless asked; suggest the install command.
