from __future__ import annotations

from pathlib import Path
from typing import Protocol

from winter_cli.modules.workspace.models.domain_model import RefKind


class IGitRepository(Protocol):
    """Service-level seam for the imperative git operations that init/destroy/prune perform.

    Distinct from `IReadRepoRepository` / `IWriteRepoRepository` (which model
    *winter's domain*: feature worktrees, syncing, etc.) — this Protocol
    exposes raw git verbs the lifecycle services need. The split keeps each
    surface focused: domain repositories speak in `FeatureWorktree` /
    `StandaloneRepository`, this one speaks in paths.

    Every method raises `RepoError` on git failure — GitPython types are
    confined to the adapter under `internal/`.
    """

    # ── Cloning + worktrees ───────────────────────────────────────────────

    def clone(self, url: str, dest: Path) -> None: ...

    def add_worktree(
        self,
        source: Path,
        worktree_path: Path,
        branch: str,
        base_branch: str | None = None,
    ) -> None:
        """Create a new git worktree at `worktree_path`.

        When `base_branch` is None the branch is assumed to already exist
        locally and is attached; when supplied, a new branch is created from
        `base_branch`. Matches the `git worktree add [-b <branch> <base>]`
        forms.
        """
        ...

    def remove_worktree(self, source: Path, worktree_path: Path, force: bool) -> None: ...

    def list_worktrees(self, source: Path) -> list[Path]: ...

    # ── Branches + tracking ──────────────────────────────────────────────

    def get_local_branches(self, path: Path) -> list[str]: ...

    def get_tracking_branch(self, path: Path) -> str | None:
        """Return the current branch's tracking ref (e.g. `origin/main`), or None if unset / detached."""
        ...

    def set_upstream_to(self, path: Path, ref: str) -> None: ...

    def set_push_default_upstream(self, path: Path) -> None:
        """Set `push.default=upstream` so `git push` from the worktree branch targets its tracking branch."""
        ...

    # ── Repository-scope config ──────────────────────────────────────────

    def set_user_identity(self, path: Path, name: str, email: str) -> None: ...

    def get_push_default(self, path: Path) -> str | None: ...

    # ── Status probes ────────────────────────────────────────────────────

    def is_worktree_clean(self, path: Path) -> bool:
        """True if the worktree has no uncommitted/untracked changes.

        Returns False on any git failure — callers use this for safety
        checks (destroy, prune) where "I don't know" must be treated as
        "do not touch".
        """
        ...

    # ── Ref resolution + checkout ─────────────────────────────────────────

    def resolve_ref(self, path: Path, ref: str) -> tuple[RefKind, str]:
        """Classify `ref` against on-disk refs and return its kind + full 40-char SHA.

        Resolution order (first match wins):
          1. ``refs/remotes/origin/<ref>`` → ``RefKind.branch``
          2. ``refs/tags/<ref>``           → ``RefKind.tag``
          3. ``<ref>^{commit}``            → ``RefKind.commit`` (raw SHA or abbrev)

        Uses ``git rev-parse --verify`` per candidate — no network access.
        Raises ``RepoError`` if none of the candidates resolve, naming `path` and `ref`.
        """
        ...

    def checkout_detached(self, path: Path, commit: str) -> None:
        """Check out `commit` in detached-HEAD mode (equivalent to ``git checkout --detach <commit>``)."""
        ...

    def checkout_branch(self, path: Path, branch: str) -> None:
        """Land the working tree on the local branch tracking ``origin/<branch>``.

        Creates the local branch and sets its upstream if it does not yet exist.
        When the branch already exists, ``-B`` FORCE-RESETS the local branch pointer
        to ``origin/<branch>`` — this is NOT a fast-forward and CAN silently discard
        local commits. Callers that want ff-only safety must check for divergence
        themselves before calling this, or use ``integrate_standalone_to_ref``
        instead (which wraps ``git merge --ff-only``).

        Intended for: init's fresh-clone checkout of a branch pin (always clean),
        and ``update_pins`` explicit re-pin where the caller has already applied
        the dirty-tree guard and the intent is to force-land on the remote tip.
        NOT for branch-pin advances on pull, which use ``integrate_standalone_to_ref``.
        """
        ...

    def get_head_commit(self, path: Path) -> str:
        """Return the full 40-character SHA of HEAD."""
        ...

    def stash_push(self, path: Path) -> None:
        """Stash the working tree at `path` (equivalent to ``git stash push``)."""
        ...

    def stash_pop(self, path: Path) -> None:
        """Pop the most recent stash at `path` (equivalent to ``git stash pop``).

        Best-effort: called after checkout to restore a dirty tree stashed by
        `stash_push`. If the pop fails (e.g. conflict or empty stash), it
        raises `RepoError`.
        """
        ...
