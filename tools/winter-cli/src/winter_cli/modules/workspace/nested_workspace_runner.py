"""The seam `NestedWorkspaceService` depends on to start a nested workspace's own winter CLI."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from pathlib import Path


class INestedWorkspaceRunner(Protocol):
    """Starts `winter` inside a nested workspace root — raw process I/O, no policy.

    Every call runs with its working directory at *root* and exactly the
    environment *env* it is given. Whether a call may run at all, and whether
    the `winter` it starts resolved the nested workspace rather than some other
    one, is `NestedWorkspaceService`'s to decide before it calls.
    """

    def status_json(self, root: Path, env: Mapping[str, str]) -> str:
        """Run `winter ws status --json` in *root* and return its stdout.

        Raises `RepoError` when the process cannot start or exits with anything
        but 0 (clean) or 1 (dirty).
        """
        ...

    def run(
        self,
        root: Path,
        args: Sequence[str],
        env: Mapping[str, str],
        on_line: Callable[[str], None],
    ) -> int:
        """Run `winter <args>` in *root*, streaming each output line to *on_line*; return its exit code.

        Raises `RepoError` when the process cannot start.
        """
        ...
