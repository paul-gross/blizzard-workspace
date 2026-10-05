from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from winter_cli.config.workspace import CONFIG_FILE, LOCAL_CONFIG_FILE, WINTER_DIR
from winter_cli.core.config_file import ConfigFileReadError
from winter_cli.modules.workspace.models import RepoError
from winter_cli.modules.workspace.nested_env import nested_chain, nested_child_env
from winter_cli.modules.workspace.nested_status import NestedStatusShapeError, parse_state, reported_root
from winter_cli.util import deep_merge

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from winter_cli.core.config_file import IConfigFileReader
    from winter_cli.core.filesystem import IFilesystemReader
    from winter_cli.modules.workspace.init_reporter import IInitReporter
    from winter_cli.modules.workspace.models import NestedWorkspaceState, ProjectRepository
    from winter_cli.modules.workspace.nested_workspace_runner import INestedWorkspaceRunner

INIT_COMMAND = "winter ws init"
SERVICE_DOWN_COMMAND = "winter service down workspace"
_STATUS_ARGS = ("ws", "status", "--json")


class NestedWorkspaceService:
    """Drives the nested workspace inside one env's worktree of a `nested = true` repo, reporting as that repo.

    Initializes it, reads which feature envs it holds and what work in it
    exists nowhere else, destroys its envs, and stops its workspace-scope
    services. The nested workspace's own CLI does the work; its output streams
    through the caller's reporter under the outer repo's name.

    Every nested call is guarded here, before the runner starts anything:

    - **Chain.** A process whose own *workspace_root* is already on
      `WINTER_NESTED_CHAIN` was started by a nested call yet resolved an
      enclosing workspace, so it refuses every nested call; so does a call
      into a root already on the chain. Each child's chain is this process's
      chain plus *workspace_root*, so a mis-resolved child stops at once
      instead of fanning out to every sibling nested worktree.
    - **Preflight.** The nested root must hold `.winter/config.toml`.
    - **Verify before mutate.** The nested `ws status --json` must be a
      schema-v1 document whose `workspace.root_path` is the nested root;
      otherwise the `winter` there resolved some other workspace — the outer
      one, say — which must never be mutated through this service. A root is
      verified once per process, by its first status read, and every later
      call into it reuses that verification.

    Every child runs with `nested_child_env`: this process's environment
    scrubbed of the outer workspace's variables.
    """

    def __init__(
        self,
        runner: INestedWorkspaceRunner,
        fs: IFilesystemReader,
        workspace_root: Path,
        environ: Mapping[str, str],
        config_file_reader: IConfigFileReader,
    ) -> None:
        self._runner = runner
        self._fs = fs
        self._workspace_root = workspace_root
        self._environ = environ
        self._config_file_reader = config_file_reader
        self._verified: set[Path] = set()

    def reconcile(self, repo: ProjectRepository, root: Path, reporter: IInitReporter) -> None:
        """Verify the nested *root*, then run `winter ws init` there; raise `RepoError` when either fails.

        A bare `ws init` reconciles the nested workspace and clones its
        projects but creates no nested env, so init never recurses further.
        """
        self._ensure_verified(root)
        reporter.cmd_started(repo.name, INIT_COMMAND)
        returncode = self._run(root, ["ws", "init"], lambda line: reporter.cmd_output_line(repo.name, line))
        reporter.cmd_completed(repo.name, INIT_COMMAND, returncode)
        if returncode != 0:
            raise RepoError(f"nested `{INIT_COMMAND}` exited with code {returncode}", cwd=str(root))

    def state(self, root: Path) -> NestedWorkspaceState:
        """What the nested workspace at *root* holds, read from its `ws status --json`.

        Raises `RepoError` when it cannot be read. The read also verifies
        *root* for every later call in this process.
        """
        self._preflight(root)
        state = self._read_verified(root)
        self._verified.add(root.resolve())
        return state

    def destroy_envs(
        self,
        repo: ProjectRepository,
        root: Path,
        state: NestedWorkspaceState,
        *,
        force: bool,
        strict: bool,
        provision_teardown: bool,
        reporter: IInitReporter,
    ) -> bool:
        """Destroy every env in *state*, one nested `winter ws destroy` per env; return whether all succeeded.

        Every env is attempted even after one fails, so a single stuck nested
        env leaves no sibling running. A *root* `state` already read is not
        verified again. Each success reports
        `nested_env_destroyed`; each failure is reported as a repo error.
        """
        success = True
        for env in state.envs:
            command = f"winter ws destroy {env.name}"
            reporter.cmd_started(repo.name, command)
            args = ["ws", "destroy", env.name]
            if force:
                args.append("--force")
            if strict:
                args.append("--strict")
            if not provision_teardown:
                args.append("--no-provision-teardown")
            try:
                self._ensure_verified(root)
                returncode = self._run(root, args, lambda line: reporter.cmd_output_line(repo.name, line))
            except RepoError as exc:
                reporter.repo_error(repo.name, f"nested env {env.name} — {exc}")
                success = False
                continue
            reporter.cmd_completed(repo.name, command, returncode)
            if returncode != 0:
                reporter.repo_error(repo.name, f"nested `{command}` exited with code {returncode}")
                success = False
                continue
            reporter.repo_action(repo.name, str(root / env.name), "nested_env_destroyed", env.name)
        return success

    def binds_service(self, root: Path) -> bool:
        """Whether the nested workspace at *root* binds a `service` capability in its effective config.

        Reads its committed `config.toml` and its `config.local.toml` layered as
        winter layers them. Raises `RepoError` when either is not valid TOML.
        """
        try:
            committed = self._read_config(root / WINTER_DIR / CONFIG_FILE)
            local = self._read_config(root / WINTER_DIR / LOCAL_CONFIG_FILE)
        except ConfigFileReadError as exc:
            raise RepoError(f"cannot read the nested workspace config at {root}: {exc}", cwd=str(root)) from exc
        return _binds_service(deep_merge(committed, local))

    def stop_workspace_services(self, repo: ProjectRepository, root: Path, *, reporter: IInitReporter) -> bool:
        """Run `winter service down workspace` in *root* when it binds a service provider; return whether it succeeded.

        A nested env's destroy stops only that env's services; the nested
        workspace's own workspace-scope services would outlive the worktree
        that holds them. Nothing runs when no provider is bound, since there
        `service down` itself fails. A *root* `state` already read is not
        verified again. Success reports `nested_workspace_services_stopped`; a
        failure is reported as a repo error.
        """
        try:
            if not self.binds_service(root):
                return True
            reporter.cmd_started(repo.name, SERVICE_DOWN_COMMAND)
            self._ensure_verified(root)
            returncode = self._run(
                root, ["service", "down", "workspace"], lambda line: reporter.cmd_output_line(repo.name, line)
            )
        except RepoError as exc:
            reporter.repo_error(repo.name, f"nested workspace services — {exc}")
            return False
        reporter.cmd_completed(repo.name, SERVICE_DOWN_COMMAND, returncode)
        if returncode != 0:
            reporter.repo_error(repo.name, f"nested `{SERVICE_DOWN_COMMAND}` exited with code {returncode}")
            return False
        reporter.repo_action(repo.name, str(root), "nested_workspace_services_stopped")
        return True

    def _read_config(self, path: Path) -> dict[str, Any]:
        return self._config_file_reader.load(path) if self._fs.is_file(path) else {}

    def _ensure_verified(self, root: Path) -> None:
        """Refuse a guarded *root*, and verify it unless an earlier call in this process already did."""
        self._preflight(root)
        if root.resolve() not in self._verified:
            self._read_verified(root)
            self._verified.add(root.resolve())

    def _preflight(self, root: Path) -> None:
        """Refuse a nested call into *root* before anything runs: the chain checks and the config check."""
        chain = nested_chain(self._environ)
        own = str(self._workspace_root.resolve())
        if own in chain:
            raise RepoError(
                f"this winter resolved workspace {own}, which an enclosing nested call already runs in; "
                f"refusing to run the nested workspace at {root} from it",
                cwd=str(root),
            )
        if str(root.resolve()) in chain:
            raise RepoError(
                f"nested workspace at {root} is already being run by an enclosing winter call; refusing to re-enter it",
                cwd=str(root),
            )
        if not self._fs.is_file(root / WINTER_DIR / CONFIG_FILE):
            raise RepoError(
                f"nested workspace at {root} has no {WINTER_DIR}/{CONFIG_FILE}; refusing to run winter there",
                cwd=str(root),
            )

    def _read_verified(self, root: Path) -> NestedWorkspaceState:
        """Run the nested `ws status --json` and parse it, after confirming it describes *root*."""
        stdout = self._runner.status_json(root, self._child_env())
        try:
            doc = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise self._status_error(root, f"nested workspace status at {root} is not JSON — {exc}") from exc
        try:
            reported = reported_root(doc)
        except NestedStatusShapeError as exc:
            raise self._status_error(root, f"nested workspace status at {root} {exc}") from exc
        if reported is None or Path(reported).resolve() != root.resolve():
            raise self._status_error(
                root, f"winter in {root} resolved workspace {reported!r}, not the nested workspace; refusing to run it"
            )
        try:
            return parse_state(doc)
        except NestedStatusShapeError as exc:
            raise self._status_error(root, f"nested workspace status at {root} is malformed: {exc}") from exc

    def _run(self, root: Path, args: Sequence[str], on_line: Callable[[str], None]) -> int:
        return self._runner.run(root, args, self._child_env(), on_line)

    def _child_env(self) -> dict[str, str]:
        return nested_child_env(self._environ, self._workspace_root)

    @staticmethod
    def _status_error(root: Path, message: str) -> RepoError:
        return RepoError(message, program="winter", subcommand="ws", cmd_args=_STATUS_ARGS[1:], cwd=str(root))


def _binds_service(config: Mapping[str, Any]) -> bool:
    """Whether *config*'s `[capabilities] service` names a provider — a non-empty string, or a list holding one."""
    capabilities = config.get("capabilities")
    if not isinstance(capabilities, dict):
        return False
    service = capabilities.get("service")
    names = service if isinstance(service, list) else [service]
    return any(isinstance(name, str) and name for name in names)
