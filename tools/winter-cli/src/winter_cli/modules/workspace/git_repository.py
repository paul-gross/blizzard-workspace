from __future__ import annotations

from pathlib import Path
from typing import Protocol


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
