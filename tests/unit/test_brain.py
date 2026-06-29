"""Unit tests for the graph brain memory (nodes, edges, traversal, details, filters)."""

from __future__ import annotations

from lmstudioclaw.orchestrator.brain import BrainStore


def _store(temp_app_paths) -> BrainStore:
    return BrainStore(temp_app_paths.graph_db, temp_app_paths.brain_dir)


def test_empty_on_init(temp_app_paths):
    brain = _store(temp_app_paths)
    meta = brain.meta()
    assert meta["node_count"] == 0 and meta["edge_count"] == 0
    assert brain.graph() == {"nodes": [], "edges": []}


def test_add_node_with_details_writes_markdown(temp_app_paths):
    brain = _store(temp_app_paths)
    nid = brain.add_node("Project X", "a project", "project", details="# Project X\n\nbig plan")
    node = brain.get_node(nid)
    assert node and node["label"] == "Project X" and node["type"] == "project"
    assert "big plan" in brain.read_details(nid)
    assert (temp_app_paths.brain_dir / f"{nid}.md").exists()


def test_link_requires_existing_nodes(temp_app_paths):
    brain = _store(temp_app_paths)
    a = brain.add_node("A")
    try:
        brain.add_edge(a, "missing-id")
    except ValueError as exc:
        assert "Unknown node" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError for missing target")


def test_neighbors_and_traverse(temp_app_paths):
    brain = _store(temp_app_paths)
    a = brain.add_node("A")
    b = brain.add_node("B")
    c = brain.add_node("C")
    brain.add_edge(a, b, "related")
    brain.add_edge(b, c, "related")

    near = brain.neighbors(a)
    assert near["node"]["id"] == a
    assert {n["id"] for n in near["neighbors"]} == {b}

    deep = brain.traverse(a, depth=2)
    assert {n["id"] for n in deep["nodes"]} == {a, b, c}
    assert len(deep["edges"]) == 2


def test_search_and_filters(temp_app_paths):
    brain = _store(temp_app_paths)
    p = brain.add_node("Ada Lovelace", "mathematician", "person")
    brain.add_node("Pizza", "food", "food")
    hits = brain.search("ada")
    assert [h["id"] for h in hits] == [p]

    only_people = brain.graph(node_types=["person"])
    assert {n["id"] for n in only_people["nodes"]} == {p}


def test_delete_node_cascades_edges(temp_app_paths):
    brain = _store(temp_app_paths)
    a = brain.add_node("A")
    b = brain.add_node("B")
    brain.add_edge(a, b)
    assert brain.delete_node(a) is True
    assert brain.meta()["edge_count"] == 0
