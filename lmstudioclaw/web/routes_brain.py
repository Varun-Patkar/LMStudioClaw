"""Read-only REST routes for the Brain viewer.

Exposes the agent's graph memory to the control-panel Brain page: the full (optionally
filtered) graph, per-node details with direct neighbors, and the distinct node/edge
types used to build the filter controls. These endpoints are read-only — the agent
owns writes through its tools; the viewer only inspects.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(tags=["brain"])


def _brain(request: Request):
    """Return the controller's BrainStore."""
    return request.app.state.controller.brain


def _csv(value: str | None) -> list[str] | None:
    """Parse a comma-separated query param into a list (or None when absent/empty)."""
    if not value:
        return None
    items = [v.strip() for v in value.split(",") if v.strip()]
    return items or None


@router.get("/api/brain/meta")
async def brain_meta(request: Request) -> dict:
    """Return node/edge type vocabularies + counts for the viewer's filters."""
    return _brain(request).meta()


@router.get("/api/brain/graph")
async def brain_graph(request: Request, node_types: str | None = None,
                      edge_types: str | None = None) -> dict:
    """Return the graph, optionally filtered by node and/or edge type."""
    return _brain(request).graph(_csv(node_types), _csv(edge_types))


@router.get("/api/brain/node/{node_id}")
async def brain_node(node_id: str, request: Request) -> dict:
    """Return a node, its Markdown details, and its directly-connected neighbors."""
    brain = _brain(request)
    data = brain.neighbors(node_id)
    if data["node"] is None:
        raise HTTPException(404, "Node not found")
    data["details"] = brain.read_details(node_id)
    return data
