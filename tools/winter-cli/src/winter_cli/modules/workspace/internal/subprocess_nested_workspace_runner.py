"""`ISubprocessRunner` adapter for `INestedWorkspaceRunner`."""

from __future__ import annotations

from typing import TYPE_CHECKING

from winter_cli.modules.workspace.nested_workspace_runner import INestedWorkspaceRunner

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from pathlib import Path

    from winter_cli.core.subprocess_runner import ISubprocessRunner
    from winter_cli.modules.workspace.internal.repo_error_factory import RepoErrorFactory

_STATUS_ARGS = ("ws", "status", "--json")
_STATUS_EXIT_CODES = (0, 1)
"""`ws status` exits 0 when clean and 1 when anything is dirty; both carry a full JSON document."""


class SubprocessNestedWorkspaceRunner:
    """Runs a nested workspace's `winter` CLI through `ISubprocessRunner`.

    *command* is the argv prefix that starts winter — `("winter",)` from
    `PATH` in production, so the shim resolves the nested root's own CLI.
    Going through `ISubprocessRunner` keeps trace propagation and
    non-interactive mode the same as for every other child.
    """

    def __init__(
        self,
        subprocess_runner: ISubprocessRunner,
        error_factory: RepoErrorFactory,
        command: tuple[str, ...] = ("winter",),
    ) -> None:
        self._subprocess = subprocess_runner
        self._errors = error_factory
        self._command = command

    def status_json(self, root: Path, env: Mapping[str, str]) -> str:
        cmd = [*self._command, *_STATUS_ARGS]
        result = self._subprocess.run(cmd, cwd=root, env=env)
        if result.returncode not in _STATUS_EXIT_CODES:
            raise self._errors.from_result(result, f"nested workspace status failed at {root}", cmd=cmd, cwd=root)
        return result.stdout

    def run(
        self,
        root: Path,
        args: Sequence[str],
        env: Mapping[str, str],
        on_line: Callable[[str], None],
    ) -> int:
        cmd = [*self._command, *args]
        try:
            with self._subprocess.popen(cmd, cwd=root, env=env) as proc:
                for line in proc.stdout_lines:
                    on_line(line)
                return proc.wait()
        except OSError as exc:
            raise self._errors.from_exception(exc, f"`{' '.join(cmd)}` could not start in {root}", cwd=root) from exc


def _conforms_subprocess_nested_workspace_runner(x: SubprocessNestedWorkspaceRunner) -> INestedWorkspaceRunner:
    return x
