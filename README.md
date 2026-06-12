# LM Studio Manager

A Windows desktop **agent runtime** powered by your local LM Studio models. A resident
controller lives in the system tray and serves a local web control panel. It keeps **no
model loaded while idle**; when you start a session or an automation fires, it loads the
chosen model, runs an interactive agent (with skills, custom tools, and MCP servers
inside a folder-consent boundary), then unloads the model and records the run.

> This is a ground-up redesign of the original "model sync" tray app into a call-based,
> LM Studio-powered agent. The model load/unload/context functionality lives on under
> **Settings → Advanced → Model Management**.

## Features

- **Resident controller** — system tray + local web UI; closing the browser does not quit
  (only the tray **Quit** does). No model is loaded at idle.
- **Interactive agent sessions** — streaming output with live **steering** (Enter),
  **queuing** (Alt+Enter), **Stop**, and automatic **context compaction** near the
  context limit.
- **Single-active FIFO queue** — exactly one session runs at a time; never two models at once.
- **Consent-bounded filesystem** — the agent freely uses its `workspace/`, and must
  request hierarchical, least-privilege access to anything else (session or permanent
  grants, revocable).
- **Automations** — Daily (weekdays + time) or Interval schedules, new or persistent
  sessions, run/missed notifications.
- **Skills, tools, MCP** — drop a `SKILL.md` folder, add a trusted custom Python tool, or
  register an MCP server.
- **Isolated secrets** — stored outside any agent-reachable path; the agent can use a
  capability that needs a secret, but can never read the value.
- **Personas** — an editable default plus a library, selectable per session/automation.

## Install

```powershell
# From the repo root (D:\Projects\LMStudioClaw)
python -m venv venv
venv\Scripts\Activate.ps1            # activate before ANY terminal command (repo rule)
pip install -e ".[dev]"              # editable install + test extras
```

Runtime dependencies (FastAPI, uvicorn, pydantic, the `mcp` SDK, tiktoken, a Windows
toast library) are declared in `pyproject.toml` and installed by the command above.

> If the venv's `pip` launcher errors with a stale path, use `python -m pip ...`.

## Prerequisites

- Windows 10/11, Python 3.12+.
- **LM Studio** running locally with its server enabled (native API + `/v1`).
- At least one chat (non-embedding) model available.
- **VS Code** on PATH (`code`) for "open file" links (optional).

## Run

```powershell
lmstudio            # console-script entry point
```

On first run the controller creates `Documents\LMStudioClaw\{skills,tools,workspace,memory}`
+ `mcp.json`, an isolated secrets store under `%APPDATA%`, then starts the web server, tray,
and scheduler — with no model loaded. Use the tray **Open Control Panel** to open the UI.

Or use `lmstudio.bat` (also suitable for a `shell:startup` shortcut to launch on login).

## Control panel

- **Sessions** — start a session, watch streaming output, steer/queue/stop, manage folder
  permissions, and browse past runs.
- **Automations** — schedule Daily/Interval tasks, new vs persistent sessions, run now.
- **Skills & Tools** — enable skills, trust + enable custom tools, add MCP servers, set
  secrets (write-only).
- **Settings** — theme, default model, startup, timeouts, retention, compression threshold,
  personas, and **Advanced → Model Management** (per-model context, manual load/unload/warmup).

## Configuration

- Connection defaults: `lmstudioclaw/config/default.yaml` (`lmstudio.base_url`, `lmstudio.api_key`).
- Per-model context prefs: `lmstudioclaw/config/context_prefs.json` (managed in Advanced).
- Settings: stored under `%APPDATA%/LMStudioClaw/settings.json`.

## Tests

```powershell
pytest
```

## More

- [ARCHITECTURE.md](ARCHITECTURE.md) — modules, control flow, invariants, extension points.
- [AGENTS.md](AGENTS.md) — guide for AI agents working in this repo.
- [specs/001-agent-runtime/](specs/001-agent-runtime/) — full specification and design.
