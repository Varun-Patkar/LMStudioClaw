"""Path-authorization gate — the single chokepoint for all agent file access.

Every agent file operation MUST pass through :meth:`PathGate.authorize`. The gate:

* canonicalizes the target (resolving ``..`` and symlinks) to defeat traversal and
  symlink escapes (FR-024);
* always allows the workspace folder and everything beneath it (FR-020);
* allows any path that is hierarchically beneath an active grant (parent grant covers
  subfolders — FR-069);
* hard-denies the isolated secrets area and app internals regardless of grants
  (FR-077);
* for unattended automations, never blocks interactively — it fails fast when no
  permanent grant covers the path (FR-025), so a scheduled run can't hang waiting for
  a human.

Read-vs-write is least-privilege: a ``read`` grant does not authorize a write (FR-070).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from ..config.paths import AppPaths


class Access(str, Enum):
    """Access level requested or granted for a folder."""

    READ = "read"
    READ_WRITE = "read_write"


class DecisionKind(str, Enum):
    """Outcome of an authorization check."""

    ALLOW = "allow"
    NEEDS_CONSENT = "needs_consent"  # interactive: prompt the user
    DENY = "deny"                    # hard deny (deny-list) or fail-fast (unattended)


@dataclass
class Decision:
    """Result of :meth:`PathGate.authorize`."""

    kind: DecisionKind
    path: str                      # canonical path evaluated
    access: Access
    reason: str = ""
    request_id: str | None = None  # set when kind == NEEDS_CONSENT


def _canon(path: str | Path) -> Path:
    """Canonicalize a path, resolving symlinks and ``..`` (non-strict)."""
    try:
        return Path(path).resolve(strict=False)
    except OSError:
        return Path(path).absolute()


def _is_within(target: Path, parent: Path) -> bool:
    """Return True if ``target`` equals or is nested under ``parent`` (hierarchical)."""
    try:
        target.relative_to(parent)
        return True
    except ValueError:
        return False


class PathGate:
    """Authorizes agent file access against grants, the workspace, and a deny-list."""

    def __init__(self, paths: AppPaths, store) -> None:
        """Store the resolved app paths (for workspace + deny-list) and the grant store."""
        self._paths = paths
        self._store = store
        self._workspace = _canon(paths.workspace)
        # The whole Documents/LMStudioClaw area (skills, tools, memory, mcp.json, …) is
        # the agent's home and is implicitly allowed without prompting — the secrets
        # directory and app internals are NOT under it and stay on the deny-list, which
        # is always evaluated first below (FR-020/FR-077).
        self._base = _canon(paths.base)
        self._deny_list = tuple(_canon(p) for p in paths.deny_list)
        # The active session id, set by the runner before a run, so session-scoped
        # grants apply on a file tool's gate check (which doesn't pass session_id).
        # Only one session runs at a time (FR-008), so a single field is sufficient.
        self.current_session_id: str | None = None

    def _access_satisfies(self, granted: str, requested: Access) -> bool:
        """Least-privilege: a read grant cannot authorize a write (FR-070)."""
        if requested == Access.READ:
            return True
        return granted == Access.READ_WRITE.value

    def authorize(
        self,
        path: str | Path,
        access: Access = Access.READ,
        *,
        session_id: str | None = None,
        unattended: bool = False,
    ) -> Decision:
        """Authorize a file operation on ``path`` at the given ``access`` level.

        For interactive sessions an uncovered path yields ``NEEDS_CONSENT`` with a
        ``request_id``. For unattended automations the same case yields ``DENY``
        (fail-fast, FR-025).

        A **relative** ``path`` is resolved against the agent's home base (not the
        controller's working directory), so the agent referring to ``mcp.json`` or
        ``workspace/`` reaches its own home files (which are implicitly allowed)
        instead of an unrelated path that would prompt.
        """
        # Resolve relative inputs against the agent's home so they land in-home.
        raw = Path(path)
        target = _canon(raw if raw.is_absolute() else self._base / raw)

        # Default to the active session so session-scoped grants are honoured even when
        # the caller (a file tool) does not thread the id through.
        if session_id is None:
            session_id = self.current_session_id

        # 1. Hard deny-list: secrets area + app internals, regardless of grants.
        for denied in self._deny_list:
            if _is_within(target, denied):
                return Decision(
                    DecisionKind.DENY, str(target), access,
                    reason="Access to secrets/app-internal paths is never permitted.",
                )

        # 2. Workspace is always allowed (read or write) and covers subfolders.
        if _is_within(target, self._workspace):
            return Decision(DecisionKind.ALLOW, str(target), access, reason="workspace")

        # 2b. The agent's Documents home (Documents/LMStudioClaw) is implicitly allowed,
        # so config like mcp.json, skills, tools, and memory need no prompt (FR-020).
        if _is_within(target, self._base):
            return Decision(DecisionKind.ALLOW, str(target), access, reason="home")

        # 3. Hierarchical grant prefix match (parent grant covers subfolders).
        for grant in self._store.active_grants(session_id=session_id):
            grant_path = _canon(grant["path"])
            if _is_within(target, grant_path) and self._access_satisfies(grant["access"], access):
                return Decision(
                    DecisionKind.ALLOW, str(target), access, reason=f"grant:{grant['id']}"
                )

        # 4. Not covered. Fail fast for unattended runs; prompt otherwise.
        if unattended:
            return Decision(
                DecisionKind.DENY, str(target), access,
                reason="Unattended run has no permanent grant for this path.",
            )
        return Decision(
            DecisionKind.NEEDS_CONSENT, str(target), access,
            reason="Folder is outside the workspace and not covered by a grant.",
            request_id=str(uuid.uuid4()),
        )
