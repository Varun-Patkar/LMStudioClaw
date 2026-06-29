"""Agent tools for the graph "brain" memory.

Registers first-party tools that let the agent build and recall a long-term graph
memory (see :mod:`lmstudioclaw.orchestrator.brain`). Like the ``remember``/``recall``
learning tools these operate only on the agent's own home area (the graph DB + the
``memory/brain`` Markdown folder), so they never need a consent prompt.

Token-frugality is the point: the agent stores a short summary + relationships in the
graph and the long details in Markdown, then pulls only what it needs back via
``brain_search``/``brain_get`` instead of holding everything in context.
"""

from __future__ import annotations

from .registry import ToolResult, ToolSpec


def _fmt_node(n: dict) -> str:
    """One-line rendering of a node for tool output (id · type · label — summary)."""
    summary = (n.get("summary") or "").strip()
    tail = f" — {summary}" if summary else ""
    return f"[{n['id']}] ({n.get('type', 'note')}) {n.get('label', '')}{tail}"


def register_brain_tools(registry, brain) -> None:
    """Register brain_add_node / brain_link / brain_get / brain_search / brain_update."""

    async def _add_node(*, label: str, summary: str = "", type: str = "note",
                        details: str | None = None, consent=None) -> ToolResult:
        """Create a memory node (summary in the graph, details in Markdown)."""
        try:
            nid = brain.add_node(label, summary, type, details)
            return ToolResult(
                True, f"Saved node {nid}.",
                meta={"action": "brain_add", "id": nid, "label": label, "type": type},
            )
        except Exception as exc:
            return ToolResult(False, "", error=f"Could not add node: {exc}")

    async def _link(*, source: str, target: str, type: str = "related",
                   weight: float = 1.0, consent=None) -> ToolResult:
        """Create a typed relationship (edge) between two existing nodes."""
        try:
            eid = brain.add_edge(source, target, type, weight)
            # Resolve labels for a readable UI card (ids alone are opaque).
            src = brain.get_node(source) or {}
            tgt = brain.get_node(target) or {}
            return ToolResult(
                True, f"Linked {source} -[{type}]-> {target}.",
                meta={"action": "brain_link", "source": source, "target": target,
                      "type": type, "source_label": src.get("label", source),
                      "target_label": tgt.get("label", target)},
            )
        except Exception as exc:
            return ToolResult(False, "", error=f"Could not link nodes: {exc}")

    async def _get(*, id: str, depth: int = 1, consent=None) -> ToolResult:
        """Recall a node: its details (Markdown) plus connected nodes up to ``depth``."""
        node = brain.get_node(id)
        if node is None:
            return ToolResult(False, "", error=f"No node with id '{id}'.")
        details = brain.read_details(id)
        sub = brain.traverse(id, depth)
        lines = [_fmt_node(node), ""]
        if details.strip():
            lines += ["Details:", details.strip(), ""]
        edges = sub.get("edges", [])
        if edges:
            by_id = {n["id"]: n for n in sub.get("nodes", [])}
            lines.append("Connections:")
            for e in edges:
                other = e["target"] if e["source"] == id else e["source"]
                arrow = "->" if e["source"] == id else "<-"
                label = by_id.get(other, {}).get("label", other)
                lines.append(f"  {arrow} [{e['type']}] {label} ({other})")
        return ToolResult(True, "\n".join(lines).strip(),
                          meta={"action": "brain_get", "id": id,
                                "label": node.get("label", id),
                                "type": node.get("type", "note"),
                                "connections": len(edges)})

    async def _search(*, query: str, consent=None) -> ToolResult:
        """Find memory nodes whose label or summary matches ``query``."""
        rows = brain.search(query)
        if not rows:
            return ToolResult(True, "(no matching memory nodes)",
                              meta={"action": "brain_search", "query": query, "count": 0})
        return ToolResult(True, "\n".join(_fmt_node(n) for n in rows),
                          meta={"action": "brain_search", "query": query, "count": len(rows)})

    async def _update(*, id: str, label: str | None = None, summary: str | None = None,
                     type: str | None = None, details: str | None = None,
                     consent=None) -> ToolResult:
        """Update a node's summary/label/type and/or replace its detail Markdown."""
        try:
            ok = brain.update_node(id, label=label, summary=summary,
                                   node_type=type, details=details)
        except Exception as exc:
            return ToolResult(False, "", error=f"Could not update node: {exc}")
        if not ok:
            return ToolResult(False, "", error=f"No node with id '{id}'.")
        node = brain.get_node(id) or {}
        return ToolResult(True, f"Updated node {id}.",
                          meta={"action": "brain_update", "id": id,
                                "label": node.get("label", id),
                                "type": node.get("type", "note")})

    async def _delete(*, id: str, consent=None) -> ToolResult:
        """Delete a single memory node (and its relationships) by id."""
        node = brain.get_node(id)
        label = (node or {}).get("label", id)
        try:
            removed = brain.delete_node(id)
        except Exception as exc:
            return ToolResult(False, "", error=f"Could not delete node: {exc}")
        if not removed:
            return ToolResult(False, "", error=f"No node with id '{id}'.")
        return ToolResult(True, f"Deleted node {id} ({label}).",
                          meta={"action": "brain_delete", "id": id, "label": label})

    async def _clear(*, consent=None) -> ToolResult:
        """Erase the ENTIRE graph memory — every node, edge, and detail file."""
        try:
            count = brain.clear()
        except Exception as exc:
            return ToolResult(False, "", error=f"Could not clear memory: {exc}")
        return ToolResult(True, f"Cleared graph memory: removed {count} node(s).",
                          meta={"action": "brain_clear", "count": count})

    node_props = {
        "label": {"type": "string", "description": "Short name/title of the node"},
        "summary": {"type": "string", "description": "Brief summary kept in the graph (token-cheap)"},
        "type": {"type": "string", "description": "Node category, e.g. person, project, fact, concept"},
        "details": {"type": "string", "description": "Full details, stored as Markdown (optional)"},
    }

    registry.register_tool(ToolSpec(
        "brain_add_node",
        "Add a node to your long-term graph memory. Keep `summary` short; put the long "
        "content in `details` (saved as Markdown). Returns the node id to link later.",
        {"type": "object", "properties": node_props, "required": ["label"]},
        _add_node,
    ))
    registry.register_tool(ToolSpec(
        "brain_link",
        "Create a relationship between two memory nodes by their ids (e.g. "
        "type='works_on', 'depends_on', 'related').",
        {"type": "object", "properties": {
            "source": {"type": "string", "description": "Source node id"},
            "target": {"type": "string", "description": "Target node id"},
            "type": {"type": "string", "description": "Relationship type"},
            "weight": {"type": "number", "description": "Optional strength (default 1.0)"},
        }, "required": ["source", "target"]},
        _link,
    ))
    registry.register_tool(ToolSpec(
        "brain_get",
        "Recall a memory node by id: its full Markdown details plus the nodes it "
        "connects to (up to `depth` hops, 1–4).",
        {"type": "object", "properties": {
            "id": {"type": "string", "description": "Node id"},
            "depth": {"type": "integer", "description": "Traversal depth (1–4, default 1)"},
        }, "required": ["id"]},
        _get,
    ))
    registry.register_tool(ToolSpec(
        "brain_search",
        "Search your graph memory for nodes whose label or summary matches a query. "
        "Start here to find relevant node ids before reading them with brain_get.",
        {"type": "object", "properties": {
            "query": {"type": "string", "description": "Text to match against label/summary"},
        }, "required": ["query"]},
        _search,
    ))
    registry.register_tool(ToolSpec(
        "brain_update",
        "Update an existing memory node's summary/label/type, and/or replace its "
        "Markdown details.",
        {"type": "object", "properties": {"id": {"type": "string", "description": "Node id"},
                                          **node_props}, "required": ["id"]},
        _update,
    ))
    registry.register_tool(ToolSpec(
        "brain_delete",
        "Permanently delete one memory node (and all its relationships) by id. Use "
        "this to forget a specific thing.",
        {"type": "object", "properties": {
            "id": {"type": "string", "description": "Node id to delete"},
        }, "required": ["id"]},
        _delete,
    ))
    registry.register_tool(ToolSpec(
        "brain_clear",
        "ERASE the entire graph memory — every node, relationship, and detail file. "
        "Use only when the user asks to clear/wipe/forget everything. Irreversible.",
        {"type": "object", "properties": {}},
        _clear,
    ))
