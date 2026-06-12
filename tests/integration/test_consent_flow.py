"""Integration test for the consent lifecycle (SC-004, SC-005).

Exercises the path gate + grant store together: prompt for uncovered paths,
session-scope expiry on session end, permanent persistence across a restart,
hierarchical subfolder coverage, and revocation.
"""

from __future__ import annotations

from lmstudioclaw.consent.path_gate import Access, DecisionKind, PathGate
from lmstudioclaw.sessions.store import Store


def _gate(paths) -> tuple[PathGate, Store]:
    store = Store(paths.db_path)
    return PathGate(paths, store), store


def test_uncovered_path_prompts(temp_app_paths, tmp_path):
    gate, _store = _gate(temp_app_paths)
    target = tmp_path / "outside" / "file.txt"
    assert gate.authorize(target, Access.READ).kind == DecisionKind.NEEDS_CONSENT


def test_session_grant_expires_on_end(temp_app_paths, tmp_path):
    gate, store = _gate(temp_app_paths)
    folder = tmp_path / "proj"
    folder.mkdir()
    sid = store.create_session(trigger_type="manual")
    store.add_grant(path=str(folder), scope="session", access="read", session_id=sid)

    target = folder / "a.txt"
    assert gate.authorize(target, Access.READ, session_id=sid).kind == DecisionKind.ALLOW

    # Session ends -> session-scoped grants cleared.
    store.clear_session_grants(sid)
    assert gate.authorize(target, Access.READ, session_id=sid).kind == DecisionKind.NEEDS_CONSENT


def test_permanent_grant_persists_across_restart(temp_app_paths, tmp_path):
    folder = tmp_path / "permanent"
    folder.mkdir()
    gate, store = _gate(temp_app_paths)
    store.add_grant(path=str(folder), scope="permanent", access="read_write")
    store.close()

    # "Restart": new store/gate over the same database file.
    gate2, store2 = _gate(temp_app_paths)
    target = folder / "deep" / "b.txt"  # hierarchical subfolder coverage
    assert gate2.authorize(target, Access.READ_WRITE).kind == DecisionKind.ALLOW


def test_revoke_blocks_subsequent_access(temp_app_paths, tmp_path):
    folder = tmp_path / "revokable"
    folder.mkdir()
    gate, store = _gate(temp_app_paths)
    gid = store.add_grant(path=str(folder), scope="permanent", access="read")
    target = folder / "c.txt"
    assert gate.authorize(target, Access.READ).kind == DecisionKind.ALLOW

    store.revoke_grant(gid)
    assert gate.authorize(target, Access.READ).kind == DecisionKind.NEEDS_CONSENT
