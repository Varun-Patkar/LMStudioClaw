"""Controller wiring and lifespan.

Constructs and owns every service (store, secrets vault, settings, model lifecycle,
consent gate, capability registry, orchestrator engine, session queue, scheduler,
notifications) and exposes the high-level operations the web/tray layers call.

On startup it detects an orphaned model left loaded from a previous run (FR-006),
prunes old history (FR-038), and starts the single-active session queue loop. On
shutdown it stops the queue/scheduler and unloads any model (graceful Quit, FR-043).
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .capabilities.registry import CapabilityRegistry
from .capabilities.run_config import RunConfig
from .config import paths as paths_mod
from .config.settings import load_settings, save_settings
from .consent.path_gate import PathGate
from .model.catalog import load_connection, make_client, list_models
from .model.context_prefs import preferred_context
from .model.lifecycle import ModelLifecycle
from .notifications import toast
from .orchestrator import persona as persona_mod
from .orchestrator.engine import Engine, SessionResult
from .secrets.vault import SecretsVault
from .sessions.queue import SessionQueue
from .sessions.store import Store
from .web.tunnel import TunnelManager
from .web.ws import SessionHub, StatusHub


class Controller:
    """Owns all runtime services and coordinates session execution."""

    def __init__(self) -> None:
        """Bootstrap paths and construct every service (no network yet)."""
        self.paths, self.bootstrap_warnings = paths_mod.bootstrap()
        self.settings = load_settings(self.paths.settings_path)
        self.store = Store(self.paths.db_path)
        self.store.ensure_default_persona()
        self.vault = SecretsVault(self.paths.secrets_dir)
        # Graph "brain" memory (nodes + edges in graph.db; node details as Markdown).
        # Created empty on first run; the agent reads/writes/traverses it via tools.
        from .orchestrator.brain import BrainStore
        self.brain = BrainStore(self.paths.graph_db, self.paths.brain_dir)
        # Drop/refresh the standalone HTML log viewer in the logs folder (first run).
        from .sessions import logbook as _logbook
        _logbook.ensure_logs_assets(self.paths.logs_dir)

        self.connection = load_connection(
            base_url=self.settings.lmstudio_base_url,
            api_key=self.vault.inject({"k": self.settings.lmstudio_api_key_ref}).get("k"),
        )
        self.http = make_client(self.connection)
        self.lifecycle = ModelLifecycle(self.http, self.connection)

        self.gate = PathGate(self.paths, self.store)
        self.registry = CapabilityRegistry(self.paths, self.store, self.gate, self.vault)
        self.engine = Engine(
            self.store, self.registry, self.connection.openai_base, self.connection.api_key
        )
        self.queue = SessionQueue(store=self.store)
        self.hub = SessionHub()
        self.status = StatusHub()
        self.status.snapshot_provider = self._status_snapshot
        self._model_status: dict = {"type": "model_status", "status": "idle", "model": None}
        self.scheduler = None  # set during startup (Phase 6)
        self._queue_task: asyncio.Task | None = None
        self._scheduler_task: asyncio.Task | None = None
        # An automation id passed via ``--run-automation`` when the app is launched by a
        # Windows Scheduled Task; run once the controller is up (then cleared).
        self.pending_automation_id: str | None = None
        self.served_url: str = f"http://localhost:{self.settings.web_port}"
        self.served_port: int = self.settings.web_port
        # Public-URL tunnel for "See this on your phone" (started on demand).
        self.tunnel = TunnelManager()

    # -- lifecycle ----------------------------------------------------------

    async def startup(self) -> None:
        """Detect orphan models, prune history, start the queue loop + scheduler."""
        try:
            orphan = await self.lifecycle.detect_orphan()
            if orphan is not None:
                await self.lifecycle.unload(orphan.instance_id)
        except Exception:
            pass  # best-effort startup cleanup
        self.store.prune(self.settings.retention_days)
        self._queue_task = asyncio.create_task(self.queue.run_loop())
        self._reconcile_interrupted_runs()
        self.queue.restore_from_store(self._restore_runner)
        await self._start_scheduler()
        self._write_runtime_marker()
        if getattr(self.settings, "use_task_scheduler", False):
            self.sync_automation_tasks()
        self._run_pending_automation()

    async def _start_scheduler(self) -> None:
        """Start the automation scheduler (wired in Phase 6)."""
        try:
            from .automations.scheduler import Scheduler

            self.scheduler = Scheduler(self.store, self.enqueue_automation)
            self.scheduler.report_missed(toast.notify)
            self._scheduler_task = asyncio.create_task(self.scheduler.run())
        except Exception:
            # Scheduler is optional for the MVP slice; controller still runs.
            self.scheduler = None

    # -- Windows Task Scheduler integration (opt-in) -----------------------

    def _runtime_marker_path(self):
        """Path to the runtime marker file external launchers use to find us."""
        return self.paths.app_data / "runtime.json"

    def _write_runtime_marker(self) -> None:
        """Record this live instance (url/port/pid) so taskrunner can reach it."""
        import json
        import os

        try:
            self._runtime_marker_path().write_text(
                json.dumps({"url": self.served_url, "port": self.served_port,
                            "pid": os.getpid()}),
                encoding="utf-8",
            )
        except OSError:
            pass  # best-effort; the launcher falls back to starting a new instance

    def _remove_runtime_marker(self) -> None:
        """Remove the runtime marker on shutdown (best-effort)."""
        try:
            self._runtime_marker_path().unlink(missing_ok=True)
        except OSError:
            pass

    def _run_pending_automation(self) -> None:
        """Run an automation requested at launch via ``--run-automation`` (once)."""
        aid = self.pending_automation_id
        self.pending_automation_id = None
        if not aid:
            return
        automation = self.store.get_automation(aid)
        if automation is not None:
            try:
                self.enqueue_automation(automation)
            except Exception:
                pass  # best-effort; controller records its own failures

    def sync_automation_tasks(self) -> None:
        """Reconcile Windows Scheduled Tasks with automations (opt-in; best-effort).

        Registers a per-automation task when ``use_task_scheduler`` is on so due
        automations fire even while the app is closed; removes all managed tasks when
        the setting is off. No-op on non-Windows platforms / if schtasks is missing.
        """
        try:
            from .automations import tasksched

            tasksched.sync(self.store.list_automations(),
                           enabled=bool(getattr(self.settings, "use_task_scheduler", False)))
        except Exception:
            pass

    async def shutdown(self) -> None:
        """Stop the queue/scheduler, unload any model, close clients (FR-043)."""
        self._remove_runtime_marker()
        self.queue.stop()
        if self.scheduler is not None:
            self.scheduler.stop()
        for task in (self._queue_task, self._scheduler_task):
            if task is not None:
                task.cancel()
        try:
            await self.lifecycle.unload_all()
        except Exception:
            pass
        try:
            await self.engine.aclose()
        except Exception:
            pass
        try:
            self.tunnel.stop()
        except Exception:
            pass
        self.http.close()
        self.brain.close()
        self.store.close()

    # -- settings -----------------------------------------------------------

    def save(self) -> None:
        """Persist current settings to disk."""
        save_settings(self.paths.settings_path, self.settings)

    # -- onboarding / connection setup (FR: first-run + per-start check) ----

    def connection_status(self) -> dict:
        """Probe LM Studio and report what onboarding needs (runs every startup).

        Returns the connection classification plus whether a key is stored and any
        first-run bootstrap warnings. ``needs_setup`` is the single flag the UI uses
        to decide whether to show the setup wizard — it never returns the key value
        itself (FR-026/FR-077).
        """
        from .model.catalog import probe_connection

        probe = probe_connection(self.http)
        has_key = self.vault.has(self.settings.lmstudio_api_key_ref)
        # Setup is needed when LM Studio is unreachable, or reachable but our key is
        # rejected / the instance is protected and we have no working key.
        needs_setup = (not probe["reachable"]) or (not probe["authorized"])
        return {
            "reachable": probe["reachable"],
            "authorized": probe["authorized"],
            "auth_required": probe["auth_required"],
            "has_key": has_key,
            "base_url": self.settings.lmstudio_base_url,
            "needs_setup": needs_setup,
            "warnings": list(self.bootstrap_warnings),
        }

    def update_connection(self, base_url: str | None, api_key: str | None) -> dict:
        """Apply a new base URL and/or API key, rebuilding the live clients.

        The key (when provided and non-empty) is written to the isolated vault, never
        to settings/logs. A blank key clears any stored key (unprotected instance).
        Returns the fresh :meth:`connection_status` so the caller can confirm success.
        """
        if base_url:
            self.settings.lmstudio_base_url = base_url.strip()

        ref = self.settings.lmstudio_api_key_ref
        if api_key is not None:
            stripped = api_key.strip()
            if stripped:
                self.vault.set(ref, stripped, owner="user")
            else:
                self.vault.delete(ref)
        self.save()

        # Rebuild the connection + every client that depends on the key.
        self.connection = load_connection(
            base_url=self.settings.lmstudio_base_url,
            api_key=self.vault.inject({"k": ref}).get("k"),
        )
        try:
            self.http.close()
        except Exception:
            pass
        self.http = make_client(self.connection)
        self.lifecycle = ModelLifecycle(self.http, self.connection)
        self.engine.set_client(self.connection.openai_base, self.connection.api_key)
        return self.connection_status()

    # -- live status broadcasting (FR-005/FR-007/FR-024) -------------------

    def _status_snapshot(self) -> list[dict]:
        """Return the current model/run/queue status events (replayed on connect)."""
        return [
            dict(self._model_status),
            {"type": "run_status", "active": self._active_run_info()},
            {"type": "queue", "items": self._queue_items()},
        ]

    def _active_run_info(self) -> dict | None:
        """Describe the currently active run for the top-right indicator, or None."""
        sid = self.queue.active_session_id
        if sid is None:
            return None
        session = self.store.get_session(sid) or {}
        return {
            "id": sid,
            "trigger_type": session.get("trigger_type", "manual"),
            "status": session.get("status", "active"),
            "label": self._run_label(session),
        }

    def _run_label(self, session: dict) -> str:
        """Human label for a run (automation name when available, else 'Session')."""
        aid = session.get("automation_id")
        if aid:
            automation = self.store.get_automation(aid)
            if automation:
                return automation.get("name", "Automation")
            return "Automation"
        return "Session"

    def _queue_items(self) -> list[dict]:
        """Build enriched queue snapshot items (type + label) for the queue panel."""
        items: list[dict] = []
        for entry in self.queue.snapshot():
            if entry.get("state") != "queued":
                continue
            session = self.store.get_session(entry["session_id"]) or {}
            items.append({
                "id": entry["session_id"],
                "state": "queued",
                "trigger_type": session.get("trigger_type", "manual"),
                "label": self._run_label(session),
            })
        return items

    async def _broadcast_status(self) -> None:
        """Push the full current status snapshot to all status sockets."""
        for event in self._status_snapshot():
            await self.status.broadcast(event)

    def _schedule_status(self) -> None:
        """Schedule a status broadcast from sync code if an event loop is running."""
        try:
            asyncio.get_running_loop().create_task(self._broadcast_status())
        except RuntimeError:
            pass  # no running loop (e.g. unit-test context) — snapshot still on connect

    def _set_model_status(self, status: str, model: str | None = None, reason: str | None = None) -> None:
        """Update the tracked model status and broadcast it live."""
        self._model_status = {"type": "model_status", "status": status, "model": model}
        if reason:
            self._model_status["reason"] = reason
        self._schedule_status()

    async def set_model_status(self, status: str, model: str | None = None, reason: str | None = None) -> None:
        """Async variant: update tracked model status and broadcast immediately.

        Used by the manual model load/unload REST routes (which run on the event loop)
        so the UI reflects load/ready/idle/error live without a page reload (FR-005).
        """
        self._model_status = {"type": "model_status", "status": status, "model": model}
        if reason:
            self._model_status["reason"] = reason
        await self._broadcast_status()

    # -- persisted-queue restore / reconciliation (FR-025a) ----------------

    def _reconcile_interrupted_runs(self) -> None:
        """Mark any session left ``loading``/``active`` from a prior process as interrupted.

        A run that was in-progress when the app stopped cannot have its mid-turn state
        replayed; it is recorded as failed (interrupted) and surfaced via a notification
        rather than silently resumed (FR-025a, Edge Cases).
        """
        orphan = self.store.active_or_loading()
        if orphan is None:
            return
        self.store.update_session(
            orphan["id"], status="failed",
            failure_reason="Interrupted by app/PC restart", failure_point="interrupted",
        )
        self.store.remove_queued_run(orphan["id"])
        self.store.add_notification(
            type="run_failed",
            message="A run was interrupted by a restart and could not be resumed.",
            related_session_id=orphan["id"],
        )

    def _restore_runner(self, row: dict):
        """Rebuild a queue runner from a persisted ``queued_runs`` row, or None."""
        run_config = RunConfig.from_dict(row.get("run_config"))
        if row.get("trigger_type") == "automation" and row.get("automation_id"):
            automation = self.store.get_automation(row["automation_id"])
            if automation is None:
                return None  # automation deleted while queued → drop
            model_key, ctx = self._resolve_model(self._config_model(run_config, automation))
            self.hub.register(row["id"])
            return self._make_runner(
                row["id"], model_key=model_key, persona_id=automation.get("persona_id"),
                context_length=ctx, unattended=True, initial_message=automation.get("task"),
                automation_id=automation["id"], run_config=run_config,
            )
        # Manual run.
        model_key, ctx = self._resolve_model(run_config.model if run_config else None)
        self.hub.register(row["id"])
        return self._make_runner(
            row["id"], model_key=model_key, persona_id=None, context_length=ctx,
            unattended=False, initial_message=row.get("initial_message"),
            run_config=run_config,
        )

    @staticmethod
    def _config_model(run_config, automation: dict) -> str | None:
        """Resolve the model key from run_config, falling back to legacy model_override."""
        if run_config and run_config.model:
            return run_config.model
        return automation.get("model_override")

    # -- session coordination ----------------------------------------------

    def _resolve_model(self, model_key: str | None) -> tuple[str, int]:
        """Resolve the model key to use and its effective context length."""
        models, _connected = list_models(self.http)
        chosen_key = model_key or self.settings.default_model
        chosen = None
        for m in models:
            if m.key == chosen_key:
                chosen = m
                break
        if chosen is None and models:
            chosen = models[0]
        if chosen is None:
            # No models discoverable; fall back to the requested key + a safe context.
            return (chosen_key or "", 4096)
        ctx = preferred_context(
            {"key": chosen.key, "max_context_length": chosen.max_context_length}
        )
        return chosen.key, ctx

    def session_output_dir(self, session_id: str, *, create: bool = False):
        """Return (and optionally create) the per-session output folder.

        Lives under the workspace (always agent-writable, no consent prompt) so the
        agent can drop user-facing deliverables there; the UI lists them in the
        session's Output panel.
        """
        path = self.paths.workspace / "outputs" / session_id
        if create:
            try:
                path.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
        return path

    def _build_system_prompt(self, persona_id: str | None, scope: str | None = None,
                             session_id: str | None = None) -> str:
        """Compose the system prompt from persona, enabled skills, and learnings."""
        from .orchestrator import memory as memory_mod

        persona = persona_mod.resolve(self.store, persona_id)
        parts = [persona.instructions]
        # Tool-use guidance: read before editing so edits land correctly (FR-016).
        parts.append(
            "\n## Tool use\nBefore editing a file, read the relevant section first so "
            "your edit targets the correct place. Use `edit` for precise in-place changes "
            "(exact-string find/replace, or a line range) and `write_file` to create or "
            "overwrite. Use `parallel` only for independent operations — never for two "
            "edits to the same file."
        )
        # Internet access: the model often wrongly assumes it is sandboxed offline. The
        # `powershell` tool runs on the real host with full network access, so spell out
        # that the agent CAN reach the internet and how to do it.
        parts.append(
            "\n## Internet access\n"
            "You CAN access the internet. To READ a web page or call a JSON/HTTP API, "
            "use the `fetch_url` tool: pass it a URL and it returns the page title, the "
            "readable text (HTML stripped), and the links — no raw markup to wade through. "
            "**Fetch a URL once, then answer from the returned text; never re-request the "
            "same URL** (re-fetching wastes the context budget and stalls the run). If you "
            "need scripting, downloads, or other shell work, the `powershell` tool also has "
            "full network access (`Invoke-WebRequest`/`Invoke-RestMethod`/`curl`). Never tell "
            "the user you cannot browse the web or fetch URLs. Configured MCP servers and "
            "skills may also provide richer web tools when available."
        )
        # Environment map: where the agent's home + key config files live, so the agent
        # goes straight to them instead of probing drives (no consent needed here —
        # the whole home folder is implicitly allowed).
        parts.append(
            "\n## Environment\n"
            f"Your home folder is `{self.paths.base}` and everything under it is "
            "directly accessible (no permission prompt). It contains: `skills/` "
            "(SKILL.md folders), `tools/` (custom Python tools), `workspace/` (your "
            "read/write playground), `memory/` (durable learnings), and `mcp.json`.\n"
            f"MCP servers are configured in `{self.paths.mcp_json}`. Edit it to add or "
            "change servers; it uses the standard MCP config format:\n"
            "```json\n"
            "{\n"
            '  "mcpServers": {\n'
            '    "stdio-example": { "command": "npx", "args": ["-y", "server-pkg"], '
            '"env": { "KEY": "value" } },\n'
            '    "http-example": { "type": "http", "url": "https://host/mcp", '
            '"headers": { "Authorization": "Bearer <token>" } }\n'
            "  }\n"
            "}\n"
            "```\n"
            "Use `type` `\"http\"` (Streamable HTTP) or `\"sse\"` for remote servers and "
            "put auth keys in `headers`; omit `type` for local `command`-based servers. "
            "Split the executable from its arguments: `command` is just the program "
            "(e.g. `\"npx\"`) and every flag/package goes in the `args` array (e.g. "
            "`[\"-y\", \"@scope/pkg\", \"--flag\"]`) — never put arguments inside `command`. "
            "For a secret value (API key/token), do NOT hardcode it: reference it as "
            "`\"${secret:REF_NAME}\"` in an `env` or `headers` value, choosing a concrete, "
            "descriptive REF_NAME yourself (e.g. `${secret:WEBIQ_API_KEY}`) — never leave "
            "the literal text `REF_NAME`. After you save the file, the Skills & Tools page "
            "automatically prompts the user to enter that secret's value (write-only); you "
            "never see it. Tell the user which secret name to fill in. The value is resolved "
            "only at connect time and never exposed.\n"
            "Secrets work the same way for capabilities you author: a custom tool in "
            "`tools/` may declare `SECRETS = {\"ENV_VAR\": \"REF_NAME\"}` (or a list of ref "
            "names) and receives the resolved values via a reserved `_secrets` keyword "
            "argument; a skill may list `secrets:` in its SKILL.md front-matter and its "
            "scripts get them as environment variables. You never see the values — only "
            "the user can set them in the Secrets UI."
        )
        # Skills are progressively disclosed: list a catalog (name + when-to-use +
        # SKILL.md path) instead of dumping every skill's full instructions into context.
        # The agent reads a skill's SKILL.md on demand when it applies (or is @mentioned),
        # so "enabled" means available — not always loaded (keeps context lean).
        skills = self.registry.enabled_skills()
        if skills:
            from pathlib import Path

            lines = [
                "\n## Available skills",
                "These skills are available to you but are NOT pre-loaded. Each lists its "
                "name, when to use it, and the path to its SKILL.md. When a task matches a "
                "skill — or the user explicitly references one with a slash command (e.g. "
                "`/docx`) — first read that skill's SKILL.md with `read_file` to load its "
                "full instructions, then follow them (including any scripts it references via "
                "`run_skill_script` or `powershell`). Don't guess a skill's steps without "
                "reading it.",
            ]
            for s in skills:
                md = Path(s.source_path) / "SKILL.md" if s.source_path else s.name
                lines.append(f"- **{s.name}** — {s.description or '(no description)'}\n"
                             f"  instructions: `{md}`")
            parts.append("\n".join(lines))
        if session_id:
            out_dir = self.session_output_dir(session_id)
            parts.append(
                "\n## Delivering files to the user\n"
                "The user cannot browse your filesystem. To hand a file to the user "
                "(a document, image, report, export, generated artifact, etc.), save it "
                f"into your session output folder:\n`{out_dir}`\n"
                "Writing a file there creates the folder automatically (it stays absent "
                "until you actually produce a deliverable). Anything you write there "
                "appears in the session's Output panel with a download button (images "
                "preview inline). Use a clear filename with the correct extension, and "
                "mention in your reply that you created it. Only put finished deliverables "
                "here — use `workspace/` for scratch work."
            )
        # Graph "brain" memory: tell the agent it exists and how to use it — but inject
        # NO content (that's the whole point: recall on demand to save tokens).
        parts.append(
            "\n## Graph memory (your brain)\n"
            "You have a persistent graph memory of nodes (each with a short summary) and "
            "typed relationships between them; full per-node details are stored as Markdown. "
            "Nothing from it is loaded automatically — recall only what you need. Use "
            "`brain_search` to find relevant nodes, `brain_get` to read a node's details and "
            "connections, and `brain_add_node`/`brain_link`/`brain_update` to record durable "
            "facts, people, projects, decisions, and how they relate. To forget, use "
            "`brain_delete` (remove one node by id) or `brain_clear` (erase the ENTIRE memory "
            "— use only when the user asks to clear/wipe/forget everything). Prefer this over "
            "dumping everything into the conversation, and consult it when a task may build on earlier work.\n"
            "When you save a body of information (a website, profile, document, project, "
            "etc.), build an INTERCONNECTED graph — never one giant node:\n"
            "1. Create a SEPARATE node for each distinct entity (each person, project, skill, "
            "job, place, contact method, concept, decision). Give each a specific `type` "
            "(e.g. person, project, skill, experience, education, contact, concept). Put one "
            "line in `summary` and ALL the specifics (descriptions, links, dates, numbers) in "
            "`details` (Markdown).\n"
            "2. Then `brain_link` the nodes with meaningful relationship types (e.g. person "
            "`has_skill` skill, person `built` project, project `uses` skill, person "
            "`worked_at` experience, person `reachable_via` contact). Aim for many small nodes "
            "and many links, not a few big ones.\n"
            "3. When fetching info from a website, first try its machine-readable file "
            "(`<site>/agents.md` or `<site>/llms.txt`) before the HTML home page — fetch it "
            "ONCE with `fetch_url`, then decompose it into nodes as above.\n"
            "4. Avoid duplicates: before adding, `brain_search` for the entity and reuse the "
            "existing node (link to it or `brain_update` it) instead of making a near-identical "
            "one. Adding a node whose label+type already exists automatically reuses that node "
            "rather than duplicating it.\n"
            "5. Keep node `details` to the entity's OWN facts (prose). Do NOT paste a list of its "
            "connections/relationships into `details` — links live as edges and are shown "
            "automatically, so listing them in the text just duplicates them. Never copy "
            "`brain_get` output back into a node's details."
        )
        learnings = memory_mod.load_learnings(self.paths.memory, scope)
        if learnings:
            parts.append(f"\n## Remembered learnings\n{learnings}")
        return "\n".join(parts)

    def _prepare_registry(self, scope: str | None) -> None:
        """Reset and (re)register dynamic capabilities + memory tools for a run.

        Called before each session (only one runs at a time, FR-008) so the agent's
        tool surface reflects current skills/tools/MCP and the right learning scope.
        """
        from .orchestrator import memory as memory_mod

        self.registry.reset_extras()
        discover = getattr(self.registry, "discover", None)
        if callable(discover):
            try:
                discover()
            except Exception:
                pass
        memory_mod.register_memory_tools(self.registry, self.paths.memory, scope)
        from .capabilities.brain_tools import register_brain_tools
        register_brain_tools(self.registry, self.brain)

    def start_manual_session(
        self, *, model: str | None = None, persona_id: str | None = None,
        run_config: "RunConfig | None" = None, resume_session_id: str | None = None,
        initial_message: str | None = None,
    ) -> tuple[str, int]:
        """Create and enqueue a manual interactive session. Returns (id, position).

        An optional ``run_config`` selects the model and per-run tool/MCP overrides for
        this run (FR-026); it is persisted with the session and the queued run so it
        survives a restart (FR-025a/FR-030b). When ``resume_session_id`` is given, the
        prior session's conversation is carried forward: its turns are copied into the
        new session (so the transcript shows) and seeded as engine history so the model
        continues with full context. An optional ``initial_message`` is delivered as the
        first user turn so the agent starts processing as soon as the session runs.
        """
        chosen_model = (run_config.model if run_config and run_config.model else model)
        model_key, ctx = self._resolve_model(chosen_model)
        rc_dict = run_config.to_dict() if run_config else None
        history = self._session_history(resume_session_id) if resume_session_id else None
        first_message = (initial_message or "").strip() or None
        session_id = self.store.create_session(
            trigger_type="manual", model_key=model_key, persona_id=persona_id,
            session_mode="ephemeral", context_length=ctx, run_config=rc_dict,
        )
        # Copy the prior transcript into the new session so the user sees the history.
        if resume_session_id:
            for turn in self.store.list_turns(resume_session_id):
                if turn.get("role") in ("user", "assistant", "system") and turn.get("content"):
                    self.store.add_turn(
                        session_id, role=turn["role"], content=turn["content"],
                        token_estimate=turn.get("token_estimate", 0),
                    )
        self.hub.register(session_id)
        runner = self._make_runner(
            session_id, model_key=model_key, persona_id=persona_id, context_length=ctx,
            unattended=False, initial_message=first_message, run_config=run_config,
            history=history,
        )
        position = self.queue.enqueue(
            session_id, runner,
            persist={"trigger_type": "manual", "run_config": rc_dict,
                     "initial_message": first_message},
        )
        self._schedule_status()
        return session_id, position

    def enqueue_automation(self, automation: dict) -> str:
        """Create and enqueue a session for a fired automation."""
        run_config = RunConfig.from_dict(automation.get("run_config"))
        model_key, ctx = self._resolve_model(self._config_model(run_config, automation))
        persistent = automation.get("session_mode") == "persistent"
        mode = "persistent" if persistent else "ephemeral"
        # For persistent automations, seed the prior conversation (FR-064).
        history = None
        if persistent and automation.get("persistent_session_id"):
            history = self._session_history(automation["persistent_session_id"])
        rc_dict = run_config.to_dict() if run_config else None
        session_id = self.store.create_session(
            trigger_type="automation", automation_id=automation["id"],
            persona_id=automation.get("persona_id"), model_key=model_key,
            session_mode=mode, context_length=ctx, run_config=rc_dict,
        )
        self.hub.register(session_id)
        toast.notify("automation_running", f"Automation '{automation['name']}' is running.")
        self.store.add_notification(
            type="automation_running",
            message=f"Automation '{automation['name']}' is running.",
            related_automation_id=automation["id"], related_session_id=session_id,
        )
        if persistent:
            # Remember this run as the resumable session for next time.
            self.store.update_automation(automation["id"], persistent_session_id=session_id)
        runner = self._make_runner(
            session_id, model_key=model_key, persona_id=automation.get("persona_id"),
            context_length=ctx, unattended=True, initial_message=automation.get("task"),
            automation_id=automation["id"], history=history, run_config=run_config,
        )
        self.queue.enqueue(
            session_id, runner,
            persist={"trigger_type": "automation", "automation_id": automation["id"],
                     "run_config": rc_dict, "initial_message": automation.get("task")},
        )
        self._schedule_status()
        return session_id

    def _session_history(self, session_id: str) -> list[dict]:
        """Build seed chat messages from a prior session's stored turns (FR-064)."""
        messages: list[dict] = []
        for turn in self.store.list_turns(session_id):
            role, content = turn.get("role"), turn.get("content")
            if role in ("user", "assistant", "system") and content:
                messages.append({"role": role, "content": content})
        return messages

    def _make_runner(
        self, session_id: str, *, model_key: str, persona_id: str | None,
        context_length: int, unattended: bool, initial_message: str | None,
        automation_id: str | None = None, history: list[dict] | None = None,
        run_config: "RunConfig | None" = None,
    ):
        """Build the async runner the queue executes: load → run → unload (FR-002)."""
        async def runner() -> None:
            control = self.hub.register(session_id)
            scope = automation_id or "global"
            self._prepare_registry(scope)
            # Bind the gate to this run so session-scoped folder grants apply to the
            # file tools' consent checks (single active run, FR-008).
            self.gate.current_session_id = session_id

            # Detailed per-session JSON audit log (Documents/LMStudioClaw/logs/).
            from .sessions import logbook
            logger = logbook.SessionLogger(self.paths.logs_dir, session_id, meta={
                "trigger_type": "automation" if automation_id else "manual",
                "automation_id": automation_id, "persona_id": persona_id,
                "model_key": model_key, "context_length": context_length,
                "run_config": run_config.to_dict() if run_config else None,
            })

            async def on_event(event: dict) -> None:
                await self.hub.broadcast(session_id, event)

            self.store.update_session(session_id, status="loading")
            await on_event({"type": "status", "status": "loading"})
            self._set_model_status("loading", model_key)
            await self._broadcast_status()
            try:
                loaded = await self.lifecycle.load(model_key, context_length)
            except Exception as exc:
                self.store.update_session(
                    session_id, status="failed", failure_reason=str(exc),
                    failure_point="model_load",
                )
                await on_event({"type": "error", "reason": str(exc), "point": "model_load"})
                logger.event("error", reason=str(exc), point="model_load")
                logger.finalize("failed", failure_point="model_load", failure_reason=str(exc))
                self._set_model_status("error", model_key, reason=str(exc))
                toast.notify("run_failed", f"Model load failed: {exc}")
                self.hub.unregister(session_id)
                await self._broadcast_status()
                return

            self.store.update_session(session_id, status="active")
            self._set_model_status("ready", loaded.key)
            logger.event("model_load", model=loaded.key, instance_id=loaded.instance_id,
                         context_length=loaded.context_length)
            await self._broadcast_status()
            try:
                result: SessionResult = await self.engine.run_session(
                    session_id=session_id, model_id=loaded.key,
                    system_prompt=self._build_system_prompt(persona_id, scope, session_id),
                    context_length=context_length or loaded.context_length or 4096,
                    control=control, on_event=on_event,
                    threshold=self.settings.compression_threshold,
                    idle_timeout=self.settings.session_idle_timeout,
                    max_run_duration=self.settings.max_run_duration,
                    unattended=unattended, initial_message=initial_message,
                    history=history, run_config=run_config,
                    summarize_mcp=self.settings.summarize_mcp_outputs,
                    logger=logger,
                )
                self.store.update_session(
                    session_id, status=result.status,
                    failure_reason=result.failure_reason, failure_point=result.failure_point,
                )
            except Exception as exc:
                self.store.update_session(
                    session_id, status="failed", failure_reason=str(exc),
                    failure_point="engine",
                )
                result = SessionResult("failed", failure_reason=str(exc))
            finally:
                # Always unload the model and clear session-scoped grants (FR-002/FR-022).
                try:
                    await self.lifecycle.unload(loaded.instance_id)
                except Exception:
                    pass
                logger.event("model_unload", instance_id=loaded.instance_id)
                self.store.clear_session_grants(session_id)
                if self.gate.current_session_id == session_id:
                    self.gate.current_session_id = None

            logger.finalize(result.status, failure_reason=result.failure_reason,
                            failure_point=result.failure_point)
            self._finish_notify(result, automation_id)
            self.store.prune(self.settings.retention_days)
            # Tell the session view the run is over so the badge/controls update live
            # (before the channel is torn down), then clear app-wide model status.
            try:
                await on_event({"type": "status", "status": result.status})
            except Exception:
                pass
            self.hub.unregister(session_id)
            self._set_model_status("idle", None)
            await self._broadcast_status()

        return runner

    def _finish_notify(self, result: SessionResult, automation_id: str | None) -> None:
        """Emit a completion/failure notification and update automation result."""
        ntype = "run_completed" if result.status == "completed" else "run_failed"
        self.store.add_notification(type=ntype, message=f"Session {result.status}.")
        toast.notify(ntype, f"Session {result.status}.")
        if automation_id:
            self.store.update_automation(automation_id, last_run_result=result.status)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan: start the controller on boot, shut it down on exit."""
    controller: Controller = app.state.controller
    await controller.startup()
    try:
        yield
    finally:
        await controller.shutdown()
