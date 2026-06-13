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

        self.connection = load_connection(
            base_url=self.settings.lmstudio_base_url,
            api_key=self.vault.inject({"k": self.settings.lmstudio_api_key_ref}).get("k"),
        )
        self.http = make_client(self.connection)
        self.lifecycle = ModelLifecycle(self.http, self.connection)

        self.gate = PathGate(self.paths, self.store)
        self.registry = CapabilityRegistry(self.paths, self.store, self.gate)
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
        self.served_url: str = f"http://localhost:{self.settings.web_port}"

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

    async def shutdown(self) -> None:
        """Stop the queue/scheduler, unload any model, close clients (FR-043)."""
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
        self.http.close()
        self.store.close()

    # -- settings -----------------------------------------------------------

    def save(self) -> None:
        """Persist current settings to disk."""
        save_settings(self.paths.settings_path, self.settings)

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

    def _build_system_prompt(self, persona_id: str | None, scope: str | None = None) -> str:
        """Compose the system prompt from persona, enabled skills, and learnings."""
        from .orchestrator import memory as memory_mod

        persona = persona_mod.resolve(self.store, persona_id)
        parts = [persona.instructions]
        for skill in self.registry.enabled_skills():
            parts.append(f"\n## Skill: {skill.name}\n{skill.instructions}")
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

    def start_manual_session(
        self, *, model: str | None = None, persona_id: str | None = None,
        run_config: "RunConfig | None" = None,
    ) -> tuple[str, int]:
        """Create and enqueue a manual interactive session. Returns (id, position).

        An optional ``run_config`` selects the model and per-run tool/MCP overrides for
        this run (FR-026); it is persisted with the session and the queued run so it
        survives a restart (FR-025a/FR-030b).
        """
        chosen_model = (run_config.model if run_config and run_config.model else model)
        model_key, ctx = self._resolve_model(chosen_model)
        rc_dict = run_config.to_dict() if run_config else None
        session_id = self.store.create_session(
            trigger_type="manual", model_key=model_key, persona_id=persona_id,
            session_mode="ephemeral", context_length=ctx, run_config=rc_dict,
        )
        self.hub.register(session_id)
        runner = self._make_runner(
            session_id, model_key=model_key, persona_id=persona_id, context_length=ctx,
            unattended=False, initial_message=None, run_config=run_config,
        )
        position = self.queue.enqueue(
            session_id, runner,
            persist={"trigger_type": "manual", "run_config": rc_dict, "initial_message": None},
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
                self._set_model_status("error", model_key, reason=str(exc))
                toast.notify("run_failed", f"Model load failed: {exc}")
                self.hub.unregister(session_id)
                await self._broadcast_status()
                return

            self.store.update_session(session_id, status="active")
            self._set_model_status("ready", loaded.key)
            await self._broadcast_status()
            try:
                result: SessionResult = await self.engine.run_session(
                    session_id=session_id, model_id=loaded.key,
                    system_prompt=self._build_system_prompt(persona_id, scope),
                    context_length=context_length or loaded.context_length or 4096,
                    control=control, on_event=on_event,
                    threshold=self.settings.compression_threshold,
                    idle_timeout=self.settings.session_idle_timeout,
                    max_run_duration=self.settings.max_run_duration,
                    unattended=unattended, initial_message=initial_message,
                    history=history, run_config=run_config,
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
                self.store.clear_session_grants(session_id)

            self._finish_notify(result, automation_id)
            self.store.prune(self.settings.retention_days)
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
