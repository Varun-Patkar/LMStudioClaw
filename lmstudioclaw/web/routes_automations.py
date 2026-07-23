"""Automation REST routes (expanded in Phase 6 / US4)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .routes_sessions import RunConfigIn

router = APIRouter(prefix="/api/automations", tags=["automations"])

# Minimum interval (minutes) for a heartbeat: evaluating user Python more often than
# this wastes CPU and a model can't even initialize within a tighter window.
MIN_HEARTBEAT_MINUTES = 5

_UNIT_MINUTES = {"minutes": 1, "hours": 60, "days": 1440}


class AutomationIn(BaseModel):
    """Create/update payload for an automation."""

    name: str
    task: str
    schedule_type: str = Field(pattern="^(daily|interval)$")
    daily_days: list[int] | None = None
    daily_time: str | None = None
    interval_unit: str | None = None
    interval_value: int | None = None
    session_mode: str = "new"
    persona_id: str | None = None
    model_override: str | None = None
    run_config: RunConfigIn | None = None
    enabled: bool = True
    # Heartbeat: references a standalone trigger (see routes_triggers) by id when
    # ``trigger_type='python'``. ``time`` keeps the legacy fixed-schedule behaviour.
    trigger_type: str = Field(default="time", pattern="^(time|python)$")
    trigger_id: str | None = None


def _ctrl(request: Request):
    """Return the controller from app state."""
    return request.app.state.controller


@router.get("")
async def list_automations(request: Request) -> list[dict]:
    """List all automations with schedule + status metadata."""
    return _ctrl(request).store.list_automations()


@router.post("")
async def create_automation(payload: AutomationIn, request: Request) -> dict:
    """Create an automation and compute its next fire time."""
    ctrl = _ctrl(request)
    data = payload.model_dump()
    if not data["name"].strip():
        raise HTTPException(422, "Automation name is required.")
    if not data["task"].strip():
        raise HTTPException(422, "Task / instruction is required.")
    _validate_schedule(data, ctrl)
    aid = ctrl.store.create_automation(data)
    if ctrl.scheduler is not None:
        ctrl.scheduler.refresh()
    if getattr(ctrl.settings, "use_task_scheduler", False):
        ctrl.sync_automation_tasks()
    return {"id": aid}


@router.patch("/{automation_id}")
async def update_automation(automation_id: str, payload: dict, request: Request) -> dict:
    """Edit / enable / disable an automation."""
    ctrl = _ctrl(request)
    if ctrl.store.get_automation(automation_id) is None:
        raise HTTPException(404, "Automation not found")
    ctrl.store.update_automation(automation_id, **payload)
    if ctrl.scheduler is not None:
        ctrl.scheduler.refresh()
    if getattr(ctrl.settings, "use_task_scheduler", False):
        ctrl.sync_automation_tasks()
    return {"ok": True}


@router.delete("/{automation_id}")
async def delete_automation(automation_id: str, request: Request) -> dict:
    """Delete an automation."""
    ctrl = _ctrl(request)
    ctrl.store.delete_automation(automation_id)
    if ctrl.scheduler is not None:
        ctrl.scheduler.refresh()
    if getattr(ctrl.settings, "use_task_scheduler", False):
        from ..automations import tasksched
        try:
            tasksched.unregister(automation_id)
        except Exception:
            pass
    return {"ok": True}


@router.post("/{automation_id}/run")
async def run_now(automation_id: str, request: Request) -> dict:
    """Run an automation immediately (enters the queue)."""
    ctrl = _ctrl(request)
    automation = ctrl.store.get_automation(automation_id)
    if automation is None:
        raise HTTPException(404, "Automation not found")
    session_id = ctrl.enqueue_automation(automation)
    return {"session_id": session_id}


def _validate_schedule(data: dict, ctrl) -> None:
    """Validate schedule + trigger fields per data-model rules."""
    if data.get("trigger_type") == "python":
        trigger_id = data.get("trigger_id")
        if not trigger_id or ctrl.store.get_trigger(trigger_id) is None:
            raise HTTPException(422, "A heartbeat must reference an existing trigger.")
        if data["schedule_type"] != "interval":
            raise HTTPException(422, "A heartbeat must use an interval schedule.")
        minutes = _UNIT_MINUTES.get(data.get("interval_unit"), 0) * (data.get("interval_value") or 0)
        if minutes < MIN_HEARTBEAT_MINUTES:
            raise HTTPException(
                422, f"A heartbeat interval must be at least {MIN_HEARTBEAT_MINUTES} minutes."
            )
        return
    if data["schedule_type"] == "daily":
        if not data.get("daily_days") or not data.get("daily_time"):
            raise HTTPException(422, "Daily schedule requires daily_days and daily_time")
    else:
        if not data.get("interval_unit") or not (data.get("interval_value") or 0) > 0:
            raise HTTPException(422, "Interval schedule requires interval_unit and value > 0")
