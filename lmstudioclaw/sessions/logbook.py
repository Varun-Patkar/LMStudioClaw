"""Detailed per-session JSON logging + the standalone HTML log viewer.

Every session writes a fully-detailed JSON record to ``Documents/LMStudioClaw/logs/
<session_id>.json``. Unlike the SQLite turn store (which keeps the conversation for
the UI), this log captures the **exact, ordered** runtime activity for auditing:

* the full system prompt and every message array sent to the model API,
* every tool call (name + arguments) and its **full** result, in order,
* compaction/compression events including the generated summary content,
* model load/unload, steering, warnings, and errors.

This is the record to review when a skill or tool output tries to sneak an
instruction past the model (prompt injection): the precise sequence of what went in
and came out is preserved verbatim.

Writes are best-effort (Constitution II): a logging hiccup never interrupts a run. A
standalone ``index.html`` viewer is dropped into the logs folder on first run; it
lists all logs and renders any one prettified (with a raw-JSON toggle), reading the
files locally via the browser File System Access API — no server required.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

# The bundled standalone viewer copied into the logs folder.
_VIEWER_SRC = Path(__file__).resolve().parent / "logs_viewer.html"


def _now() -> str:
    """Current UTC timestamp as ISO-8601."""
    return datetime.now(timezone.utc).isoformat()


def _atomic_write(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically (temp file + replace); best-effort."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-log-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except OSError:
        pass  # logging must never break a run


class SessionLogger:
    """Accumulates a session's detailed event log and flushes it to JSON on disk.

    One instance per session (sessions run one-at-a-time). Every :meth:`event` appends
    an ordered, timestamped record and rewrites the file so a crash mid-run still
    leaves a usable log on disk.
    """

    def __init__(self, logs_dir: Path, session_id: str, meta: dict | None = None) -> None:
        """Create the log document and write its initial (empty) state to disk."""
        self._path = logs_dir / f"{session_id}.json"
        self._lock = threading.Lock()
        self._seq = 0
        self._doc: dict = {
            "schema": 1,
            "session_id": session_id,
            "created_at": _now(),
            "ended_at": None,
            "status": "running",
            "meta": meta or {},
            "events": [],
        }
        self._flush()

    def event(self, type: str, **data) -> None:
        """Append an ordered, timestamped event and persist the log (best-effort)."""
        try:
            with self._lock:
                self._seq += 1
                self._doc["events"].append(
                    {"seq": self._seq, "at": _now(), "type": type, **data}
                )
                self._flush()
        except Exception:
            pass  # never let logging interrupt the run

    def finalize(self, status: str, **data) -> None:
        """Mark the session ended with a final status and flush."""
        try:
            with self._lock:
                self._doc["status"] = status
                self._doc["ended_at"] = _now()
            self.event("session_end", status=status, **data)
        except Exception:
            pass

    def _flush(self) -> None:
        """Serialize the whole document to disk (callers hold the lock)."""
        _atomic_write(self._path, json.dumps(self._doc, indent=2, default=str, ensure_ascii=False))


def ensure_logs_assets(logs_dir: Path) -> None:
    """Create the logs dir and (re)write the standalone HTML viewer (best-effort).

    Refreshing on every startup keeps the bundled viewer current without a migration.
    """
    try:
        logs_dir.mkdir(parents=True, exist_ok=True)
        if _VIEWER_SRC.exists():
            _atomic_write(logs_dir / "index.html", _VIEWER_SRC.read_text(encoding="utf-8"))
    except OSError:
        pass


def list_logs(logs_dir: Path) -> list[dict]:
    """Return a newest-first manifest of available logs (summary fields only)."""
    if not logs_dir.exists():
        return []
    out: list[dict] = []
    for path in logs_dir.glob("*.json"):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        meta = doc.get("meta") or {}
        out.append({
            "session_id": doc.get("session_id", path.stem),
            "created_at": doc.get("created_at"),
            "ended_at": doc.get("ended_at"),
            "status": doc.get("status"),
            "event_count": len(doc.get("events") or []),
            "trigger_type": meta.get("trigger_type"),
            "model_key": meta.get("model_key"),
        })
    out.sort(key=lambda d: d.get("created_at") or "", reverse=True)
    return out


def read_log(logs_dir: Path, session_id: str) -> dict | None:
    """Return the full log document for a session, or None if missing/unreadable."""
    path = logs_dir / f"{session_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def delete_log(logs_dir: Path, session_id: str) -> None:
    """Delete a session's detailed JSON log file (best-effort; ignores missing/errors)."""
    try:
        (logs_dir / f"{session_id}.json").unlink(missing_ok=True)
    except OSError:
        pass
