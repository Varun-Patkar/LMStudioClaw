"""Consent-gated file tools for the agent (feature 002).

This module implements the file-manipulation half of the default toolset
(`read_file` with range, `list_dir`, `write_file`, `edit`, `grep`, `find`). The
handlers live here (rather than in ``registry.py``) so the registry stays within the
project's ~500-line modularity limit (Constitution I). Every handler routes through
the consent/path gate via :func:`authorize` — access outside the workspace or an
active grant is blocked or prompts the user (FR-015).

The :class:`~lmstudioclaw.capabilities.registry.ToolResult` type is imported lazily
at call sites' module-load time; ``registry`` never imports this module at top level,
so there is no import cycle.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from ..consent.path_gate import Access, DecisionKind
from .registry import ToolResult

# Bound tool output so a single call can never flood the transcript / context.
_MAX_OUTPUT = 50_000
_MAX_MATCHES = 200
# Bound the before/after snapshots sent to the UI for the diff view (display-only).
_MAX_DIFF_CHARS = 20_000


async def authorize(gate, path: str, access: Access, consent) -> "Path | str":
    """Authorize ``path`` via the gate, prompting through ``consent`` if needed.

    Returns a resolved :class:`Path` on success or a human-readable error string on
    denial. This is the single authorization helper shared by every file tool and by
    the registry's built-in handlers (FR-015).
    """
    decision = gate.authorize(path, access)
    if decision.kind == DecisionKind.ALLOW:
        return Path(decision.path)
    if decision.kind == DecisionKind.DENY:
        return f"Access denied: {decision.reason}"
    # NEEDS_CONSENT — ask the user via the engine-supplied callback.
    granted = await consent(decision.path, access)
    if not granted:
        return "Access denied by user."
    recheck = gate.authorize(path, access)
    if recheck.kind == DecisionKind.ALLOW:
        return Path(recheck.path)
    return "Access denied: still not permitted after consent."


def _atomic_write(target: Path, text: str) -> None:
    """Write ``text`` to ``target`` atomically (temp file + replace)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".tmp-edit-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


async def read_file(gate, consent, *, path: str,
                    start_line: int | None = None, end_line: int | None = None) -> ToolResult:
    """Read a UTF-8 text file, optionally only an inclusive 1-based line range (FR-009)."""
    resolved = await authorize(gate, path, Access.READ, consent)
    if isinstance(resolved, str):
        return ToolResult(False, "", error=resolved)
    try:
        text = resolved.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return ToolResult(False, "", error=f"Read failed: {exc}")
    meta = {"action": "read", "path": str(resolved), "name": resolved.name,
            "start_line": start_line, "end_line": end_line}
    if start_line is None and end_line is None:
        return ToolResult(True, text[:_MAX_OUTPUT], meta=meta)
    lines = text.splitlines()
    lo = max(1, start_line or 1)
    hi = min(len(lines), end_line or len(lines))
    if lo > hi:
        return ToolResult(False, "", error=f"Invalid range: {lo}-{hi} (file has {len(lines)} lines)")
    chunk = "\n".join(lines[lo - 1:hi])
    return ToolResult(True, chunk[:_MAX_OUTPUT], meta=meta)


async def list_dir(gate, consent, *, path: str) -> ToolResult:
    """List the entries of a directory after consent authorization (FR-013)."""
    resolved = await authorize(gate, path, Access.READ, consent)
    if isinstance(resolved, str):
        return ToolResult(False, "", error=resolved)
    try:
        entries = sorted(f"{p.name}/" if p.is_dir() else p.name for p in resolved.iterdir())
        meta = {"action": "list", "path": str(resolved), "name": resolved.name,
                "count": len(entries)}
        return ToolResult(True, "\n".join(entries) or "(empty)", meta=meta)
    except OSError as exc:
        return ToolResult(False, "", error=f"List failed: {exc}")


async def write_file(gate, consent, *, path: str, content: str) -> ToolResult:
    """Create or overwrite a file, creating parent folders as needed (FR-011)."""
    resolved = await authorize(gate, path, Access.READ_WRITE, consent)
    if isinstance(resolved, str):
        return ToolResult(False, "", error=resolved)
    # Snapshot prior content (if any) so the UI can show a create-vs-overwrite diff.
    existed = resolved.exists()
    old = ""
    if existed:
        try:
            old = resolved.read_text(encoding="utf-8", errors="replace")
        except OSError:
            old = ""
    try:
        _atomic_write(resolved, content)
        meta = {"action": "overwrite" if existed else "create",
                "path": str(resolved), "name": resolved.name,
                "old": old[:_MAX_DIFF_CHARS], "new": content[:_MAX_DIFF_CHARS]}
        return ToolResult(True, f"Wrote {len(content)} chars to {resolved}", meta=meta)
    except OSError as exc:
        return ToolResult(False, "", error=f"Write failed: {exc}")


async def edit(gate, consent, *, path: str,
               old_string: str | None = None, new_string: str | None = None,
               start_line: int | None = None, end_line: int | None = None,
               new_content: str | None = None) -> ToolResult:
    """Precise in-place edit in one of two overloaded modes (FR-010).

    * **exact-string**: ``old_string`` must occur exactly once; it is replaced by
      ``new_string``. Zero or multiple matches fail and leave the file unchanged.
    * **line-range**: lines ``start_line``..``end_line`` (inclusive, 1-based) are
      replaced by ``new_content``. An out-of-bounds range fails without writing.

    The agent should read the relevant section before editing so the target is
    correct (FR-016).
    """
    resolved = await authorize(gate, path, Access.READ_WRITE, consent)
    if isinstance(resolved, str):
        return ToolResult(False, "", error=resolved)
    try:
        text = resolved.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return ToolResult(False, "", error=f"Read-before-edit failed: {exc}")

    if old_string is not None:
        count = text.count(old_string)
        if count == 0:
            return ToolResult(False, "", error="Edit target not found; file unchanged.")
        if count > 1:
            return ToolResult(False, "", error=f"Edit target is ambiguous ({count} matches); file unchanged.")
        updated = text.replace(old_string, new_string or "", 1)
    elif start_line is not None and new_content is not None:
        lines = text.splitlines()
        hi = end_line if end_line is not None else start_line
        if start_line < 1 or hi > len(lines) or start_line > hi:
            return ToolResult(False, "", error=f"Line range {start_line}-{hi} out of bounds "
                                                f"(file has {len(lines)} lines); file unchanged.")
        new_lines = new_content.splitlines()
        updated = "\n".join(lines[:start_line - 1] + new_lines + lines[hi:])
        if text.endswith("\n"):
            updated += "\n"
    else:
        return ToolResult(False, "", error="edit requires either old_string (exact) or "
                                            "start_line+new_content (line-range).")
    try:
        _atomic_write(resolved, updated)
    except OSError as exc:
        return ToolResult(False, "", error=f"Write failed: {exc}")
    meta = {"action": "edit", "path": str(resolved), "name": resolved.name,
            "old": text[:_MAX_DIFF_CHARS], "new": updated[:_MAX_DIFF_CHARS]}
    return ToolResult(True, f"Edited {resolved}", meta=meta)


def _base_dir(gate, path: str | None) -> str:
    """Resolve the search base: the given path or the workspace default."""
    if path:
        return path
    return str(gate._workspace)  # workspace is always allowed (FR-020)


async def grep(gate, consent, *, pattern: str, path: str | None = None,
               glob: str | None = None) -> ToolResult:
    """Search file contents for a regex ``pattern`` and return matches (FR-012)."""
    base = await authorize(gate, _base_dir(gate, path), Access.READ, consent)
    if isinstance(base, str):
        return ToolResult(False, "", error=base)
    try:
        rx = re.compile(pattern)
    except re.error as exc:
        return ToolResult(False, "", error=f"Bad pattern: {exc}")
    files = base.rglob(glob) if glob else base.rglob("*")
    out: list[str] = []
    for fp in files:
        if not fp.is_file():
            continue
        try:
            for n, line in enumerate(fp.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if rx.search(line):
                    out.append(f"{fp}:{n}: {line.strip()[:200]}")
                    if len(out) >= _MAX_MATCHES:
                        out.append("… (truncated)")
                        return ToolResult(True, "\n".join(out))
        except OSError:
            continue
    return ToolResult(True, "\n".join(out) or "(no matches)")


async def find(gate, consent, *, glob: str, path: str | None = None) -> ToolResult:
    """Return file paths matching a ``glob`` under the base directory (FR-012)."""
    base = await authorize(gate, _base_dir(gate, path), Access.READ, consent)
    if isinstance(base, str):
        return ToolResult(False, "", error=base)
    matches = [str(p) for p in base.rglob(glob)]
    if len(matches) > _MAX_MATCHES:
        matches = matches[:_MAX_MATCHES] + ["… (truncated)"]
    return ToolResult(True, "\n".join(matches) or "(no files)")
