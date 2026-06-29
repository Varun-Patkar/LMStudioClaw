"""Graph-based agent memory — the agent's "brain".

A small SQLite graph (``Documents/LMStudioClaw/graph.db``) of **nodes** and **edges**
that the agent reads, writes, and traverses through tools. It is a token-frugal,
long-term memory: the database holds only a short *summary* per node plus the
relationships between nodes, while each node's full details live in a Markdown file
(``memory/brain/<id>.md``) referenced by the node id. The agent recalls just the
summaries/relationships it needs and only opens a node's Markdown when it wants the
detail — so the brain can grow large without ever flooding the context window.

Design notes:

* The DB owns structure (nodes, edges, summaries); Markdown owns content (details).
* A node's id doubles as its Markdown filename stem, so the file is found without
  storing a path (the brain dir is always-allowed agent home, so no consent gate).
* One connection guarded by a re-entrant lock (SQLite is not thread-safe to share),
  mirroring :class:`sessions.store.Store`. ``check_same_thread`` is disabled because
  tool calls run across asyncio executors.
* Writes raise on real errors so tool handlers can report them; the read helpers the
  viewer uses are defensive.
"""

from __future__ import annotations

import re
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL DEFAULT 'note',
    label TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS edges (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    target TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'related',
    weight REAL NOT NULL DEFAULT 1.0,
    created_at TEXT NOT NULL,
    FOREIGN KEY(source) REFERENCES nodes(id) ON DELETE CASCADE,
    FOREIGN KEY(target) REFERENCES nodes(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS edges_source ON edges(source);
CREATE INDEX IF NOT EXISTS edges_target ON edges(target);
"""


def _now() -> str:
    """Return the current UTC timestamp as ISO-8601."""
    return datetime.now(timezone.utc).isoformat()


def _slug(text: str) -> str:
    """Make a short, filename-safe slug from a label (for a readable node id)."""
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (text or "").strip().lower()).strip("-")
    return s[:40] or "node"


class BrainStore:
    """SQLite graph memory with per-node Markdown details."""

    def __init__(self, db_path: Path, brain_dir: Path) -> None:
        """Open (creating if absent) the graph DB and ensure the detail dir exists.

        On first run this initializes an **empty** graph (the schema is applied but no
        nodes/edges are inserted).
        """
        self._lock = threading.RLock()
        self._dir = brain_dir
        try:
            db_path.parent.mkdir(parents=True, exist_ok=True)
            brain_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # -- detail markdown ----------------------------------------------------

    def _md_path(self, node_id: str) -> Path:
        """Resolve the Markdown detail file for a node id."""
        return self._dir / f"{node_id}.md"

    def _write_details(self, node_id: str, details: str) -> None:
        """Write a node's full details to its Markdown file (best-effort)."""
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            self._md_path(node_id).write_text((details or "").strip() + "\n", encoding="utf-8")
        except OSError:
            pass

    def read_details(self, node_id: str) -> str:
        """Return a node's Markdown details, or empty string if none saved."""
        path = self._md_path(node_id)
        if not path.exists():
            return ""
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    # -- mutations (raise on error so tools can report) --------------------

    def add_node(self, label: str, summary: str = "", node_type: str = "note",
                 details: str | None = None, node_id: str | None = None) -> str:
        """Insert a node and return its id; write detail Markdown when provided."""
        if not (label or "").strip():
            raise ValueError("A node needs a non-empty label.")
        nid = node_id or f"{_slug(label)}-{uuid.uuid4().hex[:6]}"
        now = _now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO nodes (id, type, label, summary, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (nid, (node_type or "note").strip(), label.strip(),
                 (summary or "").strip(), now, now),
            )
            self._conn.commit()
        if details is not None:
            self._write_details(nid, details)
        return nid

    def update_node(self, node_id: str, *, label: str | None = None,
                    summary: str | None = None, node_type: str | None = None,
                    details: str | None = None) -> bool:
        """Update a node's fields and/or its detail Markdown. False if it's unknown."""
        sets, vals = [], []
        if label is not None:
            sets.append("label = ?"); vals.append(label.strip())
        if summary is not None:
            sets.append("summary = ?"); vals.append(summary.strip())
        if node_type is not None:
            sets.append("type = ?"); vals.append(node_type.strip())
        with self._lock:
            if self._conn.execute("SELECT 1 FROM nodes WHERE id = ?", (node_id,)).fetchone() is None:
                return False
            if sets:
                sets.append("updated_at = ?"); vals.append(_now()); vals.append(node_id)
                self._conn.execute(f"UPDATE nodes SET {', '.join(sets)} WHERE id = ?", vals)
                self._conn.commit()
        if details is not None:
            self._write_details(node_id, details)
        return True

    def add_edge(self, source: str, target: str, edge_type: str = "related",
                 weight: float = 1.0) -> str:
        """Connect two existing nodes with a typed, weighted edge; return its id."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id FROM nodes WHERE id IN (?, ?)", (source, target)
            ).fetchall()
            found = {r["id"] for r in rows}
            missing = [n for n in (source, target) if n not in found]
            if missing:
                raise ValueError(f"Unknown node id(s): {', '.join(missing)}")
            eid = uuid.uuid4().hex
            self._conn.execute(
                "INSERT INTO edges (id, source, target, type, weight, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (eid, source, target, (edge_type or "related").strip(), float(weight), _now()),
            )
            self._conn.commit()
        return eid

    def delete_node(self, node_id: str) -> bool:
        """Delete a node (and its edges via cascade) and its detail Markdown."""
        with self._lock:
            cur = self._conn.execute("DELETE FROM nodes WHERE id = ?", (node_id,))
            self._conn.commit()
            removed = cur.rowcount > 0
        if removed:
            try:
                self._md_path(node_id).unlink(missing_ok=True)
            except OSError:
                pass
        return removed

    def delete_edge(self, edge_id: str) -> bool:
        """Delete a single edge by id."""
        with self._lock:
            cur = self._conn.execute("DELETE FROM edges WHERE id = ?", (edge_id,))
            self._conn.commit()
            return cur.rowcount > 0

    def clear(self) -> int:
        """Wipe the entire graph: delete all nodes, edges, and detail Markdown.

        Returns the number of nodes removed. Used by the ``brain_clear`` tool so the
        agent can honour a "forget everything" request (there is no other way to
        remove memory in bulk).
        """
        with self._lock:
            ids = [r["id"] for r in self._conn.execute("SELECT id FROM nodes").fetchall()]
            self._conn.execute("DELETE FROM edges")
            self._conn.execute("DELETE FROM nodes")
            self._conn.commit()
        # Remove each node's detail Markdown (best-effort; DB is the source of truth).
        for nid in ids:
            try:
                self._md_path(nid).unlink(missing_ok=True)
            except OSError:
                pass
        return len(ids)

    # -- reads --------------------------------------------------------------

    def get_node(self, node_id: str) -> dict | None:
        """Return a node row as a dict (without details), or None if unknown."""
        with self._lock:
            row = self._conn.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
        return dict(row) if row else None

    def neighbors(self, node_id: str) -> dict:
        """Return a node plus its directly-connected nodes and the connecting edges.

        Shape: ``{"node": {..}, "edges": [..], "neighbors": [{..}, ..]}``. Used by the
        viewer's click-to-focus and by the ``brain_get`` tool at depth 1.
        """
        node = self.get_node(node_id)
        if node is None:
            return {"node": None, "edges": [], "neighbors": []}
        with self._lock:
            edges = [dict(r) for r in self._conn.execute(
                "SELECT * FROM edges WHERE source = ? OR target = ?", (node_id, node_id)
            ).fetchall()]
            ids = {e["source"] for e in edges} | {e["target"] for e in edges}
            ids.discard(node_id)
            others = []
            if ids:
                qs = ",".join("?" * len(ids))
                others = [dict(r) for r in self._conn.execute(
                    f"SELECT * FROM nodes WHERE id IN ({qs})", tuple(ids)
                ).fetchall()]
        return {"node": node, "edges": edges, "neighbors": others}

    def traverse(self, node_id: str, depth: int = 1) -> dict:
        """Breadth-first subgraph around ``node_id`` up to ``depth`` hops.

        Returns ``{"nodes": [..], "edges": [..]}``. Depth is clamped to a small range
        so a single call can never pull the whole graph into context.
        """
        depth = max(1, min(4, int(depth or 1)))
        if self.get_node(node_id) is None:
            return {"nodes": [], "edges": []}
        seen: set[str] = {node_id}
        frontier = {node_id}
        edge_ids: dict[str, dict] = {}
        for _ in range(depth):
            if not frontier:
                break
            qs = ",".join("?" * len(frontier))
            with self._lock:
                rows = self._conn.execute(
                    f"SELECT * FROM edges WHERE source IN ({qs}) OR target IN ({qs})",
                    tuple(frontier) * 2,
                ).fetchall()
            nxt: set[str] = set()
            for r in rows:
                edge_ids[r["id"]] = dict(r)
                for end in (r["source"], r["target"]):
                    if end not in seen:
                        seen.add(end); nxt.add(end)
            frontier = nxt
        with self._lock:
            qs = ",".join("?" * len(seen))
            nodes = [dict(r) for r in self._conn.execute(
                f"SELECT * FROM nodes WHERE id IN ({qs})", tuple(seen)
            ).fetchall()]
        return {"nodes": nodes, "edges": list(edge_ids.values())}

    def search(self, query: str, limit: int = 25) -> list[dict]:
        """Find nodes whose label or summary contains ``query`` (case-insensitive)."""
        like = f"%{(query or '').strip()}%"
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM nodes WHERE label LIKE ? OR summary LIKE ? "
                "ORDER BY updated_at DESC LIMIT ?",
                (like, like, int(limit)),
            ).fetchall()
        return [dict(r) for r in rows]

    def graph(self, node_types: list[str] | None = None,
              edge_types: list[str] | None = None) -> dict:
        """Return the whole graph (optionally filtered) for the viewer.

        Filtering by node type keeps only matching nodes *and* the edges between them;
        filtering by edge type keeps only matching edges.
        """
        with self._lock:
            nrows = [dict(r) for r in self._conn.execute("SELECT * FROM nodes").fetchall()]
            erows = [dict(r) for r in self._conn.execute("SELECT * FROM edges").fetchall()]
        if node_types:
            wanted = set(node_types)
            nrows = [n for n in nrows if n["type"] in wanted]
        keep_ids = {n["id"] for n in nrows}
        erows = [e for e in erows if e["source"] in keep_ids and e["target"] in keep_ids]
        if edge_types:
            wanted_e = set(edge_types)
            erows = [e for e in erows if e["type"] in wanted_e]
        return {"nodes": nrows, "edges": erows}

    def meta(self) -> dict:
        """Return distinct node/edge types + counts for the viewer's filter controls."""
        with self._lock:
            ntypes = [r["type"] for r in self._conn.execute(
                "SELECT DISTINCT type FROM nodes ORDER BY type").fetchall()]
            etypes = [r["type"] for r in self._conn.execute(
                "SELECT DISTINCT type FROM edges ORDER BY type").fetchall()]
            ncount = self._conn.execute("SELECT COUNT(*) AS c FROM nodes").fetchone()["c"]
            ecount = self._conn.execute("SELECT COUNT(*) AS c FROM edges").fetchone()["c"]
        return {"node_types": ntypes, "edge_types": etypes,
                "node_count": ncount, "edge_count": ecount}

    def close(self) -> None:
        """Close the underlying connection (best-effort)."""
        try:
            self._conn.close()
        except Exception:
            pass
