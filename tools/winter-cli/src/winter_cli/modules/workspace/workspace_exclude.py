from __future__ import annotations

from pathlib import Path
from typing import Protocol


class IWorkspaceExcludeLocator(Protocol):
    """Read-only resolver for the git exclude file that holds the workspace root's winter-managed blocks.

    A normal clone keeps the file at `<root>/.git/info/exclude`. A linked git
    worktree root (`git worktree add`) has a `.git` *file*, and the shared
    `info/exclude` of the common directory would be rewritten by every worktree
    of the repository. For that case the file is the worktree's own
    `<absolute-git-dir>/info/exclude`, made effective by a per-worktree
    `core.excludesFile` (see `IWorkspaceExcludeWriteLocator`).

    Callers that only read or strip the managed blocks (`ws destroy`, `ws prune`)
    depend on this seam alone: it touches no git configuration and no file.
    Raises `RepoError` on git failure — GitPython types are confined to the adapter.
    """

    def locate(self) -> Path | None:
        """The exclude file's path, or `None` when the workspace root is not a git repository.

        The returned file or its parent directory may not exist yet.
        """
        ...


class IWorkspaceExcludeWriteLocator(IWorkspaceExcludeLocator, Protocol):
    """`IWorkspaceExcludeLocator` plus the git setup that makes a written exclude file effective.

    The `ws init` writers (`InitService`, `ExtensionExcludeService`,
    `WorkspaceSkillService`) take this seam; nothing else may change git
    configuration.
    """

    def locate_for_write(self) -> Path | None:
        """Like `locate`, and ensure git will honor the file for this workspace root.

        For a normal clone this changes nothing. For a linked worktree it
        enables `extensions.worktreeConfig` (first moving `core.bare` and
        `core.worktree` out of the shared config, as git requires) and sets
        `core.excludesFile` to the file with `git config --worktree`, so the
        blocks apply to that worktree alone. Each setting is read first and
        written only when it differs, so the call is idempotent and parallel
        `ws init` runs in sibling worktrees do not contend for the shared
        config lock once it is set up. Callers still create the file's parent
        directory.
        """
        ...
