"""Heartbeat trigger evaluation.

A *heartbeat* is an automation whose first node is a user-authored Python function
``def trigger() -> bool``. Instead of firing purely on a schedule, the function is run
at every interval and the session is only launched when it returns truthy. This module
runs that function in an isolated, **silent** subprocess (no console window pops up on
Windows) with a hard timeout and captured stdout, and threads a tiny persistent
key/value ``state`` dict between runs so a trigger can detect *changes* (e.g. "a new
version was released since last time").

Security note: the code is authored/approved by the user (like a custom tool), so it
runs with full interpreter capabilities — network, stdlib, installed packages. It is
isolated in a subprocess so a crash or hang never affects the controller, and a timeout
bounds runaway code.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

# The harness that wraps the user's code in the child process. It loads the prior
# ``state`` dict, executes the user code, calls ``trigger()`` with stdout captured,
# then writes back the boolean result + (possibly mutated) state as JSON.
_HARNESS = r'''
import json, sys, io, contextlib, traceback

code_path, state_path, result_path = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    state = json.loads(open(state_path, "r", encoding="utf-8").read() or "{}")
except Exception:
    state = {}
if not isinstance(state, dict):
    state = {}

source = open(code_path, "r", encoding="utf-8").read()
result = {"value": False, "error": None, "stdout": "", "state": state}
buf = io.StringIO()
ns = {"state": state}
try:
    with contextlib.redirect_stdout(buf):
        exec(compile(source, "<trigger>", "exec"), ns)
        fn = ns.get("trigger")
        if not callable(fn):
            raise RuntimeError("Your code must define a function named 'trigger'.")
        value = fn()
    result["value"] = bool(value)
    new_state = ns.get("state", state)
    result["state"] = new_state if isinstance(new_state, dict) else state
except Exception:
    result["error"] = traceback.format_exc()
result["stdout"] = buf.getvalue()
try:
    json.dumps(result["state"])
except Exception:
    # State must be JSON-serializable to persist; drop it if not.
    result["state"] = state
open(result_path, "w", encoding="utf-8").write(json.dumps(result))
'''


@dataclass
class TriggerResult:
    """Outcome of evaluating a heartbeat trigger function once."""

    value: bool = False
    error: str | None = None
    stdout: str = ""
    state: dict = field(default_factory=dict)
    timed_out: bool = False


def _no_window_kwargs() -> dict:
    """Return subprocess kwargs that suppress any console window on Windows."""
    kwargs: dict = {}
    if sys.platform == "win32":
        # CREATE_NO_WINDOW prevents a flashing console for the child interpreter.
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0  # SW_HIDE
        kwargs["startupinfo"] = startupinfo
    return kwargs


def evaluate(code: str, state: dict | None = None, *, timeout: float = 30.0) -> TriggerResult:
    """Run ``trigger()`` from ``code`` in a silent subprocess and return the result.

    Parameters
    ----------
    code:
        Python source that must define ``def trigger() -> bool``. It may read/write the
        module-global ``state`` dict to persist small values across runs.
    state:
        The previously persisted state dict (passed in, returned possibly mutated).
    timeout:
        Hard wall-clock limit in seconds. On timeout the result is ``value=False`` with
        ``timed_out=True`` and the original state preserved.
    """
    state = dict(state or {})
    with tempfile.TemporaryDirectory(prefix="hb_trigger_") as tmp:
        tmp_path = Path(tmp)
        code_file = tmp_path / "code.py"
        state_file = tmp_path / "state.json"
        result_file = tmp_path / "result.json"
        harness_file = tmp_path / "harness.py"
        code_file.write_text(code, encoding="utf-8")
        state_file.write_text(json.dumps(state), encoding="utf-8")
        harness_file.write_text(_HARNESS, encoding="utf-8")

        try:
            subprocess.run(
                [sys.executable, str(harness_file), str(code_file),
                 str(state_file), str(result_file)],
                capture_output=True,
                timeout=timeout,
                **_no_window_kwargs(),
            )
        except subprocess.TimeoutExpired:
            return TriggerResult(
                value=False, error=f"Trigger timed out after {timeout:g}s.",
                state=state, timed_out=True,
            )
        except Exception as exc:  # pragma: no cover - spawn failure is environment-specific
            return TriggerResult(value=False, error=f"Failed to run trigger: {exc}", state=state)

        try:
            payload = json.loads(result_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return TriggerResult(
                value=False, error="Trigger produced no result (it may have crashed).",
                state=state,
            )

    new_state = payload.get("state")
    return TriggerResult(
        value=bool(payload.get("value")),
        error=payload.get("error"),
        stdout=payload.get("stdout", "") or "",
        state=new_state if isinstance(new_state, dict) else state,
    )
