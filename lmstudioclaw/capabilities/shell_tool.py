"""Consent-gated PowerShell tool for the agent (feature 002).

Runs a PowerShell command on the Windows host with the same consent model as the
file tools (FR-014/FR-015a):

* execution starts in the **workspace** by default; a different ``cwd`` is authorized
  through the consent/path gate, so a directory outside the workspace or an active
  grant raises the standard consent prompt and a permanent grant persists in Settings;
* the secrets store and app-internal areas remain a hard deny-list (enforced by the
  gate);
* the command is passed to PowerShell as a single ``-Command`` argument — untrusted
  text is never interpolated into an outer shell;
* execution is bounded by a timeout and stdout/stderr are truncated so a run can
  never stall indefinitely or flood the transcript.

A shell can technically reach any path the user can; the gate cannot intercept every
in-process file operation a child process performs. The cwd anchoring, timeout, output
bounds, and hard deny-list are the practical mitigations for the single-user-machine
threat model (see ``research.md`` §4); deeper OS sandboxing is a future hardening item.
"""

from __future__ import annotations

import asyncio
import shutil

from ..consent.path_gate import Access
from .file_tools import authorize
from .registry import ToolResult

# Per-call wall-clock limit and output cap (bounded execution, Constitution V).
SHELL_TIMEOUT = 60
_MAX_OUTPUT = 20_000


def _powershell_exe() -> str:
    """Return the PowerShell executable (prefer pwsh 7+, fall back to Windows PowerShell)."""
    return shutil.which("pwsh") or shutil.which("powershell") or "powershell"


async def powershell(gate, consent, *, command: str, cwd: str | None = None) -> ToolResult:
    """Run a PowerShell command from the workspace (or a consented ``cwd``)."""
    base = cwd or str(gate._workspace)
    resolved = await authorize(gate, base, Access.READ_WRITE, consent)
    if isinstance(resolved, str):
        return ToolResult(False, "", error=resolved)

    exe = _powershell_exe()
    try:
        proc = await asyncio.create_subprocess_exec(
            exe, "-NoProfile", "-NonInteractive", "-Command", command,
            cwd=str(resolved),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        return ToolResult(False, "", error=f"Could not start PowerShell: {exc}")

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=SHELL_TIMEOUT)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return ToolResult(False, "", error=f"Command timed out after {SHELL_TIMEOUT}s")

    out = (stdout.decode("utf-8", errors="replace") if stdout else "")[:_MAX_OUTPUT]
    err = (stderr.decode("utf-8", errors="replace") if stderr else "")[:_MAX_OUTPUT]
    if proc.returncode != 0:
        detail = err or f"exit code {proc.returncode}"
        # Non-zero exit is surfaced as an error but keeps any partial stdout.
        return ToolResult(False, out, error=f"PowerShell exited {proc.returncode}: {detail}")
    return ToolResult(True, out or "(no output)")
