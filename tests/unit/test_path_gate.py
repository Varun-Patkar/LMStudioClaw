"""Unit tests for the consent path gate (SC-004).

Covers: workspace always-allow, hierarchical subfolder grants, traversal/symlink
escape rejection, secrets deny-list, least-privilege read-vs-write, and fail-fast
for unattended automations.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lmstudioclaw.consent.path_gate import Access, DecisionKind, PathGate


class _FakeStore:
    """Minimal grant store stub returning a fixed list of active grants."""

    def __init__(self, grants: list[dict]) -> None:
        self._grants = grants

    def active_grants(self, session_id=None):  # noqa: D401, ANN001
        """Return the configured grants regardless of session."""
        return self._grants


def _gate(paths, grants=None) -> PathGate:
    return PathGate(paths, _FakeStore(grants or []))


def test_workspace_always_allowed(temp_app_paths):
    gate = _gate(temp_app_paths)
    target = temp_app_paths.workspace / "notes.txt"
    decision = gate.authorize(target, Access.READ_WRITE)
    assert decision.kind == DecisionKind.ALLOW


def test_workspace_subfolder_allowed(temp_app_paths):
    gate = _gate(temp_app_paths)
    target = temp_app_paths.workspace / "deep" / "nested" / "file.md"
    assert gate.authorize(target, Access.READ).kind == DecisionKind.ALLOW


def test_secrets_area_denied_even_with_grant(temp_app_paths):
    # A grant on the secrets dir must still be hard-denied (FR-077).
    grants = [{"id": "g1", "path": str(temp_app_paths.secrets_dir), "access": "read_write"}]
    gate = _gate(temp_app_paths, grants)
    target = temp_app_paths.secrets_dir / "secrets.json"
    assert gate.authorize(target, Access.READ).kind == DecisionKind.DENY


def test_hierarchical_grant_allows_subfolder(temp_app_paths, tmp_path):
    parent = tmp_path / "projects"
    parent.mkdir()
    grants = [{"id": "g1", "path": str(parent), "access": "read_write"}]
    gate = _gate(temp_app_paths, grants)
    target = parent / "sub" / "a.txt"
    assert gate.authorize(target, Access.READ_WRITE).kind == DecisionKind.ALLOW


def test_least_privilege_read_grant_blocks_write(temp_app_paths, tmp_path):
    parent = tmp_path / "docs"
    parent.mkdir()
    grants = [{"id": "g1", "path": str(parent), "access": "read"}]
    gate = _gate(temp_app_paths, grants)
    target = parent / "a.txt"
    # Read is allowed, write is not (FR-070).
    assert gate.authorize(target, Access.READ).kind == DecisionKind.ALLOW
    assert gate.authorize(target, Access.READ_WRITE).kind == DecisionKind.NEEDS_CONSENT


def test_traversal_escape_not_treated_as_workspace(temp_app_paths):
    gate = _gate(temp_app_paths)
    # ../ escapes the workspace -> must not be auto-allowed.
    target = temp_app_paths.workspace / ".." / ".." / "etc_passwd"
    decision = gate.authorize(target, Access.READ)
    assert decision.kind in (DecisionKind.NEEDS_CONSENT, DecisionKind.DENY)


def test_uncovered_path_prompts_interactive(temp_app_paths, tmp_path):
    gate = _gate(temp_app_paths)
    target = tmp_path / "elsewhere" / "x.txt"
    decision = gate.authorize(target, Access.READ)
    assert decision.kind == DecisionKind.NEEDS_CONSENT
    assert decision.request_id is not None


def test_unattended_fails_fast_without_grant(temp_app_paths, tmp_path):
    gate = _gate(temp_app_paths)
    target = tmp_path / "elsewhere" / "x.txt"
    decision = gate.authorize(target, Access.READ, unattended=True)
    assert decision.kind == DecisionKind.DENY


def test_relative_path_resolves_against_home(temp_app_paths):
    # The agent referring to "mcp.json" / "workspace/" hits its own home (allowed),
    # not the controller's working directory.
    gate = _gate(temp_app_paths)
    assert gate.authorize("mcp.json", Access.READ_WRITE).kind == DecisionKind.ALLOW
    d = gate.authorize("workspace/notes.txt", Access.READ_WRITE)
    assert d.kind == DecisionKind.ALLOW


def test_session_grant_applied_via_current_session(temp_app_paths, tmp_path):
    # A session-scoped grant must be honoured when the gate is bound to that session
    # but the caller (a file tool) does not pass session_id explicitly.
    parent = tmp_path / "proj"
    parent.mkdir()
    grants = [{"id": "g1", "path": str(parent), "access": "read_write",
               "scope": "session", "session_id": "S1"}]

    class _SessionStore:
        def active_grants(self, session_id=None):  # noqa: ANN001
            return [g for g in grants if g["session_id"] == session_id]

    gate = PathGate(temp_app_paths, _SessionStore())
    target = parent / "a.txt"
    # Without binding, the session grant is invisible → prompt.
    assert gate.authorize(target, Access.READ).kind == DecisionKind.NEEDS_CONSENT
    # Bound to the session → the grant applies.
    gate.current_session_id = "S1"
    assert gate.authorize(target, Access.READ_WRITE).kind == DecisionKind.ALLOW


@pytest.mark.skipif(
    not hasattr(Path, "symlink_to"), reason="symlink support required"
)
def test_symlink_escape_rejected(temp_app_paths, tmp_path):
    # A symlink inside the workspace pointing outside must resolve outside and
    # therefore not be auto-allowed by the workspace rule.
    outside = tmp_path / "outside"
    outside.mkdir()
    link = temp_app_paths.workspace / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted on this platform")
    gate = _gate(temp_app_paths)
    target = link / "secret.txt"
    assert gate.authorize(target, Access.READ).kind != DecisionKind.ALLOW
