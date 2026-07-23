"""SQLite persistence for sessions, turns, automations, grants, capabilities,
personas, notifications, and compression events.

All writes are best-effort/transactional (Constitution II): a storage hiccup must
not crash the controller. The store owns a single connection guarded by a lock,
since SQLite connections are not safe to share across threads without care.

Schema follows ``specs/001-agent-runtime/data-model.md``. Structured records live
here; capability *content* and learnings live in files (see capabilities/ and
orchestrator/memory.py).
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    trigger_type TEXT NOT NULL,
    automation_id TEXT,
    persona_id TEXT,
    model_key TEXT,
    status TEXT NOT NULL,
    session_mode TEXT NOT NULL DEFAULT 'ephemeral',
    started_at TEXT,
    ended_at TEXT,
    failure_reason TEXT,
    failure_point TEXT,
    context_length INTEGER DEFAULT 0,
    run_config TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS turns (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT,
    tool_call TEXT,
    tool_result TEXT,
    token_estimate INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS automations (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    task TEXT NOT NULL,
    schedule_type TEXT NOT NULL,
    daily_days TEXT,
    daily_time TEXT,
    interval_unit TEXT,
    interval_value INTEGER,
    session_mode TEXT NOT NULL DEFAULT 'new',
    persistent_session_id TEXT,
    persona_id TEXT,
    model_override TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_run_at TEXT,
    last_run_result TEXT,
    next_run_at TEXT,
    run_config TEXT,
    trigger_type TEXT NOT NULL DEFAULT 'time',
    trigger_prompt TEXT,
    trigger_code TEXT,
    trigger_state TEXT,
    last_trigger_at TEXT,
    last_trigger_value INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS personas (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    instructions TEXT NOT NULL,
    is_default INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS capabilities (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    source_path TEXT,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'valid',
    enabled INTEGER NOT NULL DEFAULT 0,
    trust_confirmed INTEGER NOT NULL DEFAULT 0,
    secret_refs TEXT,
    metadata TEXT,
    added_by TEXT NOT NULL DEFAULT 'user',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS grants (
    id TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    scope TEXT NOT NULL,
    session_id TEXT,
    access TEXT NOT NULL DEFAULT 'read',
    active INTEGER NOT NULL DEFAULT 1,
    granted_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    message TEXT NOT NULL,
    related_session_id TEXT,
    related_automation_id TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS compression_events (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    at TEXT NOT NULL,
    tokens_before INTEGER NOT NULL,
    tokens_after INTEGER NOT NULL,
    summary_turn_id TEXT
);

CREATE TABLE IF NOT EXISTS queued_runs (
    id TEXT PRIMARY KEY,
    trigger_type TEXT NOT NULL,
    automation_id TEXT,
    run_config TEXT,
    initial_message TEXT,
    position INTEGER NOT NULL,
    started INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS triggers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    prompt TEXT,
    code TEXT NOT NULL,
    state TEXT,
    last_run_at TEXT,
    last_value INTEGER,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trigger_runs (
    id TEXT PRIMARY KEY,
    trigger_id TEXT NOT NULL,
    at TEXT NOT NULL,
    value INTEGER NOT NULL DEFAULT 0,
    fired INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    stdout TEXT,
    duration_ms INTEGER,
    session_id TEXT,
    automation_id TEXT
);
"""


def _now() -> str:
    """Return the current UTC timestamp as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    """Generate a fresh UUID4 string for a primary key."""
    return str(uuid.uuid4())


class Store:
    """Thread-safe SQLite store with best-effort writes.

    A single connection is shared behind a re-entrant lock. ``check_same_thread``
    is disabled because controller work runs across asyncio executors and the tray
    thread; the lock serializes all access.
    """

    def __init__(self, db_path: Path) -> None:
        """Open (and create) the database at ``db_path`` and apply the schema."""
        self._lock = threading.RLock()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
        self._migrate()

    def _migrate(self) -> None:
        """Best-effort additive migrations for databases created by an earlier version.

        Adds columns introduced in feature 002 (per-run config) to pre-existing
        ``sessions``/``automations`` tables. Each ALTER is wrapped so a column that
        already exists (or any storage hiccup) never crashes startup.
        """
        for table, column in (
            ("sessions", "run_config TEXT"),
            ("automations", "run_config TEXT"),
            ("capabilities", "metadata TEXT"),
            # Heartbeat triggers (Python-based conditional automations).
            ("automations", "trigger_type TEXT NOT NULL DEFAULT 'time'"),
            ("automations", "trigger_prompt TEXT"),
            ("automations", "trigger_code TEXT"),
            ("automations", "trigger_state TEXT"),
            ("automations", "last_trigger_at TEXT"),
            ("automations", "last_trigger_value INTEGER"),
            ("automations", "trigger_id TEXT"),
        ):
            with self._lock:
                try:
                    self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column}")
                    self._conn.commit()
                except sqlite3.Error:
                    pass  # column already present or table will be created by schema
        self._migrate_trigger_runs()

    def _migrate_trigger_runs(self) -> None:
        """Re-key an early ``trigger_runs`` table (keyed by automation_id) to trigger_id.

        The heartbeat feature briefly stored trigger history per-automation; triggers are
        now standalone entities, so history is keyed by ``trigger_id``. This was never a
        released schema, so the rare old table is simply rebuilt (best-effort).
        """
        with self._lock:
            try:
                cols = {r[1] for r in self._conn.execute("PRAGMA table_info(trigger_runs)")}
                if cols and "trigger_id" not in cols:
                    self._conn.execute("DROP TABLE trigger_runs")
                    self._conn.execute(
                        """CREATE TABLE trigger_runs (
                               id TEXT PRIMARY KEY, trigger_id TEXT NOT NULL, at TEXT NOT NULL,
                               value INTEGER NOT NULL DEFAULT 0, fired INTEGER NOT NULL DEFAULT 0,
                               error TEXT, stdout TEXT, duration_ms INTEGER, session_id TEXT,
                               automation_id TEXT)"""
                    )
                    self._conn.commit()
            except sqlite3.Error:
                pass

    def close(self) -> None:
        """Close the underlying connection (best-effort)."""
        try:
            self._conn.close()
        except sqlite3.Error:
            pass

    # -- low-level helpers --------------------------------------------------

    def _exec(self, sql: str, params: tuple = ()) -> None:
        """Execute a write statement, committing; swallow errors (best-effort)."""
        with self._lock:
            try:
                self._conn.execute(sql, params)
                self._conn.commit()
            except sqlite3.Error:
                # Best-effort: never crash the controller on a storage hiccup.
                pass

    def _query(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        """Run a SELECT and return rows as plain dicts."""
        with self._lock:
            try:
                cur = self._conn.execute(sql, params)
                return [dict(r) for r in cur.fetchall()]
            except sqlite3.Error:
                return []

    def _query_one(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        """Run a SELECT expected to return a single row (or None)."""
        rows = self._query(sql, params)
        return rows[0] if rows else None

    # -- Sessions -----------------------------------------------------------

    def create_session(
        self,
        *,
        trigger_type: str,
        model_key: str | None = None,
        persona_id: str | None = None,
        automation_id: str | None = None,
        session_mode: str = "ephemeral",
        context_length: int = 0,
        session_id: str | None = None,
        run_config: dict | None = None,
    ) -> str:
        """Insert a new session row (status ``queued``) and return its id."""
        sid = session_id or new_id()
        self._exec(
            """INSERT INTO sessions
               (id, trigger_type, automation_id, persona_id, model_key, status,
                session_mode, context_length, run_config, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (sid, trigger_type, automation_id, persona_id, model_key, "queued",
             session_mode, context_length,
             json.dumps(run_config) if run_config is not None else None, _now()),
        )
        return sid

    # -- Persisted run queue (FR-025a) -------------------------------------

    def enqueue_run(
        self, *, run_id: str, trigger_type: str, automation_id: str | None = None,
        run_config: dict | None = None, initial_message: str | None = None,
    ) -> None:
        """Persist a queued run so the FIFO queue survives an app/PC restart."""
        with self._lock:
            try:
                cur = self._conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM queued_runs")
                position = cur.fetchone()[0]
                self._conn.execute(
                    """INSERT OR REPLACE INTO queued_runs
                       (id, trigger_type, automation_id, run_config, initial_message,
                        position, started, created_at)
                       VALUES (?,?,?,?,?,?,0,?)""",
                    (run_id, trigger_type, automation_id,
                     json.dumps(run_config) if run_config is not None else None,
                     initial_message, position, _now()),
                )
                self._conn.commit()
            except sqlite3.Error:
                pass  # best-effort: queue UI degrades gracefully if persistence hiccups

    def mark_run_started(self, run_id: str) -> None:
        """Flag a persisted run as dequeued/started."""
        self._exec("UPDATE queued_runs SET started=1 WHERE id=?", (run_id,))

    def remove_queued_run(self, run_id: str) -> None:
        """Remove a persisted run row (cancelled, completed, or failed)."""
        self._exec("DELETE FROM queued_runs WHERE id=?", (run_id,))

    def list_queued_runs(self, *, pending_only: bool = False) -> list[dict[str, Any]]:
        """Return persisted runs in FIFO order (optionally only not-yet-started)."""
        where = "WHERE started=0" if pending_only else ""
        rows = self._query(f"SELECT * FROM queued_runs {where} ORDER BY position ASC")
        for row in rows:
            if row.get("run_config"):
                try:
                    row["run_config"] = json.loads(row["run_config"])
                except (TypeError, ValueError):
                    row["run_config"] = None
        return rows

    def update_session(self, session_id: str, **fields: Any) -> None:
        """Patch session fields; stamps ``started_at``/``ended_at`` by status."""
        if "status" in fields:
            status = fields["status"]
            if status == "loading" and "started_at" not in fields:
                fields["started_at"] = _now()
            if status in ("completed", "failed", "stopped") and "ended_at" not in fields:
                fields["ended_at"] = _now()
        cols = ", ".join(f"{k}=?" for k in fields)
        self._exec(
            f"UPDATE sessions SET {cols} WHERE id=?",
            (*fields.values(), session_id),
        )

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        """Return a single session row by id."""
        return self._query_one("SELECT * FROM sessions WHERE id=?", (session_id,))

    def list_sessions(
        self, *, status: str | None = None, trigger: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        """List sessions, newest first, optionally filtered by status/trigger."""
        clauses, params = [], []
        if status:
            clauses.append("status=?")
            params.append(status)
        if trigger:
            clauses.append("trigger_type=?")
            params.append(trigger)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return self._query(
            f"SELECT * FROM sessions {where} ORDER BY created_at DESC LIMIT ?",
            (*params, limit),
        )

    def active_or_loading(self) -> dict[str, Any] | None:
        """Return the single session currently ``loading`` or ``active`` (FR-008)."""
        return self._query_one(
            "SELECT * FROM sessions WHERE status IN ('loading','active') LIMIT 1"
        )

    def delete_session(self, session_id: str) -> None:
        """Delete a session and its turns/compression events (best-effort)."""
        self._exec("DELETE FROM turns WHERE session_id=?", (session_id,))
        self._exec("DELETE FROM compression_events WHERE session_id=?", (session_id,))
        self._exec("DELETE FROM sessions WHERE id=?", (session_id,))

    # -- Turns --------------------------------------------------------------

    def add_turn(
        self,
        session_id: str,
        *,
        role: str,
        content: str | None = None,
        tool_call: dict | None = None,
        tool_result: dict | None = None,
        token_estimate: int = 0,
    ) -> str:
        """Append a conversation turn to a session and return its id."""
        tid = new_id()
        idx_row = self._query_one(
            "SELECT COALESCE(MAX(idx), -1) + 1 AS n FROM turns WHERE session_id=?",
            (session_id,),
        )
        idx = idx_row["n"] if idx_row else 0
        self._exec(
            """INSERT INTO turns
               (id, session_id, idx, role, content, tool_call, tool_result,
                token_estimate, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (tid, session_id, idx, role, content,
             json.dumps(tool_call) if tool_call else None,
             json.dumps(tool_result) if tool_result else None,
             token_estimate, _now()),
        )
        return tid

    def list_turns(self, session_id: str) -> list[dict[str, Any]]:
        """Return all turns for a session in order."""
        return self._query(
            "SELECT * FROM turns WHERE session_id=? ORDER BY idx ASC", (session_id,)
        )

    # -- Automations --------------------------------------------------------

    def create_automation(self, data: dict[str, Any]) -> str:
        """Insert an automation row from a validated dict; return its id."""
        aid = data.get("id") or new_id()
        rc = data.get("run_config")
        self._exec(
            """INSERT INTO automations
               (id, name, task, schedule_type, daily_days, daily_time, interval_unit,
                interval_value, session_mode, persona_id, model_override, enabled,
                next_run_at, run_config, trigger_type, trigger_id, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (aid, data["name"], data["task"], data["schedule_type"],
             json.dumps(data.get("daily_days")) if data.get("daily_days") is not None else None,
             data.get("daily_time"), data.get("interval_unit"), data.get("interval_value"),
             data.get("session_mode", "new"), data.get("persona_id"),
             data.get("model_override"), 1 if data.get("enabled", True) else 0,
             data.get("next_run_at"), json.dumps(rc) if rc is not None else None,
             data.get("trigger_type", "time"), data.get("trigger_id"), _now()),
        )
        return aid

    def update_automation(self, automation_id: str, **fields: Any) -> None:
        """Patch automation fields (serializes ``daily_days``/``run_config`` to JSON)."""
        if "daily_days" in fields and fields["daily_days"] is not None:
            fields["daily_days"] = json.dumps(fields["daily_days"])
        if "run_config" in fields and fields["run_config"] is not None:
            fields["run_config"] = json.dumps(fields["run_config"])
        if "trigger_state" in fields and fields["trigger_state"] is not None:
            fields["trigger_state"] = json.dumps(fields["trigger_state"])
        if "last_trigger_value" in fields and fields["last_trigger_value"] is not None:
            fields["last_trigger_value"] = 1 if fields["last_trigger_value"] else 0
        if "enabled" in fields:
            fields["enabled"] = 1 if fields["enabled"] else 0
        cols = ", ".join(f"{k}=?" for k in fields)
        self._exec(
            f"UPDATE automations SET {cols} WHERE id=?",
            (*fields.values(), automation_id),
        )

    def get_automation(self, automation_id: str) -> dict[str, Any] | None:
        """Return an automation row (with ``daily_days`` decoded)."""
        row = self._query_one("SELECT * FROM automations WHERE id=?", (automation_id,))
        return _decode_automation(row) if row else None

    def list_automations(self) -> list[dict[str, Any]]:
        """List all automations with ``daily_days`` decoded to a list."""
        return [_decode_automation(r) for r in self._query("SELECT * FROM automations")]

    def delete_automation(self, automation_id: str) -> None:
        """Remove an automation by id."""
        self._exec("DELETE FROM automations WHERE id=?", (automation_id,))

    # -- Triggers (standalone heartbeat conditions) -------------------------

    def create_trigger(self, *, name: str, prompt: str | None, code: str,
                        state: dict | None = None) -> str:
        """Insert a standalone trigger definition and return its id."""
        tid = new_id()
        self._exec(
            """INSERT INTO triggers (id, name, prompt, code, state, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?)""",
            (tid, name, prompt, code,
             json.dumps(state) if state is not None else None, _now(), _now()),
        )
        return tid

    def update_trigger(self, trigger_id: str, **fields: Any) -> None:
        """Patch a trigger's fields (serializes ``state``; stamps ``updated_at``)."""
        if "state" in fields and fields["state"] is not None:
            fields["state"] = json.dumps(fields["state"])
        if "last_value" in fields and fields["last_value"] is not None:
            fields["last_value"] = 1 if fields["last_value"] else 0
        fields.setdefault("updated_at", _now())
        cols = ", ".join(f"{k}=?" for k in fields)
        self._exec(f"UPDATE triggers SET {cols} WHERE id=?", (*fields.values(), trigger_id))

    def get_trigger(self, trigger_id: str) -> dict[str, Any] | None:
        """Return a trigger row with ``state`` decoded to a dict."""
        row = self._query_one("SELECT * FROM triggers WHERE id=?", (trigger_id,))
        return _decode_trigger(row) if row else None

    def list_triggers(self) -> list[dict[str, Any]]:
        """List all triggers (newest first) with ``state`` decoded."""
        return [_decode_trigger(r)
                for r in self._query("SELECT * FROM triggers ORDER BY created_at DESC")]

    def delete_trigger(self, trigger_id: str) -> None:
        """Remove a trigger and its run history."""
        self._exec("DELETE FROM triggers WHERE id=?", (trigger_id,))
        self._exec("DELETE FROM trigger_runs WHERE trigger_id=?", (trigger_id,))

    # -- Trigger run history (heartbeat visibility) -------------------------

    def add_trigger_run(
        self, trigger_id: str, *, value: bool, fired: bool,
        error: str | None = None, stdout: str | None = None,
        duration_ms: int | None = None, session_id: str | None = None,
        automation_id: str | None = None,
    ) -> str:
        """Record one trigger evaluation so its result/error stays visible."""
        rid = new_id()
        self._exec(
            """INSERT INTO trigger_runs
               (id, trigger_id, at, value, fired, error, stdout, duration_ms,
                session_id, automation_id)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (rid, trigger_id, _now(), 1 if value else 0, 1 if fired else 0,
             error, stdout, duration_ms, session_id, automation_id),
        )
        return rid

    def list_trigger_runs(self, trigger_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        """Return recent evaluations for a trigger, newest first."""
        return self._query(
            "SELECT * FROM trigger_runs WHERE trigger_id=? ORDER BY at DESC LIMIT ?",
            (trigger_id, limit),
        )

    def prune_trigger_runs(self, trigger_id: str, *, keep: int = 200) -> None:
        """Trim trigger history to the most recent ``keep`` rows (best-effort)."""
        self._exec(
            """DELETE FROM trigger_runs WHERE trigger_id=? AND id NOT IN (
                   SELECT id FROM trigger_runs WHERE trigger_id=?
                   ORDER BY at DESC LIMIT ?
               )""",
            (trigger_id, trigger_id, keep),
        )


    # -- Personas -----------------------------------------------------------

    def ensure_default_persona(self) -> dict[str, Any]:
        """Return the default persona, creating a neutral one if none exists (FR-071)."""
        row = self._query_one("SELECT * FROM personas WHERE is_default=1 LIMIT 1")
        if row:
            return row
        pid = new_id()
        instructions = (
            "You are a helpful, careful local assistant running on the user's machine. "
            "You can use the provided tools and skills. Respect the filesystem consent "
            "boundary: you may freely use the workspace folder and must request access "
            "to anything else. Never attempt to read secret values."
        )
        self._exec(
            """INSERT INTO personas (id, name, instructions, is_default, created_at, updated_at)
               VALUES (?,?,?,1,?,?)""",
            (pid, "Default", instructions, _now(), _now()),
        )
        return self._query_one("SELECT * FROM personas WHERE id=?", (pid,)) or {}

    def list_personas(self) -> list[dict[str, Any]]:
        """List all personas."""
        return self._query("SELECT * FROM personas ORDER BY is_default DESC, name ASC")

    def get_persona(self, persona_id: str) -> dict[str, Any] | None:
        """Return a persona row by id."""
        return self._query_one("SELECT * FROM personas WHERE id=?", (persona_id,))

    def create_persona(self, name: str, instructions: str) -> str:
        """Create a non-default persona and return its id."""
        pid = new_id()
        self._exec(
            """INSERT INTO personas (id, name, instructions, is_default, created_at, updated_at)
               VALUES (?,?,?,0,?,?)""",
            (pid, name, instructions, _now(), _now()),
        )
        return pid

    def update_persona(self, persona_id: str, **fields: Any) -> None:
        """Patch a persona's name/instructions; refreshes ``updated_at``."""
        fields["updated_at"] = _now()
        cols = ", ".join(f"{k}=?" for k in fields)
        self._exec(
            f"UPDATE personas SET {cols} WHERE id=?", (*fields.values(), persona_id)
        )

    def delete_persona(self, persona_id: str) -> bool:
        """Delete a persona unless it is the default (FR-075). Returns success."""
        row = self.get_persona(persona_id)
        if not row or row["is_default"]:
            return False
        self._exec("DELETE FROM personas WHERE id=?", (persona_id,))
        return True

    # -- Capabilities -------------------------------------------------------

    def upsert_capability(self, data: dict[str, Any]) -> str:
        """Insert or update a capability row keyed by (kind, name)."""
        existing = self._query_one(
            "SELECT id FROM capabilities WHERE kind=? AND name=?",
            (data["kind"], data["name"]),
        )
        cid = existing["id"] if existing else (data.get("id") or new_id())
        secret_refs = json.dumps(data.get("secret_refs", []))
        has_meta = "metadata" in data
        meta_json = json.dumps(data.get("metadata")) if data.get("metadata") is not None else None
        if existing:
            if has_meta:
                self._exec(
                    """UPDATE capabilities SET source_path=?, description=?, status=?,
                       secret_refs=?, metadata=? WHERE id=?""",
                    (data.get("source_path"), data.get("description"),
                     data.get("status", "valid"), secret_refs, meta_json, cid),
                )
            else:
                self._exec(
                    """UPDATE capabilities SET source_path=?, description=?, status=?,
                       secret_refs=? WHERE id=?""",
                    (data.get("source_path"), data.get("description"),
                     data.get("status", "valid"), secret_refs, cid),
                )
        else:
            self._exec(
                """INSERT INTO capabilities
                   (id, kind, name, source_path, description, status, enabled,
                    trust_confirmed, secret_refs, metadata, added_by, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (cid, data["kind"], data["name"], data.get("source_path"),
                 data.get("description"), data.get("status", "valid"),
                 1 if data.get("enabled") else 0,
                 1 if data.get("trust_confirmed") else 0,
                 secret_refs, meta_json, data.get("added_by", "user"), _now()),
            )
        return cid

    def list_capabilities(self, kind: str | None = None) -> list[dict[str, Any]]:
        """List capabilities, optionally filtered by kind; decodes ``secret_refs``."""
        if kind:
            rows = self._query("SELECT * FROM capabilities WHERE kind=?", (kind,))
        else:
            rows = self._query("SELECT * FROM capabilities")
        for r in rows:
            r["secret_refs"] = json.loads(r["secret_refs"]) if r.get("secret_refs") else []
            r["metadata"] = json.loads(r["metadata"]) if r.get("metadata") else None
        return rows

    def get_capability(self, cap_id: str) -> dict[str, Any] | None:
        """Return a capability row by id with ``secret_refs`` decoded."""
        r = self._query_one("SELECT * FROM capabilities WHERE id=?", (cap_id,))
        if r:
            r["secret_refs"] = json.loads(r["secret_refs"]) if r.get("secret_refs") else []
        return r

    def get_capability_by_kind_name(self, kind: str, name: str) -> dict[str, Any] | None:
        """Return a capability row by (kind, name), or None."""
        return self._query_one(
            "SELECT * FROM capabilities WHERE kind=? AND name=?", (kind, name)
        )

    def delete_capability(self, cap_id: str) -> None:
        """Remove a capability row by id (best-effort)."""
        self._exec("DELETE FROM capabilities WHERE id=?", (cap_id,))

    def update_capability(self, cap_id: str, **fields: Any) -> None:
        """Patch a capability's enabled/trust/status fields."""
        for bool_field in ("enabled", "trust_confirmed"):
            if bool_field in fields:
                fields[bool_field] = 1 if fields[bool_field] else 0
        cols = ", ".join(f"{k}=?" for k in fields)
        self._exec(
            f"UPDATE capabilities SET {cols} WHERE id=?", (*fields.values(), cap_id)
        )

    # -- Grants -------------------------------------------------------------

    def add_grant(
        self, *, path: str, scope: str, access: str, session_id: str | None = None
    ) -> str:
        """Persist a folder grant (session or permanent) and return its id."""
        gid = new_id()
        self._exec(
            """INSERT INTO grants (id, path, scope, session_id, access, active, granted_at)
               VALUES (?,?,?,?,?,1,?)""",
            (gid, path, scope, session_id, access, _now()),
        )
        return gid

    def active_grants(self, session_id: str | None = None) -> list[dict[str, Any]]:
        """Return active grants: all permanent plus this session's session-scoped."""
        return self._query(
            """SELECT * FROM grants WHERE active=1 AND
               (scope='permanent' OR (scope='session' AND session_id=?))""",
            (session_id,),
        )

    def revoke_grant(self, grant_id: str) -> None:
        """Mark a grant inactive so subsequent checks deny it (FR-023)."""
        self._exec("UPDATE grants SET active=0 WHERE id=?", (grant_id,))

    def clear_session_grants(self, session_id: str) -> None:
        """Deactivate all session-scoped grants when a session ends (FR-022)."""
        self._exec(
            "UPDATE grants SET active=0 WHERE scope='session' AND session_id=?",
            (session_id,),
        )

    # -- Notifications ------------------------------------------------------

    def add_notification(
        self,
        *,
        type: str,
        message: str,
        related_session_id: str | None = None,
        related_automation_id: str | None = None,
    ) -> str:
        """Record a notification (messages must never contain secrets, FR-026)."""
        nid = new_id()
        self._exec(
            """INSERT INTO notifications
               (id, type, message, related_session_id, related_automation_id, created_at)
               VALUES (?,?,?,?,?,?)""",
            (nid, type, message, related_session_id, related_automation_id, _now()),
        )
        return nid

    def list_notifications(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return the most recent notifications."""
        return self._query(
            "SELECT * FROM notifications ORDER BY created_at DESC LIMIT ?", (limit,)
        )

    # -- Compression events -------------------------------------------------

    def add_compression_event(
        self, session_id: str, tokens_before: int, tokens_after: int, summary_turn_id: str | None
    ) -> str:
        """Record an automatic context compaction within a session."""
        eid = new_id()
        self._exec(
            """INSERT INTO compression_events
               (id, session_id, at, tokens_before, tokens_after, summary_turn_id)
               VALUES (?,?,?,?,?,?)""",
            (eid, session_id, _now(), tokens_before, tokens_after, summary_turn_id),
        )
        return eid

    def list_compression_events(self, session_id: str) -> list[dict[str, Any]]:
        """Return compression events recorded for a session."""
        return self._query(
            "SELECT * FROM compression_events WHERE session_id=? ORDER BY at ASC",
            (session_id,),
        )

    # -- Retention ----------------------------------------------------------

    def prune(self, retention_days: int) -> None:
        """Delete sessions/turns/events/notifications older than the window (FR-038).

        Automations, personas, capabilities, and permanent grants are *not* pruned.
        """
        if retention_days <= 0:
            return
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
        old = self._query(
            "SELECT id FROM sessions WHERE ended_at IS NOT NULL AND ended_at < ?",
            (cutoff,),
        )
        for row in old:
            sid = row["id"]
            self._exec("DELETE FROM turns WHERE session_id=?", (sid,))
            self._exec("DELETE FROM compression_events WHERE session_id=?", (sid,))
            self._exec("UPDATE grants SET active=0 WHERE scope='session' AND session_id=?", (sid,))
            self._exec("DELETE FROM sessions WHERE id=?", (sid,))
        self._exec("DELETE FROM notifications WHERE created_at < ?", (cutoff,))


def _decode_automation(row: dict[str, Any]) -> dict[str, Any]:
    """Decode an automation row's JSON ``daily_days`` and ``run_config`` fields."""
    if row.get("daily_days"):
        try:
            row["daily_days"] = json.loads(row["daily_days"])
        except (json.JSONDecodeError, TypeError):
            row["daily_days"] = None
    if row.get("run_config"):
        try:
            row["run_config"] = json.loads(row["run_config"])
        except (json.JSONDecodeError, TypeError):
            row["run_config"] = None
    if row.get("trigger_state"):
        try:
            row["trigger_state"] = json.loads(row["trigger_state"])
        except (json.JSONDecodeError, TypeError):
            row["trigger_state"] = None
    return row


def _decode_trigger(row: dict[str, Any]) -> dict[str, Any]:
    """Decode a trigger row's JSON ``state`` field to a dict."""
    if row.get("state"):
        try:
            row["state"] = json.loads(row["state"])
        except (json.JSONDecodeError, TypeError):
            row["state"] = None
    return row
